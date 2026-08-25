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
    qa_run_id: str
    materialized_count: int
    approved_count: int
    quarantined_count: int
    source_classes: dict[str, str]


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

            qa_run = await session.scalar(
                select(CorpusQARunRow).where(
                    CorpusQARunRow.corpus_release_id == corpus_release_id
                )
            )
            if qa_run is None or qa_run.state != "VALIDATED":
                raise IndexBasisError("validated QA run is missing for the corpus release")

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
                    .where(EvidenceQADecisionRow.qa_run_id == qa_run.qa_run_id)
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
            if qa_run.bundle_sha256 != bundle_sha256:
                raise IndexBasisError("QA bundle digest does not match registered evidence")
            if qa_run.bundle_artifact_sha256 != bundle_sha256:
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
            if len(decision_rows) != qa_run.evidence_count:
                raise IndexBasisError("QA decision rows do not cover every materialized record")
            if (
                len(approved_decisions) != qa_run.approved_count
                or len(quarantined_decisions) != qa_run.quarantined_count
                or qa_run.approved_count + qa_run.quarantined_count != qa_run.evidence_count
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
                qa_run_id=qa_run.qa_run_id,
                materialized_count=qa_run.evidence_count,
                approved_count=qa_run.approved_count,
                quarantined_count=qa_run.quarantined_count,
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
