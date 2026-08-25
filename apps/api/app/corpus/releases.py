from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.corpus_steward.benchmark_schemas import (
    BenchmarkAcceptanceAttestationContent,
    BenchmarkReport,
    SignedBenchmarkAcceptance,
)
from app.corpus_steward.registry import (
    AttestationVerificationError,
    SQLAttestationRepository,
)
from app.corpus_steward.schemas import AttestationPurpose, BenchmarkAttestationPurpose
from app.persistence.database import Database
from app.persistence.models import (
    AcquisitionRow,
    ActiveCorpusReleaseRow,
    ArtifactRow,
    BenchmarkAcceptanceRow,
    CanonicalEvidenceRow,
    CorpusReleaseEvidenceRow,
    CorpusReleaseExceptionRow,
    CorpusReleaseRow,
    OutboxEventRow,
    PublisherRow,
    SourceRow,
    SourceVersionRow,
    StewardArtifactRow,
)
from app.schemas.corpus import (
    ActiveCorpusRelease,
    CorpusEvidenceRecord,
    CorpusReleaseBundle,
    CorpusReleaseManifest,
    CorpusReleaseRecord,
    EvidenceApprovalStatus,
    ReleaseState,
    SignedActivationDecision,
    canonical_sha256,
)
from app.schemas.domain import SourceStatus, utc_now
from app.schemas.questions import EvidenceDetail, EvidenceLocator

RETRIEVAL_APPROVED_SOURCE_STATES = {
    SourceStatus.APPROVED.value,
    SourceStatus.EFFECTIVE.value,
    SourceStatus.PARTIALLY_SUPERSEDED.value,
}
MAX_EVIDENCE_DETAILS_PER_RESULT = 100


class CorpusReleaseError(RuntimeError):
    pass


class CorpusReleaseNotFoundError(CorpusReleaseError):
    pass


class CorpusReleaseConflictError(CorpusReleaseError):
    pass


class CorpusReleaseGateError(CorpusReleaseError):
    def __init__(self, blockers: list[str] | tuple[str, ...]) -> None:
        self.blockers = tuple(blockers)
        super().__init__(", ".join(self.blockers))


class ImmutableEvidenceConflictError(CorpusReleaseConflictError):
    pass


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


class SQLCorpusReleaseRepository:
    """Canonical release store with atomic activation and outbox writes."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._attestations = SQLAttestationRepository(database)

    async def register_candidate(self, bundle: CorpusReleaseBundle) -> CorpusReleaseRecord:
        content = bundle.manifest.content
        contract_blockers = list(bundle.activation_blockers())
        registry_blockers: list[str] = []
        state = ReleaseState.VALIDATED if not contract_blockers else ReleaseState.CANDIDATE
        now = utc_now()

        try:
            async with self._database.session() as session:
                existing = await session.get(CorpusReleaseRow, content.corpus_release_id)
                if existing is not None:
                    if existing.manifest_sha256 != bundle.manifest.manifest_sha256:
                        raise CorpusReleaseConflictError(
                            "corpus release ID already identifies a different manifest"
                        )
                    return await self._record(session, existing)

                if (
                    content.previous_release_id is not None
                    and await session.get(CorpusReleaseRow, content.previous_release_id) is None
                ):
                    raise CorpusReleaseGateError(["PREVIOUS_RELEASE_NOT_FOUND"])

                for evidence in bundle.evidence:
                    source_version = await session.get(SourceVersionRow, evidence.source_version_id)
                    if source_version is None:
                        registry_blockers.append(
                            f"SOURCE_VERSION_NOT_FOUND:{evidence.source_version_id}"
                        )
                        continue
                    if source_version.source_id != evidence.source_id:
                        registry_blockers.append(f"SOURCE_ID_MISMATCH:{evidence.evidence_id}")
                    if source_version.status != evidence.lifecycle_status.value:
                        contract_blockers.append(f"LIFECYCLE_MISMATCH:{evidence.evidence_id}")
                    if evidence.verification.approval_status is EvidenceApprovalStatus.APPROVED:
                        if not source_version.approved_for_retrieval:
                            contract_blockers.append(
                                f"SOURCE_NOT_RETRIEVAL_APPROVED:{evidence.source_version_id}"
                            )
                        if source_version.status not in RETRIEVAL_APPROVED_SOURCE_STATES:
                            contract_blockers.append(
                                f"SOURCE_NOT_EFFECTIVE:{evidence.source_version_id}"
                            )
                    artifact_exists = await session.scalar(
                        select(ArtifactRow.artifact_id)
                        .join(
                            AcquisitionRow,
                            AcquisitionRow.artifact_id == ArtifactRow.artifact_id,
                        )
                        .where(
                            AcquisitionRow.source_version_id == evidence.source_version_id,
                            ArtifactRow.sha256 == evidence.source_artifact_sha256,
                        )
                    )
                    if artifact_exists is None:
                        steward_artifact = await session.get(
                            StewardArtifactRow, evidence.source_artifact_sha256
                        )
                        if steward_artifact is None or steward_artifact.kind != "NARRATIVE_SOURCE":
                            registry_blockers.append(
                                f"SOURCE_ARTIFACT_NOT_FOUND:{evidence.evidence_id}"
                            )

                if registry_blockers:
                    raise CorpusReleaseGateError(registry_blockers)
                if contract_blockers:
                    state = ReleaseState.CANDIDATE

                release = CorpusReleaseRow(
                    corpus_release_id=content.corpus_release_id,
                    contract_version=content.schema_version,
                    manifest_sha256=bundle.manifest.manifest_sha256,
                    manifest=bundle.manifest.model_dump(mode="json"),
                    state=state.value,
                    previous_release_id=content.previous_release_id,
                    qdrant_collection=content.qdrant_collection,
                    cutoff_at=content.cutoff_at,
                    index_status="NOT_BUILT",
                    index_point_count=None,
                    index_attestation_sha256=None,
                    index_validated_at=None,
                    validated_at=now if state is ReleaseState.VALIDATED else None,
                    activated_at=None,
                    activated_by=None,
                    activation_decision_sha256=None,
                    created_at=now,
                )
                session.add(release)
                await session.flush()

                for evidence in bundle.evidence:
                    evidence_row = await session.get(CanonicalEvidenceRow, evidence.evidence_id)
                    if evidence_row is not None:
                        if evidence_row.evidence_sha256 != evidence.sha256:
                            raise ImmutableEvidenceConflictError(
                                f"evidence ID {evidence.evidence_id} cannot be overwritten"
                            )
                    else:
                        evidence_row = CanonicalEvidenceRow(
                            evidence_id=evidence.evidence_id,
                            evidence_sha256=evidence.sha256,
                            source_id=evidence.source_id,
                            source_version_id=evidence.source_version_id,
                            approval_status=evidence.verification.approval_status.value,
                            payload=evidence.model_dump(mode="json"),
                            created_at=now,
                        )
                        session.add(evidence_row)
                    session.add(
                        CorpusReleaseEvidenceRow(
                            corpus_release_id=content.corpus_release_id,
                            evidence_id=evidence.evidence_id,
                        )
                    )

                session.add_all(
                    CorpusReleaseExceptionRow(
                        exception_id=exception.exception_id,
                        corpus_release_id=content.corpus_release_id,
                        trust_root_id=exception.trust_root_id,
                        inventory_item_id=exception.inventory_item_id,
                        reason=exception.reason.value,
                        statement_sha256=exception.statement_sha256,
                        signature_sha256=exception.signature_sha256,
                        payload=exception.model_dump(mode="json"),
                        created_at=now,
                    )
                    for exception in content.exceptions
                )
                self._append_outbox(
                    session,
                    aggregate_id=content.corpus_release_id,
                    event_type="CORPUS_RELEASE_REGISTERED",
                    deduplication_key=(
                        f"corpus-release:{content.corpus_release_id}:registered:"
                        f"{bundle.manifest.manifest_sha256}"
                    ),
                    payload={
                        "corpus_release_id": content.corpus_release_id,
                        "manifest_sha256": bundle.manifest.manifest_sha256,
                        "state": state.value,
                        "blockers": contract_blockers,
                    },
                    now=now,
                )
                await session.flush()
                return await self._record(session, release)
        except IntegrityError as error:
            raise CorpusReleaseConflictError("corpus release registry conflict") from error

    async def mark_index_validated(
        self,
        corpus_release_id: str,
        *,
        point_count: int,
        index_attestation_sha256: str,
    ) -> CorpusReleaseRecord:
        if point_count < 0:
            raise ValueError("point_count cannot be negative")
        if len(index_attestation_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in index_attestation_sha256
        ):
            raise ValueError("index_attestation_sha256 must be a lowercase SHA-256 digest")
        now = utc_now()
        async with self._database.session() as session:
            release = await session.get(CorpusReleaseRow, corpus_release_id)
            if release is None:
                raise CorpusReleaseNotFoundError(corpus_release_id)
            if release.state != ReleaseState.VALIDATED.value:
                raise CorpusReleaseGateError(["RELEASE_CONTRACT_NOT_VALIDATED"])
            evidence_count = await self._evidence_count(session, corpus_release_id)
            if point_count != evidence_count:
                raise CorpusReleaseGateError(["INDEX_EVIDENCE_COUNT_MISMATCH"])
            if release.index_status == "VALIDATED":
                if (
                    release.index_point_count != point_count
                    or release.index_attestation_sha256 != index_attestation_sha256
                ):
                    raise CorpusReleaseConflictError(
                        "validated index attestation cannot be changed"
                    )
                return await self._record(session, release)
            release.index_status = "VALIDATED"
            release.index_point_count = point_count
            release.index_attestation_sha256 = index_attestation_sha256
            release.index_validated_at = now
            self._append_outbox(
                session,
                aggregate_id=corpus_release_id,
                event_type="CORPUS_INDEX_VALIDATED",
                deduplication_key=f"corpus-release:{corpus_release_id}:index-validated",
                payload={
                    "corpus_release_id": corpus_release_id,
                    "qdrant_collection": release.qdrant_collection,
                    "point_count": point_count,
                    "index_attestation_sha256": index_attestation_sha256,
                },
                now=now,
            )
            return await self._record(session, release)

    async def register_benchmark_acceptance(
        self,
        corpus_release_id: str,
        *,
        acceptance: SignedBenchmarkAcceptance,
        report: BenchmarkReport,
    ) -> CorpusReleaseRecord:
        """Verify and persist the one sealed-holdout acceptance for a release."""

        try:
            await self._attestations.verify_existing_reference(
                acceptance.content,
                purpose=BenchmarkAttestationPurpose.BENCHMARK_ACCEPTANCE,
                predicate_type=(
                    "https://med-rag.local/attestations/benchmark-acceptance"
                ),
                statement_sha256=acceptance.statement_sha256,
                signature_sha256=acceptance.signature_sha256,
                signing_key_id=acceptance.signing_key_id,
                signer_identity=acceptance.signer_identity,
            )
        except AttestationVerificationError as error:
            raise CorpusReleaseGateError(
                ["BENCHMARK_ACCEPTANCE_SIGNATURE_NOT_VERIFIED"]
            ) from error

        content = acceptance.content
        result = report.content
        blockers: list[str] = []
        if result.outcome != "ACCEPTED":
            blockers.append("BENCHMARK_REPORT_REJECTED")
        if result.suite_partition.value != content.suite_partition:
            blockers.append("BENCHMARK_NOT_SEALED_HOLDOUT")
        for actual, expected, blocker in (
            (
                content.benchmark_report_sha256,
                report.report_sha256,
                "BENCHMARK_REPORT_DIGEST_MISMATCH",
            ),
            (
                content.benchmark_suite_sha256,
                result.benchmark_suite_sha256,
                "BENCHMARK_SUITE_DIGEST_MISMATCH",
            ),
            (content.runner_version, result.runner_version, "BENCHMARK_RUNNER_MISMATCH"),
            (
                content.candidate_configuration_sha256,
                result.candidate_configuration_sha256,
                "BENCHMARK_CANDIDATE_MISMATCH",
            ),
            (content.corpus_release_id, result.corpus_release_id, "BENCHMARK_RELEASE_MISMATCH"),
            (content.manifest_sha256, result.manifest_sha256, "BENCHMARK_MANIFEST_MISMATCH"),
            (
                content.vector_batch_sha256,
                result.vector_batch_sha256,
                "BENCHMARK_VECTOR_BATCH_MISMATCH",
            ),
            (
                content.qdrant_collection,
                result.qdrant_collection,
                "BENCHMARK_INDEX_MISMATCH",
            ),
            (
                content.holdout_access_policy_sha256,
                result.access_policy_sha256,
                "BENCHMARK_ACCESS_POLICY_MISMATCH",
            ),
            (
                content.adjudication_process_sha256,
                result.adjudication_process_sha256,
                "BENCHMARK_ADJUDICATION_PROCESS_MISMATCH",
            ),
            (
                content.adjudication_record_sha256,
                result.adjudication_record_sha256,
                "BENCHMARK_ADJUDICATION_RECORD_MISMATCH",
            ),
            (
                content.threshold_policy_sha256,
                result.threshold_policy_sha256,
                "BENCHMARK_THRESHOLD_POLICY_MISMATCH",
            ),
        ):
            if actual != expected:
                blockers.append(blocker)
        now = utc_now()
        if _as_utc(content.accepted_at) < _as_utc(result.generated_at):
            blockers.append("BENCHMARK_ACCEPTED_BEFORE_REPORT")
        if _as_utc(content.accepted_at) > now:
            blockers.append("BENCHMARK_ACCEPTANCE_NOT_YET_VALID")
        if _as_utc(content.valid_until) <= now:
            blockers.append("BENCHMARK_ACCEPTANCE_STALE")
        if blockers:
            raise CorpusReleaseGateError(blockers)

        async with self._database.session() as session:
            release = await session.scalar(
                select(CorpusReleaseRow)
                .where(CorpusReleaseRow.corpus_release_id == corpus_release_id)
                .with_for_update()
            )
            if release is None:
                raise CorpusReleaseNotFoundError(corpus_release_id)
            relationship_blockers: list[str] = []
            if release.index_status != "VALIDATED":
                relationship_blockers.append("RETRIEVAL_INDEX_NOT_VALIDATED")
            if content.corpus_release_id != corpus_release_id:
                relationship_blockers.append("BENCHMARK_RELEASE_MISMATCH")
            if content.manifest_sha256 != release.manifest_sha256:
                relationship_blockers.append("BENCHMARK_MANIFEST_MISMATCH")
            if content.qdrant_collection != release.qdrant_collection:
                relationship_blockers.append("BENCHMARK_INDEX_MISMATCH")
            if content.index_attestation_sha256 != release.index_attestation_sha256:
                relationship_blockers.append("BENCHMARK_INDEX_ATTESTATION_MISMATCH")
            if relationship_blockers:
                raise CorpusReleaseGateError(relationship_blockers)

            existing = await session.get(BenchmarkAcceptanceRow, content.acceptance_id)
            if existing is not None:
                if existing.statement_sha256 != acceptance.statement_sha256:
                    raise CorpusReleaseConflictError(
                        "benchmark acceptance ID already has different signed content"
                    )
                if release.benchmark_acceptance_sha256 != acceptance.statement_sha256:
                    raise CorpusReleaseConflictError(
                        "stored release benchmark acceptance link is inconsistent"
                    )
                return await self._record(session, release)
            if release.benchmark_acceptance_sha256 is not None:
                raise CorpusReleaseConflictError(
                    "release already has a different benchmark acceptance"
                )
            session.add(
                BenchmarkAcceptanceRow(
                    acceptance_id=content.acceptance_id,
                    statement_sha256=acceptance.statement_sha256,
                    signature_sha256=acceptance.signature_sha256,
                    corpus_release_id=corpus_release_id,
                    benchmark_suite_sha256=content.benchmark_suite_sha256,
                    benchmark_report_sha256=content.benchmark_report_sha256,
                    candidate_configuration_sha256=(
                        content.candidate_configuration_sha256
                    ),
                    manifest_sha256=content.manifest_sha256,
                    vector_batch_sha256=content.vector_batch_sha256,
                    qdrant_collection=content.qdrant_collection,
                    index_attestation_sha256=content.index_attestation_sha256,
                    runner_version=content.runner_version,
                    signing_key_id=acceptance.signing_key_id,
                    signer_identity=acceptance.signer_identity,
                    payload=content.model_dump(mode="json"),
                    accepted_at=content.accepted_at,
                    valid_until=content.valid_until,
                    created_at=now,
                )
            )
            release.benchmark_acceptance_sha256 = acceptance.statement_sha256
            release.benchmark_accepted_at = content.accepted_at
            release.benchmark_valid_until = content.valid_until
            self._append_outbox(
                session,
                aggregate_id=corpus_release_id,
                event_type="CORPUS_BENCHMARK_ACCEPTED",
                deduplication_key=(
                    f"corpus-release:{corpus_release_id}:benchmark-accepted:"
                    f"{acceptance.statement_sha256}"
                ),
                payload={
                    "corpus_release_id": corpus_release_id,
                    "benchmark_acceptance_sha256": acceptance.statement_sha256,
                    "benchmark_report_sha256": report.report_sha256,
                    "candidate_configuration_sha256": (
                        content.candidate_configuration_sha256
                    ),
                    "vector_batch_sha256": content.vector_batch_sha256,
                    "index_attestation_sha256": content.index_attestation_sha256,
                },
                now=now,
            )
            await session.flush()
            return await self._record(session, release)

    async def activate(
        self,
        corpus_release_id: str,
        *,
        decision: SignedActivationDecision,
    ) -> ActiveCorpusRelease:
        decision_digest = decision.statement_sha256
        signature_blocker: str | None = None
        try:
            await self._attestations.verify_existing_reference(
                decision.content,
                purpose=AttestationPurpose.ACTIVATION,
                predicate_type="https://med-rag.local/attestations/activation-decision",
                statement_sha256=decision.statement_sha256,
                signature_sha256=decision.signature_sha256,
                signing_key_id=decision.signing_key_id,
                signer_identity=decision.signer_identity,
            )
        except AttestationVerificationError:
            signature_blocker = "ACTIVATION_SIGNATURE_NOT_VERIFIED"
        now = utc_now()
        async with self._database.session() as session:
            release = await session.scalar(
                select(CorpusReleaseRow)
                .where(CorpusReleaseRow.corpus_release_id == corpus_release_id)
                .with_for_update()
            )
            if release is None:
                raise CorpusReleaseNotFoundError(corpus_release_id)
            pointer = await session.scalar(
                select(ActiveCorpusReleaseRow)
                .where(ActiveCorpusReleaseRow.singleton_key == 1)
                .with_for_update()
            )
            if pointer is not None and pointer.corpus_release_id == corpus_release_id:
                if release.activation_decision_sha256 != decision_digest:
                    raise CorpusReleaseConflictError(
                        "an active release cannot be reactivated with a different decision"
                    )
                return self._active_record(pointer, release)
            blockers: list[str] = []
            if signature_blocker is not None:
                blockers.append(signature_blocker)
            if release.state != ReleaseState.VALIDATED.value:
                blockers.append("RELEASE_CONTRACT_NOT_VALIDATED")
            if release.index_status != "VALIDATED":
                blockers.append("RETRIEVAL_INDEX_NOT_VALIDATED")
            manifest = CorpusReleaseManifest.model_validate(release.manifest)
            benchmark_acceptance = await session.scalar(
                select(BenchmarkAcceptanceRow)
                .where(BenchmarkAcceptanceRow.corpus_release_id == corpus_release_id)
                .with_for_update()
            )
            if benchmark_acceptance is None or release.benchmark_acceptance_sha256 is None:
                blockers.append("BENCHMARK_ACCEPTANCE_MISSING")
            else:
                try:
                    accepted_content = BenchmarkAcceptanceAttestationContent.model_validate(
                        benchmark_acceptance.payload
                    )
                    if (
                        canonical_sha256(accepted_content)
                        != benchmark_acceptance.statement_sha256
                    ):
                        raise ValueError("stored acceptance payload digest mismatch")
                except Exception:
                    accepted_content = None
                    blockers.append("BENCHMARK_ACCEPTANCE_TAMPERED")
                if (
                    benchmark_acceptance.statement_sha256
                    != release.benchmark_acceptance_sha256
                ):
                    blockers.append("BENCHMARK_ACCEPTANCE_LINK_MISMATCH")
                if (
                    decision.content.benchmark_acceptance_sha256
                    != benchmark_acceptance.statement_sha256
                ):
                    blockers.append("ACTIVATION_BENCHMARK_ACCEPTANCE_MISMATCH")
                if _as_utc(benchmark_acceptance.valid_until) <= now:
                    blockers.append("BENCHMARK_ACCEPTANCE_STALE")
                if accepted_content is not None and (
                    accepted_content.corpus_release_id != corpus_release_id
                    or accepted_content.manifest_sha256 != release.manifest_sha256
                    or accepted_content.qdrant_collection != release.qdrant_collection
                    or accepted_content.index_attestation_sha256
                    != release.index_attestation_sha256
                    or accepted_content.vector_batch_sha256
                    != benchmark_acceptance.vector_batch_sha256
                    or accepted_content.candidate_configuration_sha256
                    != benchmark_acceptance.candidate_configuration_sha256
                ):
                    blockers.append("BENCHMARK_ACCEPTANCE_RELATIONSHIP_MISMATCH")
            if decision.content.corpus_release_id != corpus_release_id:
                blockers.append("ACTIVATION_RELEASE_MISMATCH")
            if decision.content.manifest_sha256 != release.manifest_sha256:
                blockers.append("ACTIVATION_MANIFEST_MISMATCH")
            if decision.content.release_policy_sha256 != manifest.content.release_policy_sha256:
                blockers.append("ACTIVATION_POLICY_MISMATCH")
            if decision.content.qdrant_collection != release.qdrant_collection:
                blockers.append("ACTIVATION_INDEX_MISMATCH")
            if decision.content.index_point_count != release.index_point_count:
                blockers.append("ACTIVATION_INDEX_COUNT_MISMATCH")
            if decision.content.index_attestation_sha256 != release.index_attestation_sha256:
                blockers.append("ACTIVATION_INDEX_ATTESTATION_MISMATCH")
            if pointer is None:
                if release.previous_release_id is not None:
                    blockers.append("PREVIOUS_RELEASE_IS_NOT_ACTIVE")
            elif release.previous_release_id != pointer.corpus_release_id:
                blockers.append("PREVIOUS_RELEASE_IS_NOT_ACTIVE")
            if blockers:
                raise CorpusReleaseGateError(blockers)

            if pointer is not None:
                previous = await session.scalar(
                    select(CorpusReleaseRow)
                    .where(CorpusReleaseRow.corpus_release_id == pointer.corpus_release_id)
                    .with_for_update()
                )
                if previous is None:
                    raise CorpusReleaseGateError(["ACTIVE_RELEASE_RECORD_MISSING"])
                previous.state = ReleaseState.SUPERSEDED.value

            release.state = ReleaseState.ACTIVE.value
            release.activated_at = now
            release.activated_by = decision.signer_identity
            release.activation_decision_sha256 = decision_digest
            if pointer is None:
                pointer = ActiveCorpusReleaseRow(
                    singleton_key=1,
                    corpus_release_id=corpus_release_id,
                    manifest_sha256=release.manifest_sha256,
                    activated_at=now,
                    activated_by=decision.signer_identity,
                )
                session.add(pointer)
            else:
                pointer.corpus_release_id = corpus_release_id
                pointer.manifest_sha256 = release.manifest_sha256
                pointer.activated_at = now
                pointer.activated_by = decision.signer_identity

            self._append_outbox(
                session,
                aggregate_id=corpus_release_id,
                event_type="CORPUS_RELEASE_ACTIVATED",
                deduplication_key=(
                    f"corpus-release:{corpus_release_id}:activated:{decision_digest}"
                ),
                payload={
                    "corpus_release_id": corpus_release_id,
                    "manifest_sha256": release.manifest_sha256,
                    "qdrant_collection": release.qdrant_collection,
                    "activation_decision_sha256": decision_digest,
                    "activation_signature_sha256": decision.signature_sha256,
                    "benchmark_acceptance_sha256": (
                        release.benchmark_acceptance_sha256
                    ),
                    "signing_key_id": decision.signing_key_id,
                },
                now=now,
            )
            await session.flush()
            return self._active_record(pointer, release)

    async def active_release(self) -> ActiveCorpusRelease | None:
        async with self._database.session() as session:
            pointer = await session.get(ActiveCorpusReleaseRow, 1)
            if pointer is None:
                return None
            release = await session.get(CorpusReleaseRow, pointer.corpus_release_id)
            if release is None or release.state != ReleaseState.ACTIVE.value:
                return None
            if pointer.manifest_sha256 != release.manifest_sha256:
                return None
            return self._active_record(pointer, release)

    async def evidence_details(
        self,
        corpus_release_id: str,
        evidence_ids: Collection[str],
    ) -> list[EvidenceDetail]:
        """Resolve render-safe canonical evidence from the current active release.

        Missing, malformed, restricted, stale, or non-approved records are omitted. The
        caller must compare the returned IDs with its rendered-claim evidence IDs and
        abstain if any required detail is absent.
        """

        requested_ids = sorted(set(evidence_ids))
        if not requested_ids or len(requested_ids) > MAX_EVIDENCE_DETAILS_PER_RESULT:
            return []

        async with self._database.session() as session:
            pointer = await session.get(ActiveCorpusReleaseRow, 1)
            release = await session.get(CorpusReleaseRow, corpus_release_id)
            if (
                pointer is None
                or release is None
                or pointer.corpus_release_id != corpus_release_id
                or pointer.manifest_sha256 != release.manifest_sha256
                or release.state != ReleaseState.ACTIVE.value
            ):
                return []

            rows = (
                await session.execute(
                    select(
                        CanonicalEvidenceRow,
                        SourceVersionRow,
                        SourceRow,
                        PublisherRow,
                    )
                    .join(
                        CorpusReleaseEvidenceRow,
                        CorpusReleaseEvidenceRow.evidence_id == CanonicalEvidenceRow.evidence_id,
                    )
                    .join(
                        SourceVersionRow,
                        SourceVersionRow.source_version_id
                        == CanonicalEvidenceRow.source_version_id,
                    )
                    .join(SourceRow, SourceRow.source_id == CanonicalEvidenceRow.source_id)
                    .join(PublisherRow, PublisherRow.publisher_id == SourceRow.publisher_id)
                    .where(
                        CorpusReleaseEvidenceRow.corpus_release_id == corpus_release_id,
                        CanonicalEvidenceRow.evidence_id.in_(requested_ids),
                        CanonicalEvidenceRow.approval_status
                        == EvidenceApprovalStatus.APPROVED.value,
                        SourceVersionRow.approved_for_retrieval.is_(True),
                        SourceVersionRow.status.in_(RETRIEVAL_APPROVED_SOURCE_STATES),
                    )
                )
            ).all()

            details: list[EvidenceDetail] = []
            for evidence_row, source_version, source, publisher in rows:
                detail = self._safe_evidence_detail(
                    evidence_row,
                    source_version,
                    source,
                    publisher.name,
                    corpus_release_id=corpus_release_id,
                )
                if detail is not None:
                    details.append(detail)
            return sorted(details, key=lambda item: item.evidence_id)

    @staticmethod
    def _safe_evidence_detail(
        evidence_row: CanonicalEvidenceRow,
        source_version: SourceVersionRow,
        source: SourceRow,
        publisher_name: str,
        *,
        corpus_release_id: str,
    ) -> EvidenceDetail | None:
        try:
            evidence = CorpusEvidenceRecord.model_validate(evidence_row.payload)
        except (TypeError, ValueError):
            return None

        if (
            evidence_row.approval_status != EvidenceApprovalStatus.APPROVED.value
            or evidence.verification.approval_status is not EvidenceApprovalStatus.APPROVED
            or evidence.corpus_release_id != corpus_release_id
            or evidence.evidence_id != evidence_row.evidence_id
            or evidence.source_id != evidence_row.source_id
            or evidence.source_version_id != evidence_row.source_version_id
            or evidence.source_id != source.source_id
            or evidence.source_version_id != source_version.source_version_id
            or source_version.source_id != source.source_id
            or evidence.publisher_id != source.publisher_id
            or evidence.jurisdiction != source.jurisdiction
            or evidence.lifecycle_status.value != source_version.status
            or not source_version.approved_for_retrieval
            or source_version.status not in RETRIEVAL_APPROVED_SOURCE_STATES
            or evidence.sha256 != evidence_row.evidence_sha256
        ):
            return None

        render_allowed = evidence.render_allowed and source.license_render_allowed
        locators = [
            EvidenceLocator(
                kind=anchor.kind.value,
                source_uri=anchor.source_uri,
                pdf_page=anchor.pdf_page,
                printed_page=anchor.printed_page,
                bbox=anchor.bbox,
                exact_highlight_available=render_allowed and anchor.bbox is not None,
            )
            for anchor in evidence.anchors
        ]
        return EvidenceDetail(
            evidence_id=evidence.evidence_id,
            exact_text=evidence.content_exact if render_allowed else None,
            # The current canonical corpus contract has evidence roles, not a type or
            # section hierarchy. Keep those fields truthful until ingestion supplies them.
            evidence_type=None,
            evidence_roles=[role.value for role in evidence.evidence_roles],
            section_path=[],
            source_id=source.source_id,
            source_version_id=source_version.source_version_id,
            source_title=source.title,
            source_version_label=source_version.version_label,
            publisher_name=publisher_name,
            source_url=source.canonical_url,
            source_class=source.source_class,
            jurisdiction=source.jurisdiction,
            language=evidence.language,
            lifecycle_status=source_version.status,
            effective_from=source_version.effective_from,
            effective_to=source_version.effective_to,
            render_allowed=render_allowed,
            locators=locators,
        )

    @staticmethod
    async def _evidence_count(session, corpus_release_id: str) -> int:
        count = await session.scalar(
            select(func.count())
            .select_from(CorpusReleaseEvidenceRow)
            .where(CorpusReleaseEvidenceRow.corpus_release_id == corpus_release_id)
        )
        return int(count or 0)

    async def _record(self, session, row: CorpusReleaseRow) -> CorpusReleaseRecord:
        return CorpusReleaseRecord(
            corpus_release_id=row.corpus_release_id,
            contract_version=row.contract_version,
            manifest_sha256=row.manifest_sha256,
            state=ReleaseState(row.state),
            previous_release_id=row.previous_release_id,
            qdrant_collection=row.qdrant_collection,
            cutoff_at=_as_utc(row.cutoff_at),
            evidence_count=await self._evidence_count(session, row.corpus_release_id),
            index_status=row.index_status,
            index_point_count=row.index_point_count,
            index_attestation_sha256=row.index_attestation_sha256,
            index_validated_at=_as_utc(row.index_validated_at),
            benchmark_acceptance_sha256=row.benchmark_acceptance_sha256,
            benchmark_accepted_at=_as_utc(row.benchmark_accepted_at),
            benchmark_valid_until=_as_utc(row.benchmark_valid_until),
            validated_at=_as_utc(row.validated_at),
            activated_at=_as_utc(row.activated_at),
            activated_by=row.activated_by,
        )

    @staticmethod
    def _active_record(
        pointer: ActiveCorpusReleaseRow, release: CorpusReleaseRow
    ) -> ActiveCorpusRelease:
        return ActiveCorpusRelease(
            corpus_release_id=pointer.corpus_release_id,
            manifest_sha256=pointer.manifest_sha256,
            qdrant_collection=release.qdrant_collection,
            activated_at=_as_utc(pointer.activated_at),
        )

    @staticmethod
    def _append_outbox(
        session,
        *,
        aggregate_id: str,
        event_type: str,
        deduplication_key: str,
        payload: dict[str, object],
        now: datetime,
    ) -> None:
        session.add(
            OutboxEventRow(
                outbox_event_id=_new_id("OUT"),
                aggregate_type="CORPUS_RELEASE",
                aggregate_id=aggregate_id,
                event_type=event_type,
                deduplication_key=deduplication_key,
                payload=payload,
                created_at=now,
                published_at=None,
            )
        )
