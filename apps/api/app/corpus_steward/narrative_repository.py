"""Persistence boundary for immutable narrative source-analysis records.

The peer of `structured_repository`, and deliberately the same shape: materialization
verifies a narrative report by the same route it verifies a structured one, so the two
stages should not differ in how a stored report is found, checked, or refused.

One difference is load-bearing. `existing()` takes `item_id` and the unique constraint
includes it, because the narrative topology is the multi-item one - a single WHO NCD
candidate carries thirteen guidelines. A lookup keyed on the candidate alone would return
the first document's census as every other document's, silently. That is the defect
already recorded against the structured materializer's run derivation, and it is not
repeated here.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.corpus_steward.narrative_schemas import (
    NarrativeAnalysisReport,
    NarrativeRunState,
)
from app.persistence.database import Database
from app.persistence.models import NarrativeAnalysisRunRow


class NarrativeRepositoryError(RuntimeError):
    pass


class NarrativeRepositoryConflictError(NarrativeRepositoryError):
    pass


@dataclass(frozen=True)
class StoredNarrativeRun:
    state: NarrativeRunState
    report: NarrativeAnalysisReport
    report_artifact_sha256: str
    attestation_id: str


class SQLNarrativeAnalysisRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def existing(
        self,
        *,
        candidate_id: str,
        item_id: str,
        processor_name: str,
        processor_version: str,
    ) -> StoredNarrativeRun | None:
        async with self._database.session() as session:
            row = await session.scalar(
                select(NarrativeAnalysisRunRow).where(
                    NarrativeAnalysisRunRow.reconciliation_candidate_id == candidate_id,
                    NarrativeAnalysisRunRow.inventory_item_id == item_id,
                    NarrativeAnalysisRunRow.processor_name == processor_name,
                    NarrativeAnalysisRunRow.processor_version == processor_version,
                )
            )
            if row is None:
                return None
            report = NarrativeAnalysisReport.model_validate(row.report)
            # The stored JSON and the stored columns are two statements about the same
            # run. If they disagree, one of them was written by something that did not go
            # through this repository, and neither can be trusted as the census
            # materialization will be checked against.
            if (
                report.report_sha256 != row.report_sha256
                or report.content.narrative_run_id != row.narrative_run_id
                or report.content.inventory_item_id != row.inventory_item_id
                or report.content.unit_count_total != row.unit_count_total
                or list(report.content.blockers) != row.blockers
            ):
                raise NarrativeRepositoryError(
                    "stored narrative analysis report is inconsistent"
                )
            return StoredNarrativeRun(
                state=NarrativeRunState(row.state),
                report=report,
                report_artifact_sha256=row.report_artifact_sha256,
                attestation_id=row.attestation_id,
            )

    async def record(
        self,
        *,
        state: NarrativeRunState,
        report: NarrativeAnalysisReport,
        report_artifact_sha256: str,
        attestation_id: str,
    ) -> None:
        content = report.content
        expected_state = (
            NarrativeRunState.VALIDATED
            if content.promotion_eligible
            else NarrativeRunState.BLOCKED
        )
        if state is not expected_state:
            raise NarrativeRepositoryError(
                "narrative run state does not match its promotion gate"
            )
        try:
            async with self._database.session() as session:
                existing = await session.get(
                    NarrativeAnalysisRunRow, content.narrative_run_id
                )
                if existing is not None:
                    if existing.report_sha256 != report.report_sha256:
                        raise NarrativeRepositoryConflictError(
                            "narrative run ID already identifies a different report"
                        )
                    return
                session.add(
                    NarrativeAnalysisRunRow(
                        narrative_run_id=content.narrative_run_id,
                        reconciliation_candidate_id=content.reconciliation_candidate_id,
                        trust_root_id=content.trust_root_id,
                        trust_root_sha256=content.trust_root_sha256,
                        inventory_item_id=content.inventory_item_id,
                        source_artifact_sha256=content.source_artifact_sha256,
                        structured_input_run_id=content.structured_input_run_id,
                        input_closure_sha256=content.input_closure_sha256,
                        processor_name=content.processor_name,
                        processor_version=content.processor_version,
                        state=state.value,
                        report_sha256=report.report_sha256,
                        report=report.model_dump(mode="json"),
                        report_artifact_sha256=report_artifact_sha256,
                        attestation_id=attestation_id,
                        document_count=len(content.documents),
                        unit_count_total=content.unit_count_total,
                        blockers=list(content.blockers),
                        completed_at=content.processed_at,
                    )
                )
        except IntegrityError as error:
            raise NarrativeRepositoryConflictError(
                f"narrative analysis persistence conflict: {error.orig}"
            ) from error
