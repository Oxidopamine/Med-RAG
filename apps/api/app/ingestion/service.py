from app.ingestion.downloader import AcquisitionDownloadError, AsyncPDFDownloader
from app.ingestion.extractor import PDFExtractionError, PyMuPDFSpanExtractor
from app.ingestion.policy import PublisherDomainPolicy, PublisherPolicyError
from app.ingestion.registry import RegistryStateError, SQLSourceRegistry
from app.ingestion.storage import ArtifactValidationError, ImmutablePDFStore
from app.schemas.domain import SourceStatus
from app.schemas.ingestion import (
    AcquisitionCreate,
    AcquisitionOutcome,
    PublisherCreate,
    PublisherRecord,
    QuarantineRecord,
    QuarantineResolution,
    SourceCreate,
    SourceRecord,
    SourceVersionCreate,
    SourceVersionRecord,
)


class IngestionService:
    def __init__(
        self,
        *,
        registry: SQLSourceRegistry,
        downloader: AsyncPDFDownloader,
        store: ImmutablePDFStore,
        extractor: PyMuPDFSpanExtractor,
    ) -> None:
        self._registry = registry
        self._downloader = downloader
        self._store = store
        self._extractor = extractor

    async def create_publisher(self, payload: PublisherCreate) -> PublisherRecord:
        return await self._registry.create_publisher(payload)

    async def create_source(self, payload: SourceCreate) -> SourceRecord:
        publisher = await self._registry.get_publisher(payload.publisher_id)
        policy = PublisherDomainPolicy(publisher.allowed_domains)
        policy.validate_url(str(payload.canonical_url))
        return await self._registry.create_source(payload)

    async def create_source_version(
        self, payload: SourceVersionCreate
    ) -> SourceVersionRecord:
        return await self._registry.create_source_version(payload)

    async def acquire(
        self, source_version_id: str, payload: AcquisitionCreate
    ) -> AcquisitionOutcome:
        existing = await self._registry.existing_outcome(source_version_id)
        if existing is not None:
            return existing

        context = await self._registry.acquisition_context(source_version_id)
        if context.status is not SourceStatus.DISCOVERED:
            raise RegistryStateError(
                f"source version in {context.status.value} cannot begin acquisition"
            )
        policy = PublisherDomainPolicy(context.allowed_domains)
        requested_url = str(payload.url)
        policy.validate_url(requested_url)
        try:
            download = await self._downloader.download(requested_url, policy)
            stored = self._store.put(
                download.content,
                expected_sha256=payload.expected_sha256,
            )
            extraction = self._extractor.extract(stored.path)
        except (
            AcquisitionDownloadError,
            ArtifactValidationError,
            PDFExtractionError,
            PublisherPolicyError,
        ) as error:
            return await self._registry.quarantine(
                source_version_id=source_version_id,
                acquisition_url=requested_url,
                reason_code=error.reason_code,
                details={"pipeline_stage": self._pipeline_stage(error)},
            )

        return await self._registry.record_ingestion(
            source_version_id=source_version_id,
            download=download,
            stored=stored,
            extraction=extraction,
            expected_sha256=payload.expected_sha256,
        )

    async def list_quarantine(self) -> list[QuarantineRecord]:
        return await self._registry.list_open_quarantine()

    async def resolve_quarantine(
        self, quarantine_event_id: str, payload: QuarantineResolution
    ) -> QuarantineRecord:
        return await self._registry.resolve_quarantine(
            quarantine_event_id,
            resolved_by=payload.resolved_by,
            note=payload.note,
        )

    @staticmethod
    def _pipeline_stage(error: Exception) -> str:
        if isinstance(error, (AcquisitionDownloadError, PublisherPolicyError)):
            return "ACQUISITION"
        if isinstance(error, ArtifactValidationError):
            return "ARTIFACT_VALIDATION"
        return "EXTRACTION"
