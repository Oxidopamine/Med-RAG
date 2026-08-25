"""Durable, immutable repository for clinical benchmark adjudication artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.corpus_steward.adjudication_schemas import (
    AdjudicationProcessPolicy,
    BenchmarkAccessAction,
    BenchmarkAccessPolicy,
    BenchmarkAdjudicationRecord,
    BenchmarkPolicyKind,
    BenchmarkSuiteBuildResult,
    BenchmarkThresholdPolicy,
    ClinicalReviewDecision,
    DisagreementResolution,
)
from app.corpus_steward.benchmark_schemas import BenchmarkSuite, BenchmarkSuitePartition
from app.corpus_steward.schemas import ArtifactKind
from app.corpus_steward.storage import ImmutableStewardArtifactStore, StoredStewardArtifact
from app.persistence.database import Database
from app.persistence.models import (
    BenchmarkAccessEventRow,
    BenchmarkAdjudicationCaseRow,
    BenchmarkAdjudicationRecordRow,
    BenchmarkDisagreementResolutionRow,
    BenchmarkPolicyArtifactRow,
    BenchmarkReviewDecisionRow,
    BenchmarkSuiteBuildRow,
    StewardArtifactRow,
)
from app.schemas.corpus import canonical_json_bytes, canonical_sha256
from app.schemas.domain import CanonicalModel, utc_now


class AdjudicationRepositoryError(RuntimeError):
    pass


class AdjudicationRepositoryConflictError(AdjudicationRepositoryError):
    pass


class AdjudicationRepositoryNotFoundError(AdjudicationRepositoryError):
    pass


PolicyArtifact = BenchmarkAccessPolicy | AdjudicationProcessPolicy | BenchmarkThresholdPolicy
_ArtifactModel = TypeVar("_ArtifactModel", bound=CanonicalModel)


_POLICY_METADATA: dict[type[PolicyArtifact], tuple[BenchmarkPolicyKind, ArtifactKind]] = {
    BenchmarkAccessPolicy: (
        BenchmarkPolicyKind.ACCESS,
        ArtifactKind.BENCHMARK_ACCESS_POLICY,
    ),
    AdjudicationProcessPolicy: (
        BenchmarkPolicyKind.ADJUDICATION_PROCESS,
        ArtifactKind.BENCHMARK_ADJUDICATION_POLICY,
    ),
    BenchmarkThresholdPolicy: (
        BenchmarkPolicyKind.THRESHOLD,
        ArtifactKind.BENCHMARK_THRESHOLD_POLICY,
    ),
}


class SQLBenchmarkAdjudicationRepository:
    def __init__(
        self,
        database: Database,
        artifacts: ImmutableStewardArtifactStore,
    ) -> None:
        self._database = database
        self._artifacts = artifacts

    async def register_policy(
        self,
        policy: PolicyArtifact,
        artifact: StoredStewardArtifact,
    ) -> None:
        metadata = _POLICY_METADATA.get(type(policy))
        if metadata is None:
            raise TypeError(f"unsupported benchmark policy type: {type(policy).__name__}")
        kind, artifact_kind = metadata
        if artifact.kind is not artifact_kind:
            raise AdjudicationRepositoryError("benchmark policy artifact kind is inconsistent")
        self._verify_stored_value(artifact, policy)
        now = utc_now()
        try:
            async with self._database.session() as session:
                existing = await session.get(
                    BenchmarkPolicyArtifactRow, policy.policy_sha256
                )
                if existing is not None:
                    self._require_policy_match(existing, policy, artifact, kind)
                    return
                same_revision = await session.scalar(
                    select(BenchmarkPolicyArtifactRow).where(
                        BenchmarkPolicyArtifactRow.kind == kind.value,
                        BenchmarkPolicyArtifactRow.policy_id == policy.content.policy_id,
                        BenchmarkPolicyArtifactRow.revision == policy.content.revision,
                    )
                )
                if same_revision is not None:
                    raise AdjudicationRepositoryConflictError(
                        "benchmark policy revision already identifies different content"
                    )
                await self._record_artifact(session, artifact, now=now)
                session.add(
                    BenchmarkPolicyArtifactRow(
                        policy_sha256=policy.policy_sha256,
                        policy_id=policy.content.policy_id,
                        revision=policy.content.revision,
                        kind=kind.value,
                        payload=policy.model_dump(mode="json"),
                        artifact_sha256=artifact.sha256,
                        sealed_at=now,
                    )
                )
        except IntegrityError as error:
            raise AdjudicationRepositoryConflictError(
                "benchmark policy registry conflict"
            ) from error

    async def access_policy(self, policy_sha256: str) -> BenchmarkAccessPolicy:
        return await self._policy(
            policy_sha256,
            BenchmarkPolicyKind.ACCESS,
            BenchmarkAccessPolicy,
            ArtifactKind.BENCHMARK_ACCESS_POLICY,
        )

    async def adjudication_process(
        self, policy_sha256: str
    ) -> AdjudicationProcessPolicy:
        return await self._policy(
            policy_sha256,
            BenchmarkPolicyKind.ADJUDICATION_PROCESS,
            AdjudicationProcessPolicy,
            ArtifactKind.BENCHMARK_ADJUDICATION_POLICY,
        )

    async def threshold_policy(self, policy_sha256: str) -> BenchmarkThresholdPolicy:
        return await self._policy(
            policy_sha256,
            BenchmarkPolicyKind.THRESHOLD,
            BenchmarkThresholdPolicy,
            ArtifactKind.BENCHMARK_THRESHOLD_POLICY,
        )

    async def register_review_decisions(
        self,
        decisions: Sequence[tuple[ClinicalReviewDecision, StoredStewardArtifact]],
    ) -> None:
        if not decisions:
            return
        now = utc_now()
        try:
            async with self._database.session() as session:
                for decision, artifact in decisions:
                    if artifact.kind is not ArtifactKind.BENCHMARK_REVIEW_DECISION:
                        raise AdjudicationRepositoryError(
                            "clinical review artifact kind is inconsistent"
                        )
                    self._verify_stored_value(artifact, decision)
                    existing = await session.scalar(
                        select(BenchmarkReviewDecisionRow).where(
                            BenchmarkReviewDecisionRow.review_id
                            == decision.content.review_id
                        )
                    )
                    if existing is not None:
                        self._require_review_match(existing, decision, artifact)
                        continue
                    await self._record_artifact(session, artifact, now=now)
                    content = decision.content
                    session.add(
                        BenchmarkReviewDecisionRow(
                            decision_sha256=decision.decision_sha256,
                            review_id=content.review_id,
                            case_id=content.case_id,
                            suite_partition=content.suite_partition.value,
                            adjudicator_identity=content.adjudicator_identity,
                            payload=decision.model_dump(mode="json"),
                            artifact_sha256=artifact.sha256,
                            decided_at=content.decided_at,
                            imported_at=now,
                        )
                    )
        except IntegrityError as error:
            raise AdjudicationRepositoryConflictError(
                "clinical review decision registry conflict"
            ) from error

    async def review_decisions(
        self, case_ids: tuple[str, ...]
    ) -> tuple[ClinicalReviewDecision, ...]:
        async with self._database.session() as session:
            rows = tuple(
                await session.scalars(
                    select(BenchmarkReviewDecisionRow)
                    .where(BenchmarkReviewDecisionRow.case_id.in_(case_ids))
                    .order_by(
                        BenchmarkReviewDecisionRow.case_id,
                        BenchmarkReviewDecisionRow.review_id,
                    )
                )
            )
            decisions: list[ClinicalReviewDecision] = []
            for row in rows:
                decision = ClinicalReviewDecision.model_validate(row.payload)
                self._require_review_match_by_row(row, decision)
                await self._verify_registered_artifact(
                    session,
                    row.artifact_sha256,
                    ArtifactKind.BENCHMARK_REVIEW_DECISION,
                    decision,
                )
                decisions.append(decision)
            return tuple(decisions)

    async def register_resolutions(
        self,
        resolutions: Sequence[tuple[DisagreementResolution, StoredStewardArtifact]],
    ) -> None:
        if not resolutions:
            return
        now = utc_now()
        try:
            async with self._database.session() as session:
                for resolution, artifact in resolutions:
                    if artifact.kind is not ArtifactKind.BENCHMARK_RESOLUTION:
                        raise AdjudicationRepositoryError(
                            "disagreement resolution artifact kind is inconsistent"
                        )
                    self._verify_stored_value(artifact, resolution)
                    existing = await session.scalar(
                        select(BenchmarkDisagreementResolutionRow).where(
                            BenchmarkDisagreementResolutionRow.resolution_id
                            == resolution.content.resolution_id
                        )
                    )
                    if existing is not None:
                        self._require_resolution_match(existing, resolution, artifact)
                        continue
                    await self._record_artifact(session, artifact, now=now)
                    content = resolution.content
                    session.add(
                        BenchmarkDisagreementResolutionRow(
                            resolution_sha256=resolution.resolution_sha256,
                            resolution_id=content.resolution_id,
                            case_id=content.case_id,
                            payload=resolution.model_dump(mode="json"),
                            artifact_sha256=artifact.sha256,
                            resolved_at=content.resolved_at,
                            imported_at=now,
                        )
                    )
        except IntegrityError as error:
            raise AdjudicationRepositoryConflictError(
                "disagreement resolution registry conflict"
            ) from error

    async def resolutions(
        self, case_ids: tuple[str, ...]
    ) -> tuple[DisagreementResolution, ...]:
        async with self._database.session() as session:
            rows = tuple(
                await session.scalars(
                    select(BenchmarkDisagreementResolutionRow)
                    .where(BenchmarkDisagreementResolutionRow.case_id.in_(case_ids))
                    .order_by(BenchmarkDisagreementResolutionRow.case_id)
                )
            )
            resolutions: list[DisagreementResolution] = []
            for row in rows:
                resolution = DisagreementResolution.model_validate(row.payload)
                self._require_resolution_match_by_row(row, resolution)
                await self._verify_registered_artifact(
                    session,
                    row.artifact_sha256,
                    ArtifactKind.BENCHMARK_RESOLUTION,
                    resolution,
                )
                resolutions.append(resolution)
            return tuple(resolutions)

    async def register_adjudication_record(
        self,
        record: BenchmarkAdjudicationRecord,
        artifact: StoredStewardArtifact,
    ) -> None:
        if artifact.kind is not ArtifactKind.BENCHMARK_ADJUDICATION_RECORD:
            raise AdjudicationRepositoryError("adjudication-record artifact kind is inconsistent")
        self._verify_stored_value(artifact, record)
        content = record.content
        development_count = sum(
            item.suite_partition is BenchmarkSuitePartition.DEVELOPMENT
            for item in content.cases
        )
        holdout_count = len(content.cases) - development_count
        try:
            async with self._database.session() as session:
                existing = await session.scalar(
                    select(BenchmarkAdjudicationRecordRow).where(
                        BenchmarkAdjudicationRecordRow.adjudication_record_id
                        == content.adjudication_record_id
                    )
                )
                if existing is not None:
                    if (
                        existing.adjudication_record_sha256
                        != record.adjudication_record_sha256
                        or existing.artifact_sha256 != artifact.sha256
                        or existing.payload != record.model_dump(mode="json")
                    ):
                        raise AdjudicationRepositoryConflictError(
                            "adjudication record ID already identifies different content"
                        )
                    return
                await self._record_artifact(session, artifact, now=content.sealed_at)
                session.add(
                    BenchmarkAdjudicationRecordRow(
                        adjudication_record_sha256=record.adjudication_record_sha256,
                        adjudication_record_id=content.adjudication_record_id,
                        access_policy_sha256=content.access_policy_sha256,
                        adjudication_process_sha256=content.adjudication_process_sha256,
                        payload=record.model_dump(mode="json"),
                        artifact_sha256=artifact.sha256,
                        development_case_count=development_count,
                        sealed_holdout_case_count=holdout_count,
                        sealed_at=content.sealed_at,
                    )
                )
                session.add_all(
                    BenchmarkAdjudicationCaseRow(
                        adjudication_record_sha256=record.adjudication_record_sha256,
                        case_id=item.case_id,
                        suite_partition=item.suite_partition.value,
                        case_sha256=canonical_sha256(item.finalized_case),
                        review_decision_sha256s=list(item.review_decision_sha256s),
                        resolution_sha256=item.resolution_sha256,
                    )
                    for item in content.cases
                )
        except IntegrityError as error:
            raise AdjudicationRepositoryConflictError(
                "benchmark adjudication-record registry conflict"
            ) from error

    async def adjudication_record(
        self, adjudication_record_sha256: str
    ) -> BenchmarkAdjudicationRecord:
        async with self._database.session() as session:
            row = await session.get(
                BenchmarkAdjudicationRecordRow, adjudication_record_sha256
            )
            if row is None:
                raise AdjudicationRepositoryNotFoundError(
                    f"adjudication record not found: {adjudication_record_sha256}"
                )
            record = BenchmarkAdjudicationRecord.model_validate(row.payload)
            if (
                record.adjudication_record_sha256 != row.adjudication_record_sha256
                or record.content.adjudication_record_id != row.adjudication_record_id
                or record.content.access_policy_sha256 != row.access_policy_sha256
                or record.content.adjudication_process_sha256
                != row.adjudication_process_sha256
            ):
                raise AdjudicationRepositoryError(
                    "stored benchmark adjudication record is inconsistent"
                )
            case_rows = tuple(
                await session.scalars(
                    select(BenchmarkAdjudicationCaseRow)
                    .where(
                        BenchmarkAdjudicationCaseRow.adjudication_record_sha256
                        == adjudication_record_sha256
                    )
                    .order_by(BenchmarkAdjudicationCaseRow.case_id)
                )
            )
            if len(case_rows) != len(record.content.cases):
                raise AdjudicationRepositoryError(
                    "stored adjudication case membership is incomplete"
                )
            for case, case_row in zip(record.content.cases, case_rows, strict=True):
                if (
                    case.case_id != case_row.case_id
                    or case.suite_partition.value != case_row.suite_partition
                    or canonical_sha256(case.finalized_case) != case_row.case_sha256
                    or list(case.review_decision_sha256s)
                    != case_row.review_decision_sha256s
                    or case.resolution_sha256 != case_row.resolution_sha256
                ):
                    raise AdjudicationRepositoryError(
                        "stored adjudication case membership is inconsistent"
                    )
            await self._verify_registered_artifact(
                session,
                row.artifact_sha256,
                ArtifactKind.BENCHMARK_ADJUDICATION_RECORD,
                record,
            )
            return record

    async def suite_for_benchmark(
        self, benchmark_id: str, partition: BenchmarkSuitePartition
    ) -> BenchmarkSuiteBuildResult | None:
        async with self._database.session() as session:
            row = await session.scalar(
                select(BenchmarkSuiteBuildRow).where(
                    BenchmarkSuiteBuildRow.benchmark_id == benchmark_id,
                    BenchmarkSuiteBuildRow.suite_partition == partition.value,
                )
            )
            if row is None:
                return None
            return await self._suite_result(session, row)

    async def holdout_suite_for_record(
        self, adjudication_record_sha256: str
    ) -> BenchmarkSuiteBuildResult | None:
        async with self._database.session() as session:
            row = await session.scalar(
                select(BenchmarkSuiteBuildRow).where(
                    BenchmarkSuiteBuildRow.adjudication_record_sha256
                    == adjudication_record_sha256,
                    BenchmarkSuiteBuildRow.suite_partition
                    == BenchmarkSuitePartition.SEALED_HOLDOUT.value,
                )
            )
            if row is None:
                return None
            return await self._suite_result(session, row)

    async def register_suite(
        self,
        result: BenchmarkSuiteBuildResult,
        artifact: StoredStewardArtifact,
        *,
        adjudication_record_sha256: str,
        threshold_policy_sha256: str,
    ) -> None:
        suite = result.suite
        content = suite.content
        if artifact.kind is not ArtifactKind.BENCHMARK_SUITE:
            raise AdjudicationRepositoryError("benchmark suite artifact kind is inconsistent")
        if result.artifact_sha256 != artifact.sha256 or result.storage_key != artifact.storage_key:
            raise AdjudicationRepositoryError("benchmark suite build result is inconsistent")
        self._verify_stored_value(artifact, suite)
        action = (
            BenchmarkAccessAction.BUILD_DEVELOPMENT_SUITE
            if content.suite_partition is BenchmarkSuitePartition.DEVELOPMENT
            else BenchmarkAccessAction.BUILD_SEALED_HOLDOUT_SUITE
        )
        event_id = "BAE_" + hashlib.sha256(
            (
                f"{content.access_policy_sha256}:{result.built_by}:{action.value}:"
                f"{suite.suite_sha256}"
            ).encode()
        ).hexdigest()[:32]
        try:
            async with self._database.session() as session:
                existing = await session.get(BenchmarkSuiteBuildRow, suite.suite_sha256)
                if existing is not None:
                    existing_result = await self._suite_result(session, existing)
                    if existing_result != result:
                        raise AdjudicationRepositoryConflictError(
                            "benchmark suite digest already identifies another build"
                        )
                    return
                await self._record_artifact(session, artifact, now=result.built_at)
                session.add(
                    BenchmarkSuiteBuildRow(
                        suite_sha256=suite.suite_sha256,
                        benchmark_id=content.benchmark_id,
                        suite_partition=content.suite_partition.value,
                        adjudication_record_sha256=adjudication_record_sha256,
                        access_policy_sha256=content.access_policy_sha256,
                        adjudication_process_sha256=content.adjudication_process_sha256,
                        threshold_policy_sha256=threshold_policy_sha256,
                        candidate_configuration_sha256=(
                            content.candidate_configuration_sha256
                        ),
                        corpus_release_id=content.corpus_release_id,
                        manifest_sha256=content.manifest_sha256,
                        case_count=len(content.cases),
                        payload=suite.model_dump(mode="json"),
                        artifact_sha256=artifact.sha256,
                        built_by=result.built_by,
                        built_at=result.built_at,
                    )
                )
                session.add(
                    BenchmarkAccessEventRow(
                        access_event_id=event_id,
                        access_policy_sha256=content.access_policy_sha256,
                        actor_identity=result.built_by,
                        action=action.value,
                        resource_sha256=suite.suite_sha256,
                        occurred_at=result.built_at,
                    )
                )
        except IntegrityError as error:
            raise AdjudicationRepositoryConflictError(
                "benchmark suite build registry conflict"
            ) from error

    async def _policy(
        self,
        policy_sha256: str,
        kind: BenchmarkPolicyKind,
        model_type: type[_ArtifactModel],
        artifact_kind: ArtifactKind,
    ) -> _ArtifactModel:
        async with self._database.session() as session:
            row = await session.get(BenchmarkPolicyArtifactRow, policy_sha256)
            if row is None or row.kind != kind.value:
                raise AdjudicationRepositoryNotFoundError(
                    f"{kind.value.lower()} policy not found: {policy_sha256}"
                )
            policy = model_type.model_validate(row.payload)
            if getattr(policy, "policy_sha256", None) != row.policy_sha256:
                raise AdjudicationRepositoryError("stored benchmark policy is inconsistent")
            await self._verify_registered_artifact(
                session, row.artifact_sha256, artifact_kind, policy
            )
            return policy

    async def _suite_result(
        self, session, row: BenchmarkSuiteBuildRow
    ) -> BenchmarkSuiteBuildResult:
        suite = BenchmarkSuite.model_validate(row.payload)
        if (
            suite.suite_sha256 != row.suite_sha256
            or suite.content.benchmark_id != row.benchmark_id
            or suite.content.suite_partition.value != row.suite_partition
            or suite.content.adjudication_record_sha256
            != row.adjudication_record_sha256
            or suite.content.threshold_policy_sha256 != row.threshold_policy_sha256
            or len(suite.content.cases) != row.case_count
        ):
            raise AdjudicationRepositoryError("stored benchmark suite build is inconsistent")
        artifact = await self._verify_registered_artifact(
            session,
            row.artifact_sha256,
            ArtifactKind.BENCHMARK_SUITE,
            suite,
        )
        return BenchmarkSuiteBuildResult(
            suite=suite,
            artifact_sha256=artifact.sha256,
            storage_key=artifact.storage_key,
            built_by=row.built_by,
            built_at=row.built_at,
        )

    async def _record_artifact(self, session, artifact: StoredStewardArtifact, *, now) -> None:
        row = await session.get(StewardArtifactRow, artifact.sha256)
        if row is not None:
            if not self._artifact_matches(row, artifact):
                raise AdjudicationRepositoryConflictError(
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

    async def _verify_registered_artifact(
        self,
        session,
        artifact_sha256: str,
        expected_kind: ArtifactKind,
        value: CanonicalModel,
    ) -> StewardArtifactRow:
        row = await session.get(StewardArtifactRow, artifact_sha256)
        if row is None:
            raise AdjudicationRepositoryError("registered benchmark artifact is missing")
        if row.kind != expected_kind.value:
            raise AdjudicationRepositoryError("registered benchmark artifact kind is wrong")
        raw = self._artifacts.read(row.storage_key)
        if len(raw) != row.byte_size or hashlib.sha256(raw).hexdigest() != row.sha256:
            raise AdjudicationRepositoryError("registered benchmark artifact bytes are tampered")
        if raw != canonical_json_bytes(value):
            raise AdjudicationRepositoryError("benchmark artifact and registry payload differ")
        return row

    @staticmethod
    def _verify_stored_value(
        artifact: StoredStewardArtifact, value: CanonicalModel
    ) -> None:
        raw = artifact.path.read_bytes()
        if (
            raw != canonical_json_bytes(value)
            or len(raw) != artifact.byte_size
            or hashlib.sha256(raw).hexdigest() != artifact.sha256
        ):
            raise AdjudicationRepositoryError(
                "content-addressed benchmark artifact does not match its payload"
            )

    @staticmethod
    def _artifact_matches(row: StewardArtifactRow, artifact: StoredStewardArtifact) -> bool:
        return (
            row.kind == artifact.kind.value
            and row.byte_size == artifact.byte_size
            and row.media_type == artifact.media_type
            and row.storage_key == artifact.storage_key
        )

    @staticmethod
    def _require_policy_match(
        row: BenchmarkPolicyArtifactRow,
        policy: PolicyArtifact,
        artifact: StoredStewardArtifact,
        kind: BenchmarkPolicyKind,
    ) -> None:
        if (
            row.kind != kind.value
            or row.policy_id != policy.content.policy_id
            or row.revision != policy.content.revision
            or row.payload != policy.model_dump(mode="json")
            or row.artifact_sha256 != artifact.sha256
        ):
            raise AdjudicationRepositoryConflictError(
                "benchmark policy digest already identifies different content"
            )

    @staticmethod
    def _require_review_match(
        row: BenchmarkReviewDecisionRow,
        decision: ClinicalReviewDecision,
        artifact: StoredStewardArtifact,
    ) -> None:
        SQLBenchmarkAdjudicationRepository._require_review_match_by_row(row, decision)
        if row.artifact_sha256 != artifact.sha256:
            raise AdjudicationRepositoryConflictError(
                "review ID already identifies a different artifact"
            )

    @staticmethod
    def _require_review_match_by_row(
        row: BenchmarkReviewDecisionRow, decision: ClinicalReviewDecision
    ) -> None:
        content = decision.content
        if (
            row.decision_sha256 != decision.decision_sha256
            or row.review_id != content.review_id
            or row.case_id != content.case_id
            or row.suite_partition != content.suite_partition.value
            or row.adjudicator_identity != content.adjudicator_identity
            or row.payload != decision.model_dump(mode="json")
        ):
            raise AdjudicationRepositoryConflictError(
                "stored clinical review decision is inconsistent"
            )

    @staticmethod
    def _require_resolution_match(
        row: BenchmarkDisagreementResolutionRow,
        resolution: DisagreementResolution,
        artifact: StoredStewardArtifact,
    ) -> None:
        SQLBenchmarkAdjudicationRepository._require_resolution_match_by_row(row, resolution)
        if row.artifact_sha256 != artifact.sha256:
            raise AdjudicationRepositoryConflictError(
                "resolution ID already identifies a different artifact"
            )

    @staticmethod
    def _require_resolution_match_by_row(
        row: BenchmarkDisagreementResolutionRow,
        resolution: DisagreementResolution,
    ) -> None:
        content = resolution.content
        if (
            row.resolution_sha256 != resolution.resolution_sha256
            or row.resolution_id != content.resolution_id
            or row.case_id != content.case_id
            or row.payload != resolution.model_dump(mode="json")
        ):
            raise AdjudicationRepositoryConflictError(
                "stored disagreement resolution is inconsistent"
            )
