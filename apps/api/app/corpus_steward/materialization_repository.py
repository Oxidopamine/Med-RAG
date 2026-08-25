"""Persistence boundary for immutable Phase 3 materialization outputs."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.corpus_steward.materialization_schemas import (
    MATERIALIZER_NAME,
    MATERIALIZER_VERSION,
    MaterializationReport,
    MaterializationResult,
    MaterializationState,
    MaterializedEvidenceArtifactEntry,
    MaterializedEvidenceRecord,
    SignedAuthorityBinding,
    SignedCorpusReleaseCandidate,
)
from app.persistence.database import Database
from app.persistence.models import (
    MaterializationRunRow,
    MaterializedEvidenceRow,
    StewardArtifactRow,
)


class MaterializationRepositoryError(RuntimeError):
    pass


class MaterializationRepositoryConflictError(MaterializationRepositoryError):
    pass


@dataclass(frozen=True)
class StoredMaterialization:
    state: MaterializationState
    report: MaterializationReport
    corpus_candidate: SignedCorpusReleaseCandidate | None
    report_artifact_sha256: str
    candidate_artifact_sha256: str | None


class SQLMaterializationRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def existing(self, candidate_id: str) -> StoredMaterialization | None:
        async with self._database.session() as session:
            row = await session.scalar(
                select(MaterializationRunRow).where(
                    MaterializationRunRow.reconciliation_candidate_id == candidate_id,
                    MaterializationRunRow.materializer_name == MATERIALIZER_NAME,
                    MaterializationRunRow.materializer_version == MATERIALIZER_VERSION,
                )
            )
            if row is None:
                return None
            report = MaterializationReport.model_validate(row.report)
            candidate = (
                SignedCorpusReleaseCandidate.model_validate(row.corpus_candidate)
                if row.corpus_candidate is not None
                else None
            )
            if (
                report.report_sha256 != row.report_sha256
                or report.content.materialization_run_id != row.materialization_run_id
                or report.content.authority_binding.binding_sha256 != row.authority_binding_sha256
                or len(report.content.evidence) != row.evidence_count
                or list(report.content.blockers) != row.blockers
                or (candidate.candidate_sha256 if candidate else None)
                != row.corpus_candidate_sha256
            ):
                raise MaterializationRepositoryError(
                    "stored materialization output is inconsistent"
                )
            return StoredMaterialization(
                state=MaterializationState(row.state),
                report=report,
                corpus_candidate=candidate,
                report_artifact_sha256=row.report_artifact_sha256,
                candidate_artifact_sha256=row.corpus_candidate_artifact_sha256,
            )

    async def artifact_storage_keys(self, artifact_sha256s: tuple[str, ...]) -> dict[str, str]:
        if not artifact_sha256s:
            return {}
        async with self._database.session() as session:
            result = await session.scalars(
                select(StewardArtifactRow).where(StewardArtifactRow.sha256.in_(artifact_sha256s))
            )
            rows = tuple(result)
            mapping = {row.sha256: row.storage_key for row in rows}
            if set(mapping) != set(artifact_sha256s):
                raise MaterializationRepositoryError(
                    "materialization input artifact is not preserved"
                )
            if any(row.kind != "NARRATIVE_SOURCE" for row in rows):
                raise MaterializationRepositoryError(
                    "evidence materialization accepts only narrative source artifacts"
                )
            return mapping

    async def record(
        self,
        *,
        state: MaterializationState,
        report: MaterializationReport,
        report_artifact_sha256: str,
        authority_binding_artifact_sha256: str,
        input_run_id: str,
        corpus_candidate: SignedCorpusReleaseCandidate | None,
        candidate_artifact_sha256: str | None,
        evidence_artifact_sha256s: dict[str, str],
        evidence_records: tuple[MaterializedEvidenceRecord, ...],
    ) -> None:
        content = report.content
        expected_state = (
            MaterializationState.READY_FOR_QA
            if content.ready_for_qa
            else MaterializationState.BLOCKED
        )
        if state is not expected_state:
            raise MaterializationRepositoryError(
                "materialization state does not match its report gate"
            )
        evidence_ids = {
            item.content.evidence_id
            if isinstance(item, MaterializedEvidenceRecord)
            else item.evidence_id
            for item in content.evidence
        }
        if evidence_ids != set(evidence_artifact_sha256s):
            raise MaterializationRepositoryError(
                "evidence artifact membership differs from the report"
            )
        record_ids = {item.content.evidence_id for item in evidence_records}
        if record_ids != evidence_ids:
            raise MaterializationRepositoryError(
                "materialized records differ from the compact report manifest"
            )
        for entry in content.evidence:
            if isinstance(entry, MaterializedEvidenceArtifactEntry) and (
                evidence_artifact_sha256s[entry.evidence_id] != entry.artifact_sha256
            ):
                raise MaterializationRepositoryError(
                    "compact report artifact digest is inconsistent"
                )
        if (corpus_candidate is None) != (candidate_artifact_sha256 is None):
            raise MaterializationRepositoryError(
                "corpus candidate and artifact must be stored together"
            )
        binding: SignedAuthorityBinding = content.authority_binding
        try:
            async with self._database.session() as session:
                existing = await session.get(MaterializationRunRow, content.materialization_run_id)
                if existing is not None:
                    if existing.report_sha256 != report.report_sha256:
                        raise MaterializationRepositoryConflictError(
                            "materialization run ID identifies a different report"
                        )
                    return
                session.add(
                    MaterializationRunRow(
                        materialization_run_id=content.materialization_run_id,
                        reconciliation_candidate_id=content.reconciliation_candidate_id,
                        input_run_id=input_run_id,
                        structured_run_id=content.structural_mapping.structured_run_id,
                        materializer_name=content.materializer_name,
                        materializer_version=content.materializer_version,
                        state=state.value,
                        authority_binding_sha256=binding.binding_sha256,
                        authority_binding=binding.model_dump(mode="json"),
                        authority_binding_artifact_sha256=(authority_binding_artifact_sha256),
                        authority_attestation_id=binding.attestation.attestation_id,
                        report_sha256=report.report_sha256,
                        report=report.model_dump(mode="json"),
                        report_artifact_sha256=report_artifact_sha256,
                        corpus_candidate_sha256=(
                            corpus_candidate.candidate_sha256
                            if corpus_candidate is not None
                            else None
                        ),
                        corpus_candidate=(
                            corpus_candidate.model_dump(mode="json")
                            if corpus_candidate is not None
                            else None
                        ),
                        corpus_candidate_artifact_sha256=candidate_artifact_sha256,
                        corpus_candidate_attestation_id=(
                            corpus_candidate.attestation.attestation_id
                            if corpus_candidate is not None
                            else None
                        ),
                        evidence_count=len(content.evidence),
                        blockers=list(content.blockers),
                        completed_at=content.completed_at,
                    )
                )
                await session.flush()
                for evidence in evidence_records:
                    item = evidence.content
                    session.add(
                        MaterializedEvidenceRow(
                            materialization_run_id=content.materialization_run_id,
                            evidence_id=item.evidence_id,
                            asset_id=item.asset_id,
                            source_unit_id=item.source_unit_id,
                            source_artifact_sha256=item.source_artifact_sha256,
                            evidence_sha256=evidence.evidence_sha256,
                            payload=evidence.model_dump(mode="json"),
                            artifact_sha256=evidence_artifact_sha256s[item.evidence_id],
                        )
                    )
        except IntegrityError as error:
            raise MaterializationRepositoryConflictError(
                f"materialization persistence conflict: {error.orig}"
            ) from error

    @staticmethod
    def as_result(stored: StoredMaterialization) -> MaterializationResult:
        return MaterializationResult(
            state=stored.state,
            report=stored.report,
            corpus_release_candidate=stored.corpus_candidate,
            report_artifact_sha256=stored.report_artifact_sha256,
            candidate_artifact_sha256=stored.candidate_artifact_sha256,
        )
