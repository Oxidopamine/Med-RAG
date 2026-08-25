"""Persistence boundary for immutable structured-package processing records."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.corpus_steward.schemas import (
    InventoryItem,
    ReconciliationDisposition,
    ReconciliationReleaseCandidate,
    SourceArtifactReference,
)
from app.corpus_steward.structured_schemas import (
    FHIRResourceIndexEntry,
    NarrativeAuthorityLink,
    StructuredPackageReport,
    StructuredRunState,
)
from app.persistence.database import Database
from app.persistence.models import (
    ReconciliationInventoryItemRow,
    ReconciliationInventoryRow,
    ReconciliationReleaseCandidateRow,
    StewardArtifactRow,
    StructuredFHIRResourceRow,
    StructuredNarrativeLinkRow,
    StructuredPackageRunRow,
)


class StructuredRepositoryError(RuntimeError):
    pass


class StructuredRepositoryNotFoundError(StructuredRepositoryError):
    pass


class StructuredRepositoryConflictError(StructuredRepositoryError):
    pass


@dataclass(frozen=True)
class StructuredSourceContext:
    candidate: ReconciliationReleaseCandidate
    inventory_item: InventoryItem
    source_artifact: SourceArtifactReference
    artifact_storage_key: str


@dataclass(frozen=True)
class StoredStructuredRun:
    state: StructuredRunState
    report: StructuredPackageReport
    report_artifact_sha256: str
    attestation_id: str


class SQLStructuredPackageRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def source_context(
        self, candidate_id: str, *, item_id: str | None = None
    ) -> StructuredSourceContext:
        async with self._database.session() as session:
            candidate_row = await session.get(
                ReconciliationReleaseCandidateRow, candidate_id
            )
            if candidate_row is None:
                raise StructuredRepositoryNotFoundError(
                    f"reconciliation candidate not found: {candidate_id}"
                )
            candidate = ReconciliationReleaseCandidate.model_validate(
                candidate_row.payload
            )
            if (
                candidate.content.candidate_id != candidate_row.candidate_id
                or candidate.candidate_sha256 != candidate_row.candidate_sha256
            ):
                raise StructuredRepositoryError(
                    "stored reconciliation candidate identity is inconsistent"
                )
            sources = tuple(
                source
                for source in candidate.content.source_artifacts
                if item_id is None or source.item_id == item_id
            )
            if not sources:
                requested = item_id or "<only candidate item>"
                raise StructuredRepositoryNotFoundError(
                    f"candidate has no included source artifact for: {requested}"
                )
            if len(sources) != 1:
                raise StructuredRepositoryError(
                    "candidate contains multiple source artifacts; specify --item-id"
                )
            source = sources[0]
            result = await session.execute(
                select(ReconciliationInventoryItemRow, ReconciliationInventoryRow)
                .join(
                    ReconciliationInventoryRow,
                    ReconciliationInventoryRow.inventory_id
                    == ReconciliationInventoryItemRow.inventory_id,
                )
                .where(
                    ReconciliationInventoryRow.job_id == candidate_row.job_id,
                    ReconciliationInventoryItemRow.item_id == source.item_id,
                )
            )
            row = result.one_or_none()
            if row is None:
                raise StructuredRepositoryError(
                    "candidate source is missing from its reconciliation inventory"
                )
            item_row, inventory_row = row
            if (
                inventory_row.trust_root_id != candidate.content.snapshot.trust_root_id
                or item_row.disposition != ReconciliationDisposition.INCLUDED.value
                or item_row.artifact_sha256 != source.artifact_sha256
            ):
                raise StructuredRepositoryError(
                    "candidate source does not match its reconciled inventory row"
                )
            artifact = await session.get(StewardArtifactRow, source.artifact_sha256)
            if artifact is None or artifact.kind != "SOURCE":
                raise StructuredRepositoryError(
                    "candidate source does not reference a preserved source artifact"
                )
            if (
                artifact.byte_size != source.byte_size
                or artifact.media_type != source.media_type
            ):
                raise StructuredRepositoryError(
                    "candidate source artifact metadata is inconsistent"
                )
            return StructuredSourceContext(
                candidate=candidate,
                inventory_item=InventoryItem.model_validate(item_row.item),
                source_artifact=source,
                artifact_storage_key=artifact.storage_key,
            )

    async def existing(
        self,
        *,
        candidate_id: str,
        item_id: str,
        processor_name: str,
        processor_version: str,
    ) -> StoredStructuredRun | None:
        async with self._database.session() as session:
            row = await session.scalar(
                select(StructuredPackageRunRow).where(
                    StructuredPackageRunRow.reconciliation_candidate_id == candidate_id,
                    StructuredPackageRunRow.inventory_item_id == item_id,
                    StructuredPackageRunRow.processor_name == processor_name,
                    StructuredPackageRunRow.processor_version == processor_version,
                )
            )
            if row is None:
                return None
            report = StructuredPackageReport.model_validate(row.report)
            if (
                report.report_sha256 != row.report_sha256
                or report.content.structured_run_id != row.structured_run_id
                or list(report.content.blockers) != row.blockers
            ):
                raise StructuredRepositoryError(
                    "stored structured-package report is inconsistent"
                )
            return StoredStructuredRun(
                state=StructuredRunState(row.state),
                report=report,
                report_artifact_sha256=row.report_artifact_sha256,
                attestation_id=row.attestation_id,
            )

    async def record(
        self,
        *,
        state: StructuredRunState,
        report: StructuredPackageReport,
        report_artifact_sha256: str,
        attestation_id: str,
        resources: tuple[FHIRResourceIndexEntry, ...],
        narrative_links: tuple[NarrativeAuthorityLink, ...],
    ) -> None:
        content = report.content
        expected_state = (
            StructuredRunState.VALIDATED
            if content.promotion_eligible
            else StructuredRunState.BLOCKED
        )
        if state is not expected_state:
            raise StructuredRepositoryError(
                "structured run state does not match its promotion gate"
            )
        if content.resource_count != len(resources):
            raise StructuredRepositoryError(
                "structured report resource count does not match resource rows"
            )
        if tuple(content.narrative_authorities) != narrative_links:
            raise StructuredRepositoryError(
                "structured report narrative links do not match persisted links"
            )
        try:
            async with self._database.session() as session:
                existing = await session.get(
                    StructuredPackageRunRow, content.structured_run_id
                )
                if existing is not None:
                    if existing.report_sha256 != report.report_sha256:
                        raise StructuredRepositoryConflictError(
                            "structured run ID already identifies a different report"
                        )
                    return
                session.add(
                    StructuredPackageRunRow(
                        structured_run_id=content.structured_run_id,
                        reconciliation_candidate_id=(
                            content.reconciliation_candidate_id
                        ),
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
                        resource_count=content.resource_count,
                        blockers=list(content.blockers),
                        completed_at=content.processed_at,
                    )
                )
                await session.flush()
                for resource in resources:
                    session.add(
                        StructuredFHIRResourceRow(
                            structured_run_id=content.structured_run_id,
                            resource_key=resource.resource_key,
                            resource_type=resource.resource_type,
                            resource_id=resource.resource_id,
                            logical_reference=resource.logical_reference,
                            canonical_url=resource.canonical_url,
                            version=resource.version,
                            status=resource.status,
                            experimental=resource.experimental,
                            profiles=list(resource.profiles),
                            member_path=resource.member_path,
                            content_sha256=resource.content_sha256,
                            byte_size=resource.byte_size,
                            narrative_sha256=resource.narrative_sha256,
                            declared_in_implementation_guide=(
                                resource.declared_in_implementation_guide
                            ),
                            is_example=resource.is_example,
                        )
                    )
                for link in narrative_links:
                    session.add(
                        StructuredNarrativeLinkRow(
                            structured_run_id=content.structured_run_id,
                            link_id=link.link_id,
                            title=link.title,
                            url=link.url,
                            identifier=link.identifier,
                            version=link.version,
                            status=link.status.value,
                        )
                    )
        except IntegrityError as error:
            raise StructuredRepositoryConflictError(
                f"structured-package persistence conflict: {error.orig}"
            ) from error
