"""Immutable automated-benchmark generation and execution audit repository."""

from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.corpus_steward.adjudication_schemas import (
    BenchmarkAccessAction,
    BenchmarkAccessPolicy,
)
from app.corpus_steward.benchmark import derive_development_suite_for_candidate
from app.corpus_steward.benchmark_schemas import (
    BenchmarkReport,
    BenchmarkSuite,
    BenchmarkSuitePartition,
)
from app.corpus_steward.candidate_schemas import RetrievalCandidateManifest
from app.corpus_steward.schemas import ArtifactKind
from app.corpus_steward.source_derived_benchmark import (
    AutomatedBenchmarkGenerationPolicy,
    AutomatedBenchmarkGenerationRecord,
    AutomatedBenchmarkGenerationResult,
)
from app.corpus_steward.storage import ImmutableStewardArtifactStore, StoredStewardArtifact
from app.persistence.database import Database
from app.persistence.models import (
    AutomatedBenchmarkSuiteBuildRow,
    BenchmarkAccessEventRow,
    BenchmarkDevelopmentSuiteDerivationRow,
    BenchmarkExecutionRunRow,
    BenchmarkGenerationPolicyRow,
    BenchmarkGenerationRecordRow,
    BenchmarkPolicyArtifactRow,
    StewardArtifactRow,
)
from app.schemas.corpus import canonical_json_bytes
from app.schemas.domain import CanonicalModel, utc_now


class SourceDerivedBenchmarkRepositoryError(RuntimeError):
    pass


class SourceDerivedBenchmarkConflictError(SourceDerivedBenchmarkRepositoryError):
    pass


class SourceDerivedBenchmarkAccessError(SourceDerivedBenchmarkRepositoryError):
    pass


class SQLSourceDerivedBenchmarkRepository:
    def __init__(
        self,
        database: Database,
        artifacts: ImmutableStewardArtifactStore,
    ) -> None:
        self._database = database
        self._artifacts = artifacts

    async def register_generation(
        self,
        result: AutomatedBenchmarkGenerationResult,
        *,
        policy: AutomatedBenchmarkGenerationPolicy,
    ) -> None:
        policy_artifact = self._put(
            policy,
            kind=ArtifactKind.BENCHMARK_GENERATION_POLICY,
            media_type="application/vnd.med-rag.benchmark-generation-policy+json",
        )
        record = result.generation_record
        record_artifact = self._put(
            record,
            kind=ArtifactKind.BENCHMARK_GENERATION_RECORD,
            media_type="application/vnd.med-rag.benchmark-generation-record+json",
        )
        suites = (
            (
                result.development_suite,
                self._put(
                    result.development_suite,
                    kind=ArtifactKind.BENCHMARK_SUITE,
                    media_type="application/vnd.med-rag.retrieval-benchmark-suite+json",
                ),
            ),
            (
                result.sealed_holdout_suite,
                self._put(
                    result.sealed_holdout_suite,
                    kind=ArtifactKind.BENCHMARK_SUITE,
                    media_type="application/vnd.med-rag.retrieval-benchmark-suite+json",
                ),
            ),
        )
        self._verify_artifact(policy_artifact, policy)
        self._verify_artifact(record_artifact, record)
        for suite, artifact in suites:
            self._verify_artifact(artifact, suite)
        content = record.content
        development_count = sum(
            item.suite_partition is BenchmarkSuitePartition.DEVELOPMENT for item in content.cases
        )
        holdout_count = len(content.cases) - development_count
        try:
            async with self._database.session() as session:
                await self._require_registered_policy(
                    session, content.access_policy_sha256, expected_kind="ACCESS"
                )
                await self._require_registered_policy(
                    session, content.threshold_policy_sha256, expected_kind="THRESHOLD"
                )
                existing_policy = await session.get(
                    BenchmarkGenerationPolicyRow, policy.policy_sha256
                )
                if existing_policy is None:
                    same_revision = await session.scalar(
                        select(BenchmarkGenerationPolicyRow).where(
                            BenchmarkGenerationPolicyRow.policy_id == policy.content.policy_id,
                            BenchmarkGenerationPolicyRow.revision == policy.content.revision,
                        )
                    )
                    if same_revision is not None:
                        raise SourceDerivedBenchmarkConflictError(
                            "generation policy revision already identifies different content"
                        )
                    await self._record_artifact(session, policy_artifact, now=content.generated_at)
                    session.add(
                        BenchmarkGenerationPolicyRow(
                            policy_sha256=policy.policy_sha256,
                            policy_id=policy.content.policy_id,
                            revision=policy.content.revision,
                            payload=policy.model_dump(mode="json"),
                            artifact_sha256=policy_artifact.sha256,
                            sealed_at=content.generated_at,
                        )
                    )
                elif existing_policy.payload != policy.model_dump(mode="json"):
                    raise SourceDerivedBenchmarkConflictError(
                        "generation policy digest identifies different stored content"
                    )

                existing_record = await session.get(
                    BenchmarkGenerationRecordRow, record.generation_record_sha256
                )
                if existing_record is None:
                    same_id = await session.scalar(
                        select(BenchmarkGenerationRecordRow).where(
                            BenchmarkGenerationRecordRow.generation_id == content.generation_id
                        )
                    )
                    if same_id is not None:
                        raise SourceDerivedBenchmarkConflictError(
                            "generation ID already identifies different content"
                        )
                    await self._record_artifact(session, record_artifact, now=content.generated_at)
                    session.add(
                        BenchmarkGenerationRecordRow(
                            generation_record_sha256=record.generation_record_sha256,
                            generation_id=content.generation_id,
                            generation_policy_sha256=content.generation_policy_sha256,
                            access_policy_sha256=content.access_policy_sha256,
                            threshold_policy_sha256=content.threshold_policy_sha256,
                            corpus_release_id=content.corpus_release_id,
                            manifest_sha256=content.manifest_sha256,
                            candidate_configuration_sha256=None,
                            partition_assignment_sha256=content.partition_assignment_sha256,
                            development_case_count=development_count,
                            sealed_holdout_case_count=holdout_count,
                            payload=record.model_dump(mode="json"),
                            artifact_sha256=record_artifact.sha256,
                            generated_by=content.generated_by,
                            generated_at=content.generated_at,
                        )
                    )
                elif existing_record.payload != record.model_dump(mode="json"):
                    raise SourceDerivedBenchmarkConflictError(
                        "generation record digest identifies different stored content"
                    )

                for suite, artifact in suites:
                    await self._register_suite(
                        session,
                        suite,
                        artifact,
                        record=record,
                    )
        except IntegrityError as error:
            raise SourceDerivedBenchmarkConflictError(
                "automated benchmark generation registry conflict"
            ) from error

    async def register_development_derivation(
        self,
        parent_suite: BenchmarkSuite,
        derived_suite: BenchmarkSuite,
        candidate: RetrievalCandidateManifest,
        *,
        actor_identity: str,
        derived_at: datetime | None = None,
    ) -> None:
        """Register a candidate-bound retrieval-depth variant of a development suite."""

        if parent_suite.content.suite_partition is not BenchmarkSuitePartition.DEVELOPMENT:
            raise SourceDerivedBenchmarkRepositoryError(
                "development suite derivation cannot use a holdout parent"
            )
        expected = derive_development_suite_for_candidate(parent_suite, candidate)
        if expected != derived_suite:
            raise SourceDerivedBenchmarkRepositoryError(
                "derived development suite does not match its parent and candidate"
            )
        artifact = self._put(
            derived_suite,
            kind=ArtifactKind.BENCHMARK_SUITE,
            media_type="application/vnd.med-rag.retrieval-benchmark-suite+json",
        )
        self._verify_artifact(artifact, derived_suite)
        now = derived_at or utc_now()
        try:
            async with self._database.session() as session:
                parent = await session.get(
                    AutomatedBenchmarkSuiteBuildRow, parent_suite.suite_sha256
                )
                if parent is None or parent.payload != parent_suite.model_dump(mode="json"):
                    raise SourceDerivedBenchmarkRepositoryError(
                        "development suite parent is not the registered immutable automated build"
                    )
                policy_row = await self._require_registered_policy(
                    session,
                    parent_suite.content.access_policy_sha256,
                    expected_kind="ACCESS",
                )
                access = BenchmarkAccessPolicy.model_validate(policy_row.payload)
                if not access.content.permits(
                    actor_identity, BenchmarkSuitePartition.DEVELOPMENT
                ):
                    raise SourceDerivedBenchmarkAccessError(
                        f"{actor_identity} may not derive DEVELOPMENT"
                    )
                existing = await session.get(
                    BenchmarkDevelopmentSuiteDerivationRow,
                    derived_suite.suite_sha256,
                )
                payload = derived_suite.model_dump(mode="json")
                if existing is not None:
                    if (
                        existing.payload != payload
                        or existing.parent_suite_sha256 != parent_suite.suite_sha256
                        or existing.candidate_configuration_sha256
                        != candidate.candidate_sha256
                    ):
                        raise SourceDerivedBenchmarkConflictError(
                            "development suite derivation digest identifies different lineage"
                        )
                    return
                await self._record_artifact(session, artifact, now=now)
                session.add(
                    BenchmarkDevelopmentSuiteDerivationRow(
                        suite_sha256=derived_suite.suite_sha256,
                        parent_suite_sha256=parent_suite.suite_sha256,
                        candidate_configuration_sha256=candidate.candidate_sha256,
                        access_policy_sha256=parent_suite.content.access_policy_sha256,
                        corpus_release_id=parent_suite.content.corpus_release_id,
                        manifest_sha256=parent_suite.content.manifest_sha256,
                        case_count=len(derived_suite.content.cases),
                        payload=payload,
                        artifact_sha256=artifact.sha256,
                        derived_by=actor_identity,
                        derived_at=now,
                    )
                )
                action = BenchmarkAccessAction.BUILD_DEVELOPMENT_SUITE
                event_id = "BAE_" + hashlib.sha256(
                    (
                        f"{parent_suite.content.access_policy_sha256}:{actor_identity}:"
                        f"{action.value}:{derived_suite.suite_sha256}"
                    ).encode()
                ).hexdigest()[:32]
                session.add(
                    BenchmarkAccessEventRow(
                        access_event_id=event_id,
                        access_policy_sha256=parent_suite.content.access_policy_sha256,
                        actor_identity=actor_identity,
                        action=action.value,
                        resource_sha256=derived_suite.suite_sha256,
                        occurred_at=now,
                    )
                )
        except IntegrityError as error:
            raise SourceDerivedBenchmarkConflictError(
                "development suite derivation registry conflict"
            ) from error

    async def claim_execution(
        self,
        suite: BenchmarkSuite,
        *,
        candidate_configuration_sha256: str,
        vector_batch_sha256: str,
        actor_identity: str,
        started_at: datetime | None = None,
    ) -> str:
        started = started_at or utc_now()
        run_id = f"BRUN_{uuid4().hex}"
        try:
            async with self._database.session() as session:
                if suite.content.suite_partition is not BenchmarkSuitePartition.SYNTHETIC:
                    stored = await session.get(
                        AutomatedBenchmarkSuiteBuildRow, suite.suite_sha256
                    )
                    derivation = None
                    if stored is None:
                        derivation = await session.get(
                            BenchmarkDevelopmentSuiteDerivationRow,
                            suite.suite_sha256,
                        )
                        stored = derivation
                    if stored is None or stored.payload != suite.model_dump(mode="json"):
                        raise SourceDerivedBenchmarkRepositoryError(
                            "benchmark suite is not the registered immutable automated build"
                        )
                    if (
                        derivation is not None
                        and derivation.candidate_configuration_sha256
                        != candidate_configuration_sha256
                    ):
                        raise SourceDerivedBenchmarkRepositoryError(
                            "derived development suite belongs to a different candidate"
                        )
                    policy_row = await self._require_registered_policy(
                        session,
                        suite.content.access_policy_sha256,
                        expected_kind="ACCESS",
                    )
                    access = BenchmarkAccessPolicy.model_validate(policy_row.payload)
                    if not access.content.permits(actor_identity, suite.content.suite_partition):
                        raise SourceDerivedBenchmarkAccessError(
                            f"{actor_identity} may not execute "
                            f"{suite.content.suite_partition.value}"
                        )
                session.add(
                    BenchmarkExecutionRunRow(
                        run_id=run_id,
                        suite_sha256=suite.suite_sha256,
                        suite_partition=suite.content.suite_partition.value,
                        candidate_configuration_sha256=candidate_configuration_sha256,
                        vector_batch_sha256=vector_batch_sha256,
                        actor_identity=actor_identity,
                        status="STARTED",
                        report_sha256=None,
                        report_payload=None,
                        failure_class=None,
                        started_at=started,
                        completed_at=None,
                    )
                )
        except IntegrityError as error:
            if suite.content.suite_partition is BenchmarkSuitePartition.SEALED_HOLDOUT:
                raise SourceDerivedBenchmarkConflictError(
                    "sealed holdout execution has already been consumed"
                ) from error
            raise SourceDerivedBenchmarkConflictError("benchmark run registry conflict") from error
        return run_id

    async def claim_registered_execution(
        self,
        suite_sha256: str,
        *,
        candidate_configuration_sha256: str,
        vector_batch_sha256: str,
        actor_identity: str,
        started_at: datetime | None = None,
    ) -> tuple[BenchmarkSuite, str]:
        """Open an automated suite and claim its run in one transaction."""

        started = started_at or utc_now()
        run_id = f"BRUN_{uuid4().hex}"
        try:
            async with self._database.session() as session:
                stored = await session.get(AutomatedBenchmarkSuiteBuildRow, suite_sha256)
                derivation = None
                if stored is None:
                    derivation = await session.get(
                        BenchmarkDevelopmentSuiteDerivationRow, suite_sha256
                    )
                    stored = derivation
                if stored is None:
                    raise SourceDerivedBenchmarkRepositoryError(
                        "registered automated benchmark suite was not found"
                    )
                suite = BenchmarkSuite.model_validate(stored.payload)
                if suite.suite_sha256 != suite_sha256:
                    raise SourceDerivedBenchmarkRepositoryError(
                        "registered automated benchmark suite is inconsistent"
                    )
                if (
                    derivation is not None
                    and derivation.candidate_configuration_sha256
                    != candidate_configuration_sha256
                ):
                    raise SourceDerivedBenchmarkRepositoryError(
                        "derived development suite belongs to a different candidate"
                    )
                policy_row = await self._require_registered_policy(
                    session,
                    suite.content.access_policy_sha256,
                    expected_kind="ACCESS",
                )
                access = BenchmarkAccessPolicy.model_validate(policy_row.payload)
                if not access.content.permits(
                    actor_identity, suite.content.suite_partition
                ):
                    raise SourceDerivedBenchmarkAccessError(
                        f"{actor_identity} may not execute "
                        f"{suite.content.suite_partition.value}"
                    )
                session.add(
                    BenchmarkExecutionRunRow(
                        run_id=run_id,
                        suite_sha256=suite.suite_sha256,
                        suite_partition=suite.content.suite_partition.value,
                        candidate_configuration_sha256=(
                            candidate_configuration_sha256
                        ),
                        vector_batch_sha256=vector_batch_sha256,
                        actor_identity=actor_identity,
                        status="STARTED",
                        report_sha256=None,
                        report_payload=None,
                        failure_class=None,
                        started_at=started,
                        completed_at=None,
                    )
                )
        except IntegrityError as error:
            raise SourceDerivedBenchmarkConflictError(
                "sealed holdout execution has already been consumed"
            ) from error
        return suite, run_id

    async def complete_execution(
        self, run_id: str, report: BenchmarkReport, *, completed_at: datetime | None = None
    ) -> None:
        async with self._database.session() as session:
            row = await session.get(BenchmarkExecutionRunRow, run_id)
            if row is None or row.status != "STARTED":
                raise SourceDerivedBenchmarkConflictError("benchmark run is not open")
            if row.suite_sha256 != report.content.benchmark_suite_sha256:
                raise SourceDerivedBenchmarkRepositoryError(
                    "benchmark report does not belong to its claimed run"
                )
            row.status = "COMPLETED"
            row.report_sha256 = report.report_sha256
            row.report_payload = report.model_dump(mode="json")
            row.completed_at = completed_at or utc_now()

    async def fail_execution(
        self, run_id: str, *, failure_class: str, completed_at: datetime | None = None
    ) -> None:
        async with self._database.session() as session:
            row = await session.get(BenchmarkExecutionRunRow, run_id)
            if row is None or row.status != "STARTED":
                raise SourceDerivedBenchmarkConflictError("benchmark run is not open")
            row.status = "FAILED"
            row.failure_class = failure_class[:200]
            row.completed_at = completed_at or utc_now()

    async def _register_suite(
        self,
        session,
        suite: BenchmarkSuite,
        artifact: StoredStewardArtifact,
        *,
        record: AutomatedBenchmarkGenerationRecord,
    ) -> None:
        content = suite.content
        existing = await session.get(AutomatedBenchmarkSuiteBuildRow, suite.suite_sha256)
        if existing is not None:
            if existing.payload != suite.model_dump(mode="json"):
                raise SourceDerivedBenchmarkConflictError(
                    "automated suite digest identifies different stored content"
                )
            return
        await self._record_artifact(session, artifact, now=record.content.generated_at)
        session.add(
            AutomatedBenchmarkSuiteBuildRow(
                suite_sha256=suite.suite_sha256,
                benchmark_id=content.benchmark_id,
                suite_partition=content.suite_partition.value,
                generation_record_sha256=record.generation_record_sha256,
                generation_policy_sha256=record.content.generation_policy_sha256,
                access_policy_sha256=record.content.access_policy_sha256,
                threshold_policy_sha256=record.content.threshold_policy_sha256,
                candidate_configuration_sha256=None,
                corpus_release_id=content.corpus_release_id,
                manifest_sha256=content.manifest_sha256,
                case_count=len(content.cases),
                payload=suite.model_dump(mode="json"),
                artifact_sha256=artifact.sha256,
                built_by=record.content.generated_by,
                built_at=record.content.generated_at,
            )
        )
        action = (
            BenchmarkAccessAction.BUILD_DEVELOPMENT_SUITE
            if content.suite_partition is BenchmarkSuitePartition.DEVELOPMENT
            else BenchmarkAccessAction.BUILD_SEALED_HOLDOUT_SUITE
        )
        event_id = "BAE_" + hashlib.sha256(
            (
                f"{content.access_policy_sha256}:{record.content.generated_by}:"
                f"{action.value}:{suite.suite_sha256}"
            ).encode()
        ).hexdigest()[:32]
        session.add(
            BenchmarkAccessEventRow(
                access_event_id=event_id,
                access_policy_sha256=content.access_policy_sha256,
                actor_identity=record.content.generated_by,
                action=action.value,
                resource_sha256=suite.suite_sha256,
                occurred_at=record.content.generated_at,
            )
        )

    @staticmethod
    async def _require_registered_policy(session, digest: str, *, expected_kind: str):
        row = await session.get(BenchmarkPolicyArtifactRow, digest)
        if row is None or row.kind != expected_kind:
            raise SourceDerivedBenchmarkRepositoryError(
                f"registered {expected_kind.lower()} policy not found: {digest}"
            )
        return row

    async def _record_artifact(
        self, session, artifact: StoredStewardArtifact, *, now: datetime
    ) -> None:
        row = await session.get(StewardArtifactRow, artifact.sha256)
        if row is not None:
            if (
                row.kind != artifact.kind.value
                or row.byte_size != artifact.byte_size
                or row.media_type != artifact.media_type
                or row.storage_key != artifact.storage_key
            ):
                raise SourceDerivedBenchmarkConflictError(
                    "artifact digest already has conflicting metadata"
                )
            return
        session.add(
            StewardArtifactRow(
                sha256=artifact.sha256,
                kind=artifact.kind.value,
                byte_size=artifact.byte_size,
                media_type=artifact.media_type,
                storage_key=artifact.storage_key,
                created_at=now,
            )
        )
        await session.flush()

    def _put(self, value: CanonicalModel, *, kind: ArtifactKind, media_type: str):
        return self._artifacts.put(
            canonical_json_bytes(value), media_type=media_type, kind=kind
        )

    def _verify_artifact(
        self, artifact: StoredStewardArtifact, value: CanonicalModel
    ) -> None:
        content = self._artifacts.read(artifact.storage_key)
        if content != canonical_json_bytes(value):
            raise SourceDerivedBenchmarkRepositoryError(
                "stored automated benchmark artifact is inconsistent"
            )
