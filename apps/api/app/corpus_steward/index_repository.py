"""Read-only release/QA basis checks used before any Qdrant write."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.persistence.database import Database
from app.persistence.models import (
    CanonicalEvidenceRow,
    CorpusQARunRow,
    CorpusReleaseEvidenceRow,
    CorpusReleaseRow,
    EvidenceQADecisionRow,
    SourceRow,
)
from app.schemas.corpus import (
    CorpusEvidenceRecord,
    CorpusReleaseBundle,
    CorpusReleaseManifest,
    CorpusReleaseRecord,
    EvidenceApprovalStatus,
    ReleaseState,
    canonical_sha256,
)


class IndexBasisError(RuntimeError):
    pass


@dataclass(frozen=True)
class IndexReleaseBasis:
    release: CorpusReleaseRecord
    bundle: CorpusReleaseBundle
    bundle_sha256: str
    qa_run_ids: tuple[str, ...]
    materialized_count: int
    approved_count: int
    quarantined_count: int
    source_classes: dict[str, str]

    @property
    def qa_run_id(self) -> str:
        """The first member run.

        A composite release is QA'd per document, so it has one run per member and no
        single run describes it. Callers that predate composites read this; the full
        membership travels in ``qa_run_ids`` and is what the attestation records.
        """

        return self.qa_run_ids[0]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


class SQLIndexBasisRepository:
    """Reconstruct the registered bundle and reconcile it with the immutable QA ledger."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def load(self, corpus_release_id: str) -> IndexReleaseBasis:
        async with self._database.session() as session:
            release_row = await session.get(CorpusReleaseRow, corpus_release_id)
            if release_row is None:
                raise IndexBasisError(f"corpus release not found: {corpus_release_id}")

            # A composite release is QA'd one document at a time, so it has one VALIDATED
            # run per member. Selecting a single run here silently compared one member's
            # decisions against the whole release and refused every multi-document build.
            qa_runs = (
                await session.scalars(
                    select(CorpusQARunRow)
                    .where(CorpusQARunRow.corpus_release_id == corpus_release_id)
                    .order_by(CorpusQARunRow.qa_run_id)
                )
            ).all()
            if not qa_runs:
                raise IndexBasisError("validated QA run is missing for the corpus release")
            if any(run.state != "VALIDATED" for run in qa_runs):
                raise IndexBasisError("validated QA run is missing for the corpus release")
            qa_run_ids = tuple(run.qa_run_id for run in qa_runs)

            evidence_rows = (
                await session.scalars(
                    select(CanonicalEvidenceRow)
                    .join(
                        CorpusReleaseEvidenceRow,
                        CorpusReleaseEvidenceRow.evidence_id
                        == CanonicalEvidenceRow.evidence_id,
                    )
                    .where(
                        CorpusReleaseEvidenceRow.corpus_release_id == corpus_release_id
                    )
                    .order_by(CanonicalEvidenceRow.evidence_id)
                )
            ).all()
            decision_rows = (
                await session.scalars(
                    select(EvidenceQADecisionRow)
                    .where(EvidenceQADecisionRow.qa_run_id.in_(qa_run_ids))
                    .order_by(EvidenceQADecisionRow.evidence_id)
                )
            ).all()

            source_ids = sorted({row.source_id for row in evidence_rows})
            source_rows = (
                await session.scalars(
                    select(SourceRow)
                    .where(SourceRow.source_id.in_(source_ids))
                    .order_by(SourceRow.source_id)
                )
            ).all()
            source_classes = {row.source_id: row.source_class for row in source_rows}
            if set(source_classes) != set(source_ids):
                raise IndexBasisError("release source-class metadata is incomplete")

            evidence: list[CorpusEvidenceRecord] = []
            for row in evidence_rows:
                try:
                    record = CorpusEvidenceRecord.model_validate(row.payload)
                except (TypeError, ValueError) as error:
                    raise IndexBasisError(
                        f"canonical evidence payload is invalid: {row.evidence_id}"
                    ) from error
                if (
                    row.approval_status != EvidenceApprovalStatus.APPROVED.value
                    or record.verification.approval_status
                    is not EvidenceApprovalStatus.APPROVED
                    or record.corpus_release_id != corpus_release_id
                    or record.evidence_id != row.evidence_id
                    or record.source_id != row.source_id
                    or record.source_version_id != row.source_version_id
                    or record.sha256 != row.evidence_sha256
                ):
                    raise IndexBasisError(
                        f"canonical evidence registry mismatch: {row.evidence_id}"
                    )
                evidence.append(record)

            try:
                manifest = CorpusReleaseManifest.model_validate(release_row.manifest)
                bundle = CorpusReleaseBundle(manifest=manifest, evidence=tuple(evidence))
            except (TypeError, ValueError) as error:
                raise IndexBasisError(
                    "registered corpus bundle is internally inconsistent"
                ) from error

            bundle_sha256 = canonical_sha256(bundle)
            if release_row.manifest_sha256 != bundle.manifest.manifest_sha256:
                raise IndexBasisError("release manifest digest does not match the database")
            # Every member of a composite is promoted against the same assembled bundle,
            # so all of them must agree with it, not merely one.
            if any(run.bundle_sha256 != bundle_sha256 for run in qa_runs):
                raise IndexBasisError("QA bundle digest does not match registered evidence")
            if any(run.bundle_artifact_sha256 != bundle_sha256 for run in qa_runs):
                raise IndexBasisError(
                    "QA bundle artifact digest does not match registered evidence"
                )

            approved_decisions = {
                row.evidence_id for row in decision_rows if row.disposition == "APPROVE"
            }
            quarantined_decisions = {
                row.evidence_id for row in decision_rows if row.disposition == "QUARANTINE"
            }
            if approved_decisions & quarantined_decisions:
                raise IndexBasisError("QA ledger decides an evidence record more than once")
            if approved_decisions != {item.evidence_id for item in bundle.evidence}:
                raise IndexBasisError("approved QA decisions do not match release membership")
            materialized_count = sum(run.evidence_count for run in qa_runs)
            member_approved = sum(run.approved_count for run in qa_runs)
            member_quarantined = sum(run.quarantined_count for run in qa_runs)
            if len(decision_rows) != materialized_count:
                raise IndexBasisError("QA decision rows do not cover every materialized record")
            if (
                len(approved_decisions) != member_approved
                or len(quarantined_decisions) != member_quarantined
                or member_approved + member_quarantined != materialized_count
            ):
                raise IndexBasisError("QA approved/quarantined counts do not reconcile")

            release = CorpusReleaseRecord(
                corpus_release_id=release_row.corpus_release_id,
                contract_version=release_row.contract_version,
                manifest_sha256=release_row.manifest_sha256,
                state=ReleaseState(release_row.state),
                previous_release_id=release_row.previous_release_id,
                qdrant_collection=release_row.qdrant_collection,
                cutoff_at=_as_utc(release_row.cutoff_at),
                evidence_count=len(bundle.evidence),
                index_status=release_row.index_status,
                index_point_count=release_row.index_point_count,
                index_attestation_sha256=release_row.index_attestation_sha256,
                index_validated_at=_as_utc(release_row.index_validated_at),
                validated_at=_as_utc(release_row.validated_at),
                activated_at=_as_utc(release_row.activated_at),
                activated_by=release_row.activated_by,
            )
            return IndexReleaseBasis(
                release=release,
                bundle=bundle,
                bundle_sha256=bundle_sha256,
                qa_run_ids=qa_run_ids,
                materialized_count=materialized_count,
                approved_count=member_approved,
                quarantined_count=member_quarantined,
                source_classes=source_classes,
            )

    @staticmethod
    def require_bundle_match(
        basis: IndexReleaseBasis, supplied: CorpusReleaseBundle
    ) -> None:
        if canonical_sha256(supplied) != basis.bundle_sha256:
            raise IndexBasisError("supplied bundle digest does not match the registered QA bundle")
        if supplied != basis.bundle:
            raise IndexBasisError("supplied bundle content does not match registered evidence")
