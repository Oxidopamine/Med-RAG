from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
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
    ExtractionRunRow,
    OutboxEventRow,
    PublisherRow,
    SourceRow,
    SourceVersionRow,
    StewardArtifactRow,
    TrustRootRow,
)
from app.schemas.corpus import (
    ActiveCorpusRelease,
    CorpusCatalogue,
    CorpusCatalogueRelease,
    CorpusCatalogueSource,
    CorpusCatalogueVersion,
    CorpusEvidenceRecord,
    CorpusReleaseBundle,
    CorpusReleaseManifest,
    CorpusReleaseRecord,
    CorpusTrustRoot,
    EvidenceApprovalStatus,
    LocatorKind,
    ReleaseState,
    SignedActivationDecision,
    SourceAnchor,
    canonical_sha256,
)
from app.schemas.domain import SERVABLE_LIFECYCLE_VALUES, SourceStatus, utc_now
from app.schemas.questions import EvidenceDetail, EvidenceLocator

# What a *release* may contain at registration time. Deliberately wider than what may be
# served: a source can be approved-and-not-yet-in-force when a release is built, and a
# partially superseded source can still be a legitimate release member.
#
# The looseness is real and recorded rather than silently relied upon: a release can hold
# a record that neither retrieval nor evidence-detail resolution will ever serve, because
# both of those answer from `SERVABLE_LIFECYCLE_VALUES`, which admits `EFFECTIVE` only.
# Such a record is inert, not unsafe. Narrowing this set would change which releases may
# be registered at all, which is a corpus-policy decision and not a serving one.
RELEASABLE_SOURCE_STATES = {
    SourceStatus.APPROVED.value,
    SourceStatus.EFFECTIVE.value,
    SourceStatus.PARTIALLY_SUPERSEDED.value,
}
MAX_EVIDENCE_DETAILS_PER_RESULT = 100


# `ArtifactKind` records *how bytes were acquired*, not whether they may become
# clinical evidence. The DAK topology acquires its narratives as side-channel
# NARRATIVE_SOURCE assets; the narrative topology's guideline *is* the inventory
# SOURCE artifact, and re-acquiring it under a second kind is refused by the ledger
# because the same bytes must not carry two provenance stories. Widening this check
# is safe for the same reason `artifact_storage_keys` could be widened: what protects
# clinical content is the per-asset `evidence_materialization_allowed` policy and the
# authority binding, both of which run unchanged.

SOURCE_ARTIFACT_KINDS = frozenset({"NARRATIVE_SOURCE", "SOURCE"})


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


PDF_MEDIA_TYPE = "application/pdf"


@dataclass(frozen=True)
class SourcePageArtifact:
    """A source document a page image may legitimately be rendered from.

    Only ever produced by `source_page_artifact`, which will not construct one unless the
    source's licence permits page reproduction. Holding an instance therefore *is* the
    permission; there is no flag on it to re-check and none to get wrong.
    """

    source_id: str
    artifact_sha256: str
    storage_key: str
    byte_size: int
    media_type: str
    source_title: str


@dataclass(frozen=True)
class TableRowNeighbour:
    """One row of a table, as a reader inspecting a neighbouring row is shown it."""

    row_index: int
    is_anchor_row: bool
    detail: EvidenceDetail


# How far either side of an anchored row a reader may look. A decision table is
# disambiguated by its immediate neighbours - the row above that opens the condition, the
# row below that carries the exception - not by a page of them, and an unbounded radius
# would turn a provenance lookup into a corpus export.
MAX_TABLE_NEIGHBOUR_RADIUS = 10


def _section_path(anchors: Collection[SourceAnchor]) -> list[str]:
    """The structural path this record sits on, from anchors that already record one.

    The canonical evidence contract has no section hierarchy, so this is derived rather
    than carried, and only from coordinates that name a structure themselves. A workbook
    anchor's `table_id` is the worksheet its row belongs to - `HIV.D`, `Annex2Dosing` -
    which is what a reader means by the section of a spreadsheet source, and it is
    already sealed into the record's anchors, so reading it here invents nothing and
    forces no re-materialization.

    A page number is deliberately not turned into a section. It is reported as a page
    everywhere a page belongs, and promoting it here would manufacture a hierarchy the
    document does not have. Returning nothing is the honest answer for such a record.
    """

    seen: list[str] = []
    for anchor in anchors:
        if anchor.kind is not LocatorKind.TABLE_CELL:
            continue
        table_id = (anchor.table_id or "").strip()
        if table_id and table_id not in seen:
            seen.append(table_id)
    return seen


def _anchored_table_row(payload: object, table_id: str) -> int | None:
    """The row a raw payload anchors in `table_id`, without validating the whole record.

    A neighbourhood lookup scans every evidence record the source contributes to the
    release, and validating each one to read a single integer would cost a full model
    build per row for the many that are not neighbours. The records that survive this
    filter are validated in full by `_safe_evidence_detail` before anything is served, so
    this is a prefilter and never the thing that decides what a caller sees.
    """

    if not isinstance(payload, dict):
        return None
    anchors = payload.get("anchors")
    if not isinstance(anchors, list):
        return None
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        if anchor.get("kind") != LocatorKind.TABLE_CELL.value:
            continue
        if anchor.get("table_id") != table_id:
            continue
        row_index = anchor.get("row_index")
        if isinstance(row_index, int) and not isinstance(row_index, bool) and row_index >= 0:
            return row_index
    return None


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
                        if source_version.status not in RELEASABLE_SOURCE_STATES:
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
                        if (
                            steward_artifact is None
                            or steward_artifact.kind not in SOURCE_ARTIFACT_KINDS
                        ):
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
        qdrant_collection: str | None = None,
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
            selected_collection = qdrant_collection or release.qdrant_collection
            if (
                not selected_collection
                or len(selected_collection) > 255
                or (
                    selected_collection != release.qdrant_collection
                    and not selected_collection.startswith(f"{release.qdrant_collection}--vp-")
                )
            ):
                raise CorpusReleaseGateError(["INDEX_COLLECTION_IDENTITY_MISMATCH"])
            if release.index_status == "VALIDATED":
                if (
                    release.index_point_count != point_count
                    or release.index_attestation_sha256 != index_attestation_sha256
                    or release.qdrant_collection != selected_collection
                ):
                    raise CorpusReleaseConflictError(
                        "validated index attestation cannot be changed"
                    )
                return await self._record(session, release)
            release.index_status = "VALIDATED"
            release.qdrant_collection = selected_collection
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
                predicate_type=("https://med-rag.local/attestations/benchmark-acceptance"),
                statement_sha256=acceptance.statement_sha256,
                signature_sha256=acceptance.signature_sha256,
                signing_key_id=acceptance.signing_key_id,
                signer_identity=acceptance.signer_identity,
            )
        except AttestationVerificationError as error:
            raise CorpusReleaseGateError(["BENCHMARK_ACCEPTANCE_SIGNATURE_NOT_VERIFIED"]) from error

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
                content.provenance_mode,
                result.provenance_mode,
                "BENCHMARK_PROVENANCE_MODE_MISMATCH",
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
                content.generation_policy_sha256,
                result.generation_policy_sha256,
                "BENCHMARK_GENERATION_POLICY_MISMATCH",
            ),
            (
                content.generation_record_sha256,
                result.generation_record_sha256,
                "BENCHMARK_GENERATION_RECORD_MISMATCH",
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
                    candidate_configuration_sha256=(content.candidate_configuration_sha256),
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
                    "candidate_configuration_sha256": (content.candidate_configuration_sha256),
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
                    if canonical_sha256(accepted_content) != benchmark_acceptance.statement_sha256:
                        raise ValueError("stored acceptance payload digest mismatch")
                except Exception:
                    accepted_content = None
                    blockers.append("BENCHMARK_ACCEPTANCE_TAMPERED")
                if benchmark_acceptance.statement_sha256 != release.benchmark_acceptance_sha256:
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
                    or accepted_content.index_attestation_sha256 != release.index_attestation_sha256
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
                    "benchmark_acceptance_sha256": (release.benchmark_acceptance_sha256),
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

    async def catalogue(self, served: ActiveCorpusRelease | None) -> CorpusCatalogue:
        """The served release, the documents it carries evidence from, and the trust roots.

        Read-only and licence-neutral: it names documents, editions and counts, never a
        passage. The served release is taken from the caller rather than the pointer for
        the reason ``/health/ready`` gives - under research serving the pointer is empty
        while answers are being produced.
        """
        async with self._database.session() as session:
            release_block: CorpusCatalogueRelease | None = None
            sources: list[CorpusCatalogueSource] = []
            if served is not None:
                row = await session.get(CorpusReleaseRow, served.corpus_release_id)
                if row is not None:
                    release_block = CorpusCatalogueRelease(
                        corpus_release_id=row.corpus_release_id,
                        serving_mode=served.serving_mode,
                        state=ReleaseState(row.state),
                        contract_version=row.contract_version,
                        manifest_sha256=row.manifest_sha256,
                        cutoff_at=_as_utc(row.cutoff_at),
                        evidence_count=await self._evidence_count(session, row.corpus_release_id),
                        index_status=row.index_status,
                        validated_at=_as_utc(row.validated_at),
                        activated_at=_as_utc(row.activated_at),
                        activated_by=row.activated_by,
                    )
                    sources = await self._catalogue_sources(session, row.corpus_release_id)
            trust_roots = await self._catalogue_trust_roots(session)
            return CorpusCatalogue(release=release_block, sources=sources, trust_roots=trust_roots)

    async def _catalogue_sources(
        self, session, corpus_release_id: str
    ) -> list[CorpusCatalogueSource]:
        counted = await session.execute(
            select(
                CanonicalEvidenceRow.source_id,
                CanonicalEvidenceRow.source_version_id,
                func.count().label("evidence_count"),
            )
            .join(
                CorpusReleaseEvidenceRow,
                CorpusReleaseEvidenceRow.evidence_id == CanonicalEvidenceRow.evidence_id,
            )
            .where(CorpusReleaseEvidenceRow.corpus_release_id == corpus_release_id)
            .group_by(CanonicalEvidenceRow.source_id, CanonicalEvidenceRow.source_version_id)
        )
        by_version: dict[tuple[str, str], int] = {
            (source_id, version_id): int(count) for source_id, version_id, count in counted
        }
        version_ids = sorted({version_id for _, version_id in by_version})
        page_counts: dict[str, int] = {}
        if version_ids:
            paged = await session.execute(
                select(AcquisitionRow.source_version_id, func.max(ExtractionRunRow.page_count))
                .join(
                    ExtractionRunRow,
                    ExtractionRunRow.acquisition_id == AcquisitionRow.acquisition_id,
                )
                .where(AcquisitionRow.source_version_id.in_(version_ids))
                .group_by(AcquisitionRow.source_version_id)
            )
            page_counts = {
                version_id: int(pages) for version_id, pages in paged if pages is not None
            }

        sources: list[CorpusCatalogueSource] = []
        for source_id in sorted({source_id for source_id, _ in by_version}):
            source = await session.get(SourceRow, source_id)
            if source is None:
                continue
            publisher = await session.get(PublisherRow, source.publisher_id)
            versions: list[CorpusCatalogueVersion] = []
            for (candidate_source, version_id), count in sorted(by_version.items()):
                if candidate_source != source_id:
                    continue
                version = await session.get(SourceVersionRow, version_id)
                if version is None:
                    continue
                versions.append(
                    CorpusCatalogueVersion(
                        source_version_id=version.source_version_id,
                        version_label=version.version_label,
                        status=version.status,
                        effective_from=version.effective_from,
                        effective_to=version.effective_to,
                        approved_for_retrieval=version.approved_for_retrieval,
                        evidence_count=count,
                        page_count=page_counts.get(version_id),
                    )
                )
            sources.append(
                CorpusCatalogueSource(
                    source_id=source.source_id,
                    title=source.title,
                    publisher_id=source.publisher_id,
                    publisher_name=publisher.name if publisher is not None else source.publisher_id,
                    source_class=source.source_class,
                    jurisdiction=source.jurisdiction,
                    canonical_url=source.canonical_url,
                    license_excerpt_allowed=source.license_excerpt_allowed,
                    license_render_allowed=source.license_render_allowed,
                    versions=versions,
                )
            )
        return sources

    @staticmethod
    async def _catalogue_trust_roots(session) -> list[CorpusTrustRoot]:
        rows = (
            await session.execute(select(TrustRootRow).order_by(TrustRootRow.trust_root_id))
        ).scalars()
        roots: list[CorpusTrustRoot] = []
        for row in rows:
            config = row.connector_config or {}
            title = config.get("title")
            scope = config.get("scope_declaration")
            roots.append(
                CorpusTrustRoot(
                    trust_root_id=row.trust_root_id,
                    publisher_id=row.publisher_id,
                    publisher_name=row.publisher_name,
                    title=str(title) if title else None,
                    scope=str(scope)[:400] if scope else None,
                    jurisdictions=list(row.jurisdictions or []),
                    enabled=bool(row.enabled),
                    last_reconciled_at=_as_utc(row.last_reconciled_at),
                )
            )
        return roots

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

        return await self._evidence_details(
            corpus_release_id, evidence_ids, require_activation=True
        )

    async def research_evidence_details(
        self,
        corpus_release_id: str,
        evidence_ids: Collection[str],
    ) -> list[EvidenceDetail]:
        """Resolve evidence for a release being served for research, without activation.

        **This is the only check that is relaxed, and it is relaxed in exactly one
        direction.** The release must still exist and must be `VALIDATED` or `ACTIVE`;
        every per-record guarantee - approval status, digest agreement, publisher and
        jurisdiction agreement, servable lifecycle, and the licence conjunction that
        decides `render_allowed` - is the same code path the activated route uses. What
        this skips is the singleton pointer, because a research deployment deliberately
        has none, and only that.

        Reached solely from `serving_bootstrap`, which stamps every release it serves
        `RESEARCH_UNACTIVATED` so no client can mistake this for the governed route.
        """

        return await self._evidence_details(
            corpus_release_id, evidence_ids, require_activation=False
        )

    async def source_page_artifact(
        self,
        corpus_release_id: str,
        source_id: str,
    ) -> SourcePageArtifact | None:
        """Resolve the source PDF a page image may be rendered from, or None.

        None is the answer to every failure - unknown release, unservable release, a
        source that contributes no approved evidence to it, a source whose licence does
        not permit page reproduction, an artifact the store no longer preserves. The
        caller cannot distinguish them and should not: each one means "no page here", and
        saying which would report on a corpus the caller has not been granted.

        `license_render_allowed` is the whole gate, and it is read here rather than passed
        in. Reproducing a region of a source page is the stricter of the two licence acts
        - see docs/rendering-licence.md - and this is the only route that performs it, so
        the check belongs at the point the bytes are found rather than anywhere a caller
        could forget it. `license_excerpt_allowed` is not consulted: it licenses quoting
        the text, which is a different act and is gated where the text is served.
        """

        async with self._database.session() as session:
            release = await session.get(CorpusReleaseRow, corpus_release_id)
            if release is None or release.state not in (
                ReleaseState.VALIDATED.value,
                ReleaseState.ACTIVE.value,
            ):
                return None
            source = await session.get(SourceRow, source_id)
            if source is None or not source.license_render_allowed:
                return None
            # Membership in the release, established through an approved evidence record
            # that is itself servable. A source known to the registry but absent from -
            # or quarantined out of - the release being served has no page here.
            evidence_row = (
                (
                    await session.execute(
                        select(CanonicalEvidenceRow)
                        .join(
                            CorpusReleaseEvidenceRow,
                            CorpusReleaseEvidenceRow.evidence_id
                            == CanonicalEvidenceRow.evidence_id,
                        )
                        .join(
                            SourceVersionRow,
                            SourceVersionRow.source_version_id
                            == CanonicalEvidenceRow.source_version_id,
                        )
                        .where(
                            CorpusReleaseEvidenceRow.corpus_release_id == corpus_release_id,
                            CanonicalEvidenceRow.source_id == source_id,
                            CanonicalEvidenceRow.approval_status
                            == EvidenceApprovalStatus.APPROVED.value,
                            SourceVersionRow.approved_for_retrieval.is_(True),
                            SourceVersionRow.status.in_(SERVABLE_LIFECYCLE_VALUES),
                        )
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if evidence_row is None:
                return None
            try:
                evidence = CorpusEvidenceRecord.model_validate(evidence_row.payload)
            except (TypeError, ValueError):
                return None
            artifact = await session.scalar(
                select(StewardArtifactRow).where(
                    StewardArtifactRow.sha256 == evidence.source_artifact_sha256
                )
            )
            if artifact is None or artifact.media_type != PDF_MEDIA_TYPE:
                # Only a PDF has pages. The renderer will happily rasterise a spreadsheet
                # into a few hundred invented ones, at a page size and a numbering that
                # exist nowhere in the document and match no citation - a reader shown
                # "page 12 of Annex B" would reasonably believe it. The DAK annexes are
                # XLSX and are cited by table and cell, so they have no page view at all.
                return None
            return SourcePageArtifact(
                source_id=source_id,
                artifact_sha256=artifact.sha256,
                storage_key=artifact.storage_key,
                byte_size=artifact.byte_size,
                media_type=artifact.media_type,
                source_title=source.title,
            )

    async def table_row_neighbourhood(
        self,
        corpus_release_id: str,
        source_id: str,
        table_id: str,
        row_index: int,
        *,
        radius: int,
    ) -> list[TableRowNeighbour]:
        """The rows around an anchored row of one table, as evidence records.

        A spreadsheet row is the unit of evidence for this corpus, and one row read alone
        is frequently not decidable: the row above opens the condition, the row below
        carries the exception. This resolves the immediate neighbourhood so a reader can
        check a cited row against the rows it sits between, without leaving the release
        the answer was served from.

        Nothing here is a new licence act. Every row comes back through
        `_safe_evidence_detail`, so a source whose licence withholds excerpts yields
        neighbours with no text - their addresses, which are not a copyright act, and
        nothing else. Returning an empty list is the answer to every failure, for the
        same reason `source_page_artifact` gives one.
        """

        if radius < 0 or radius > MAX_TABLE_NEIGHBOUR_RADIUS or row_index < 0:
            return []
        lower = max(0, row_index - radius)
        upper = row_index + radius

        async with self._database.session() as session:
            release = await session.get(CorpusReleaseRow, corpus_release_id)
            if release is None or release.state not in (
                ReleaseState.VALIDATED.value,
                ReleaseState.ACTIVE.value,
            ):
                return []

            # Two passes, because the row this record anchors is inside its JSON payload
            # and not a column: there is no portable way to ask the database for "the
            # records anchored within seven rows of this one" without denormalising the
            # anchor list into a table of its own.
            #
            # What the pass below does not do is hydrate. Selecting the joined
            # `SourceVersionRow`, `SourceRow` and `PublisherRow` entities here built four
            # ORM objects for every approved record the source contributes to the release -
            # thousands, for a DAK annex - to keep the handful in range. The servability
            # predicates still run in SQL, so the scan reads exactly the two scalars the
            # filter needs and the joins stay semi-joins.
            scanned = (
                await session.execute(
                    select(CanonicalEvidenceRow.evidence_id, CanonicalEvidenceRow.payload)
                    .join(
                        CorpusReleaseEvidenceRow,
                        CorpusReleaseEvidenceRow.evidence_id == CanonicalEvidenceRow.evidence_id,
                    )
                    .join(
                        SourceVersionRow,
                        SourceVersionRow.source_version_id
                        == CanonicalEvidenceRow.source_version_id,
                    )
                    .where(
                        CorpusReleaseEvidenceRow.corpus_release_id == corpus_release_id,
                        CanonicalEvidenceRow.source_id == source_id,
                        CanonicalEvidenceRow.approval_status
                        == EvidenceApprovalStatus.APPROVED.value,
                        SourceVersionRow.approved_for_retrieval.is_(True),
                        SourceVersionRow.status.in_(SERVABLE_LIFECYCLE_VALUES),
                    )
                )
            ).all()

        row_by_evidence_id: dict[str, int] = {}
        for evidence_id, payload in scanned:
            anchored = _anchored_table_row(payload, table_id)
            if anchored is None or not lower <= anchored <= upper:
                continue
            row_by_evidence_id[evidence_id] = anchored

        if not row_by_evidence_id:
            return []

        # Bounded before asking, not after: `_evidence_details` answers with nothing at all
        # above its cap, and a row carrying an unusual number of records is a reason to
        # show fewer neighbours rather than none. Truncated in reading order so which ones
        # survive is the same on every request.
        in_range = sorted(row_by_evidence_id.items(), key=lambda item: (item[1], item[0]))
        wanted = dict(in_range[:MAX_EVIDENCE_DETAILS_PER_RESULT])

        # Full validation happens here, on the survivors only, through the same path every
        # other served record goes through. A neighbourhood is servable on the research bar
        # for the same reason the passage it surrounds is.
        details = await self._evidence_details(
            corpus_release_id, wanted.keys(), require_activation=False
        )

        neighbours = [
            TableRowNeighbour(
                row_index=wanted[detail.evidence_id],
                is_anchor_row=wanted[detail.evidence_id] == row_index,
                detail=detail,
            )
            for detail in details
            if detail.evidence_id in wanted
        ]
        # Reading order, which for a table is the row order the document has. The
        # evidence ID breaks a tie so two records anchored to one row do not swap
        # places between requests.
        return sorted(neighbours, key=lambda item: (item.row_index, item.detail.evidence_id))

    async def _evidence_details(
        self,
        corpus_release_id: str,
        evidence_ids: Collection[str],
        *,
        require_activation: bool,
    ) -> list[EvidenceDetail]:
        requested_ids = sorted(set(evidence_ids))
        if not requested_ids or len(requested_ids) > MAX_EVIDENCE_DETAILS_PER_RESULT:
            return []

        async with self._database.session() as session:
            release = await session.get(CorpusReleaseRow, corpus_release_id)
            if release is None:
                return []
            if require_activation:
                pointer = await session.get(ActiveCorpusReleaseRow, 1)
                if (
                    pointer is None
                    or pointer.corpus_release_id != corpus_release_id
                    or pointer.manifest_sha256 != release.manifest_sha256
                    or release.state != ReleaseState.ACTIVE.value
                ):
                    return []
            elif release.state not in (
                ReleaseState.VALIDATED.value,
                ReleaseState.ACTIVE.value,
            ):
                # A candidate, rejected, or superseded release is not servable by any
                # route. Research serving lowers the activation bar, not the validation
                # one.
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
                        SourceVersionRow.status.in_(SERVABLE_LIFECYCLE_VALUES),
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
            or source_version.status not in SERVABLE_LIFECYCLE_VALUES
            or evidence.sha256 != evidence_row.evidence_sha256
        ):
            return None

        # Two permissions, not one. Quoting a passage and reproducing a region of the
        # source page are different acts under the licence, and for this corpus they
        # have different answers: WHO's carve-out for figures, tables and maps lands on
        # the page image, and the release is almost entirely table rows. Collapsing them
        # would mean a decision to permit excerpts silently permitting page reproduction
        # too - which is exactly what docs/rendering-licence.md branch A withholds.
        #
        # The per-evidence flag still gates both: a record the corpus itself marks
        # unrenderable is unrenderable however permissive the source licence is.
        # The two permissions are nested, not independent: reproducing a region of the
        # page shows the words on it, so a source whose text may not be quoted cannot
        # have that text reproduced as a picture instead. Deriving the page permission
        # from the excerpt one makes that structural, which is also what keeps
        # `EvidenceDetail`'s own invariant - restricted evidence exposes no exact
        # highlight - impossible to violate from here rather than merely unlikely.
        excerpt_allowed = evidence.render_allowed and source.license_excerpt_allowed
        page_render_allowed = excerpt_allowed and source.license_render_allowed
        locators = [
            EvidenceLocator(
                kind=anchor.kind.value,
                source_uri=anchor.source_uri,
                pdf_page=anchor.pdf_page,
                printed_page=anchor.printed_page,
                bbox=anchor.bbox,
                # An exact highlight draws the region onto the page, so it is the page
                # permission that gates it, never the excerpt permission.
                exact_highlight_available=page_render_allowed and anchor.bbox is not None,
                # A cell address is the whole of what a TABLE_CELL anchor knows. Dropping
                # it here left the client a locator naming a cell it could not identify,
                # which is indistinguishable from a document-scope anchor. It carries no
                # source content, so no licence decision gates it.
                table_id=anchor.table_id,
                row_index=anchor.row_index,
                column_index=anchor.column_index,
            )
            for anchor in evidence.anchors
        ]
        return EvidenceDetail(
            evidence_id=evidence.evidence_id,
            exact_text=evidence.content_exact if excerpt_allowed else None,
            # The canonical corpus contract has evidence roles and no evidence type, so
            # that field stays null until ingestion supplies one. The section path is a
            # different case: the anchors already name the worksheet a table row sits on,
            # so it is derived from them rather than left empty. See `_section_path`.
            evidence_type=None,
            evidence_roles=[role.value for role in evidence.evidence_roles],
            section_path=_section_path(evidence.anchors),
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
            # `EvidenceDetail.render_allowed` is the client's answer to "may I show this
            # passage", which is the excerpt permission. The page permission reaches the
            # client only through `exact_highlight_available` on each locator, because
            # that is the only thing it can license.
            render_allowed=excerpt_allowed,
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
