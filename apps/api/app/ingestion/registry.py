from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.downloader import DownloadedPDF
from app.ingestion.extractor import ExtractionResult
from app.ingestion.storage import StoredArtifact
from app.persistence.database import Database
from app.persistence.models import (
    AcquisitionRow,
    ArtifactRow,
    ExtractedSpanRow,
    ExtractionRunRow,
    PublisherDomainRow,
    PublisherRow,
    QuarantineEventRow,
    SourceRow,
    SourceVersionRow,
)
from app.schemas.domain import EvidenceTrustStatus, SourceClass, SourceStatus, utc_now
from app.schemas.ingestion import (
    AcquisitionOutcome,
    ArtifactRecord,
    ExtractionTrustRecord,
    PublisherCreate,
    PublisherRecord,
    QuarantineRecord,
    SourceCreate,
    SourceRecord,
    SourceVersionCreate,
    SourceVersionRecord,
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class RegistryNotFoundError(KeyError):
    pass


class RegistryConflictError(ValueError):
    pass


class RegistryStateError(ValueError):
    pass


@dataclass(frozen=True)
class AcquisitionContext:
    source_version_id: str
    status: SourceStatus
    allowed_domains: frozenset[str]


class SQLSourceRegistry:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_publisher(self, payload: PublisherCreate) -> PublisherRecord:
        now = utc_now()
        row = PublisherRow(publisher_id=new_id("PUB"), name=payload.name, created_at=now)
        try:
            async with self._database.session() as session:
                session.add(row)
                session.add_all(
                    PublisherDomainRow(domain=domain, publisher_id=row.publisher_id)
                    for domain in sorted(payload.allowed_domains)
                )
        except IntegrityError as error:
            raise RegistryConflictError(
                "publisher name and domains must be unique registry records"
            ) from error
        return PublisherRecord(
            publisher_id=row.publisher_id,
            name=row.name,
            allowed_domains=payload.allowed_domains,
            created_at=now,
        )

    async def get_publisher(self, publisher_id: str) -> PublisherRecord:
        async with self._database.session() as session:
            publisher = await session.get(PublisherRow, publisher_id)
            if publisher is None:
                raise RegistryNotFoundError("publisher not found")
            domains = (
                await session.scalars(
                    select(PublisherDomainRow.domain)
                    .where(PublisherDomainRow.publisher_id == publisher_id)
                    .order_by(PublisherDomainRow.domain)
                )
            ).all()
            return PublisherRecord(
                publisher_id=publisher.publisher_id,
                name=publisher.name,
                allowed_domains=frozenset(domains),
                created_at=publisher.created_at,
            )

    async def create_source(self, payload: SourceCreate) -> SourceRecord:
        now = utc_now()
        row = SourceRow(
            source_id=new_id("SRC"),
            publisher_id=payload.publisher_id,
            title=payload.title,
            source_class=payload.source_class.value,
            jurisdiction=payload.jurisdiction,
            canonical_url=str(payload.canonical_url),
            license_render_allowed=payload.license_render_allowed,
            created_at=now,
        )
        try:
            async with self._database.session() as session:
                if await session.get(PublisherRow, payload.publisher_id) is None:
                    raise RegistryNotFoundError("publisher not found")
                session.add(row)
        except IntegrityError as error:
            raise RegistryConflictError("source registry record conflicts") from error
        return self._source_record(row)

    async def create_source_version(
        self, payload: SourceVersionCreate
    ) -> SourceVersionRecord:
        now = utc_now()
        row = SourceVersionRow(
            source_version_id=new_id("SV"),
            source_id=payload.source_id,
            version_label=payload.version_label,
            status=SourceStatus.DISCOVERED.value,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
            approved_for_retrieval=False,
            created_at=now,
            updated_at=now,
        )
        try:
            async with self._database.session() as session:
                if await session.get(SourceRow, payload.source_id) is None:
                    raise RegistryNotFoundError("source not found")
                session.add(row)
        except IntegrityError as error:
            raise RegistryConflictError(
                "source version label must be unique within a source"
            ) from error
        return self._source_version_record(row)

    async def acquisition_context(self, source_version_id: str) -> AcquisitionContext:
        async with self._database.session() as session:
            result = await session.execute(
                select(SourceVersionRow, SourceRow)
                .join(SourceRow, SourceRow.source_id == SourceVersionRow.source_id)
                .where(SourceVersionRow.source_version_id == source_version_id)
            )
            found = result.one_or_none()
            if found is None:
                raise RegistryNotFoundError("source version not found")
            version, source = found
            domains = (
                await session.scalars(
                    select(PublisherDomainRow.domain).where(
                        PublisherDomainRow.publisher_id == source.publisher_id
                    )
                )
            ).all()
            return AcquisitionContext(
                source_version_id=version.source_version_id,
                status=SourceStatus(version.status),
                allowed_domains=frozenset(domains),
            )

    async def existing_outcome(self, source_version_id: str) -> AcquisitionOutcome | None:
        async with self._database.session() as session:
            version = await session.get(SourceVersionRow, source_version_id)
            if version is None:
                raise RegistryNotFoundError("source version not found")
            acquisition_result = await session.execute(
                select(AcquisitionRow, ArtifactRow, ExtractionRunRow)
                .join(ArtifactRow, ArtifactRow.artifact_id == AcquisitionRow.artifact_id)
                .join(
                    ExtractionRunRow,
                    ExtractionRunRow.acquisition_id == AcquisitionRow.acquisition_id,
                )
                .where(AcquisitionRow.source_version_id == source_version_id)
            )
            acquisition_found = acquisition_result.one_or_none()
            quarantine = await self._latest_open_quarantine(session, source_version_id)
            if acquisition_found is None and quarantine is None:
                return None

            artifact_record = None
            extraction_record = None
            if acquisition_found is not None:
                acquisition, artifact, extraction = acquisition_found
                artifact_record = self._artifact_record(acquisition, artifact)
                extraction_record = self._extraction_record(extraction)
            return AcquisitionOutcome(
                source_version_id=source_version_id,
                status=SourceStatus(version.status),
                artifact=artifact_record,
                extraction=extraction_record,
                quarantine=self._quarantine_record(quarantine) if quarantine else None,
            )

    async def record_ingestion(
        self,
        *,
        source_version_id: str,
        download: DownloadedPDF,
        stored: StoredArtifact,
        extraction: ExtractionResult,
        expected_sha256: str | None,
    ) -> AcquisitionOutcome:
        now = utc_now()
        async with self._database.session() as session:
            version = await session.get(SourceVersionRow, source_version_id)
            if version is None:
                raise RegistryNotFoundError("source version not found")
            existing_acquisition = await session.scalar(
                select(AcquisitionRow).where(
                    AcquisitionRow.source_version_id == source_version_id
                )
            )
            if existing_acquisition is not None:
                raise RegistryConflictError("source version already has an immutable acquisition")

            artifact = await session.scalar(
                select(ArtifactRow).where(ArtifactRow.sha256 == stored.sha256)
            )
            if artifact is None:
                artifact = ArtifactRow(
                    artifact_id=new_id("ART"),
                    sha256=stored.sha256,
                    byte_size=stored.byte_size,
                    media_type="application/pdf",
                    storage_key=stored.storage_key,
                    created_at=now,
                )
                session.add(artifact)
                await session.flush()

            acquisition = AcquisitionRow(
                acquisition_id=new_id("ACQ"),
                source_version_id=source_version_id,
                artifact_id=artifact.artifact_id,
                requested_url=download.requested_url,
                final_url=download.final_url,
                publisher_domain=download.publisher_domain,
                expected_sha256=expected_sha256,
                http_etag=download.etag,
                http_last_modified=download.last_modified,
                acquired_at=now,
            )
            session.add(acquisition)
            await session.flush()

            run = ExtractionRunRow(
                extraction_run_id=new_id("EXT"),
                acquisition_id=acquisition.acquisition_id,
                extractor_name=extraction.extractor_name,
                extractor_version=extraction.extractor_version,
                trust_status=extraction.trust_status.value,
                page_count=extraction.page_count,
                span_count=len(extraction.spans),
                diagnostics=extraction.diagnostics,
                created_at=now,
            )
            session.add(run)
            session.add_all(
                ExtractedSpanRow(
                    extracted_span_id=new_id("SPAN"),
                    extraction_run_id=run.extraction_run_id,
                    pdf_page=span.pdf_page,
                    block_index=span.block_index,
                    line_index=span.line_index,
                    span_index=span.span_index,
                    text_exact=span.text_exact,
                    bbox_left=span.bbox[0],
                    bbox_top=span.bbox[1],
                    bbox_right=span.bbox[2],
                    bbox_bottom=span.bbox[3],
                    font_name=span.font_name,
                    font_size=span.font_size,
                    font_flags=span.font_flags,
                )
                for span in extraction.spans
            )

            quarantine = None
            if extraction.trust_status is EvidenceTrustStatus.QUARANTINED:
                version.status = SourceStatus.QUARANTINED.value
                quarantine = QuarantineEventRow(
                    quarantine_event_id=new_id("QUAR"),
                    source_version_id=source_version_id,
                    acquisition_url=download.final_url,
                    reason_code="NO_EXTRACTABLE_TEXT",
                    details=extraction.diagnostics,
                    created_at=now,
                )
                session.add(quarantine)
            else:
                version.status = SourceStatus.QA_REQUIRED.value
            version.approved_for_retrieval = False
            version.updated_at = now

            return AcquisitionOutcome(
                source_version_id=source_version_id,
                status=SourceStatus(version.status),
                artifact=self._artifact_record(acquisition, artifact),
                extraction=self._extraction_record(run),
                quarantine=self._quarantine_record(quarantine) if quarantine else None,
            )

    async def quarantine(
        self,
        *,
        source_version_id: str,
        acquisition_url: str | None,
        reason_code: str,
        details: dict[str, object] | None = None,
    ) -> AcquisitionOutcome:
        now = utc_now()
        async with self._database.session() as session:
            version = await session.get(SourceVersionRow, source_version_id)
            if version is None:
                raise RegistryNotFoundError("source version not found")
            existing = await self._latest_open_quarantine(session, source_version_id)
            if existing is None:
                existing = QuarantineEventRow(
                    quarantine_event_id=new_id("QUAR"),
                    source_version_id=source_version_id,
                    acquisition_url=acquisition_url,
                    reason_code=reason_code,
                    details=details or {},
                    created_at=now,
                )
                session.add(existing)
            version.status = SourceStatus.QUARANTINED.value
            version.approved_for_retrieval = False
            version.updated_at = now
            return AcquisitionOutcome(
                source_version_id=source_version_id,
                status=SourceStatus.QUARANTINED,
                quarantine=self._quarantine_record(existing),
            )

    async def list_open_quarantine(self) -> list[QuarantineRecord]:
        async with self._database.session() as session:
            rows = (
                await session.scalars(
                    select(QuarantineEventRow)
                    .where(QuarantineEventRow.resolved_at.is_(None))
                    .order_by(QuarantineEventRow.created_at)
                )
            ).all()
            return [self._quarantine_record(row) for row in rows]

    async def resolve_quarantine(
        self,
        quarantine_event_id: str,
        *,
        resolved_by: str,
        note: str,
    ) -> QuarantineRecord:
        now = utc_now()
        async with self._database.session() as session:
            event = await session.get(QuarantineEventRow, quarantine_event_id)
            if event is None:
                raise RegistryNotFoundError("quarantine event not found")
            if event.resolved_at is not None:
                raise RegistryStateError("quarantine event is already resolved")
            event.resolved_at = now
            event.resolved_by = resolved_by
            event.resolution_note = note
            version = await session.get(SourceVersionRow, event.source_version_id)
            if version is None:
                raise RegistryNotFoundError("source version not found")
            has_acquisition = await session.scalar(
                select(AcquisitionRow.acquisition_id).where(
                    AcquisitionRow.source_version_id == event.source_version_id
                )
            )
            version.status = (
                SourceStatus.QA_REQUIRED.value
                if has_acquisition is not None
                else SourceStatus.DISCOVERED.value
            )
            version.approved_for_retrieval = False
            version.updated_at = now
            return self._quarantine_record(event)

    @staticmethod
    async def _latest_open_quarantine(
        session: AsyncSession, source_version_id: str
    ) -> QuarantineEventRow | None:
        return await session.scalar(
            select(QuarantineEventRow)
            .where(
                QuarantineEventRow.source_version_id == source_version_id,
                QuarantineEventRow.resolved_at.is_(None),
            )
            .order_by(QuarantineEventRow.created_at.desc())
            .limit(1)
        )

    @staticmethod
    def _source_record(row: SourceRow) -> SourceRecord:
        return SourceRecord(
            source_id=row.source_id,
            publisher_id=row.publisher_id,
            title=row.title,
            source_class=SourceClass(row.source_class),
            jurisdiction=row.jurisdiction,
            canonical_url=row.canonical_url,
            license_render_allowed=row.license_render_allowed,
            created_at=row.created_at,
        )

    @staticmethod
    def _source_version_record(row: SourceVersionRow) -> SourceVersionRecord:
        return SourceVersionRecord(
            source_version_id=row.source_version_id,
            source_id=row.source_id,
            version_label=row.version_label,
            status=SourceStatus(row.status),
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            approved_for_retrieval=row.approved_for_retrieval,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _artifact_record(acquisition: AcquisitionRow, artifact: ArtifactRow) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=artifact.artifact_id,
            sha256=artifact.sha256,
            byte_size=artifact.byte_size,
            media_type="application/pdf",
            storage_key=artifact.storage_key,
            requested_url=acquisition.requested_url,
            final_url=acquisition.final_url,
            publisher_domain=acquisition.publisher_domain,
            acquired_at=acquisition.acquired_at,
        )

    @staticmethod
    def _extraction_record(run: ExtractionRunRow) -> ExtractionTrustRecord:
        return ExtractionTrustRecord(
            extraction_run_id=run.extraction_run_id,
            extractor_name=run.extractor_name,
            extractor_version=run.extractor_version,
            trust_status=EvidenceTrustStatus(run.trust_status),
            page_count=run.page_count,
            span_count=run.span_count,
            diagnostics=run.diagnostics,
            created_at=run.created_at,
        )

    @staticmethod
    def _quarantine_record(row: QuarantineEventRow) -> QuarantineRecord:
        return QuarantineRecord(
            quarantine_event_id=row.quarantine_event_id,
            source_version_id=row.source_version_id,
            acquisition_url=row.acquisition_url,
            reason_code=row.reason_code,
            details=row.details,
            created_at=row.created_at,
            resolved_at=row.resolved_at,
            resolved_by=row.resolved_by,
            resolution_note=row.resolution_note,
        )
