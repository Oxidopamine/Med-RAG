import hashlib

import httpx
import pymupdf
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.ingestion.downloader import AsyncPDFDownloader
from app.ingestion.extractor import PyMuPDFSpanExtractor
from app.ingestion.registry import SQLSourceRegistry
from app.ingestion.service import IngestionService
from app.ingestion.storage import ImmutablePDFStore
from app.persistence.database import Database
from app.persistence.models import ArtifactRow, ExtractedSpanRow, SourceVersionRow
from app.schemas.domain import EvidenceTrustStatus, SourceStatus, utc_now
from app.schemas.ingestion import (
    AcquisitionCreate,
    PublisherCreate,
    QuarantineResolution,
    SourceCreate,
    SourceVersionCreate,
)


def make_pdf() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Synthetic guideline source")
    content = document.tobytes()
    document.close()
    return content


async def build_service(tmp_path, content: bytes) -> tuple[Database, IngestionService]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'registry.sqlite3'}")
    await database.create_schema_for_tests()

    async def publisher_response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf", "etag": '"synthetic-v1"'},
            content=content,
            request=request,
        )

    service = IngestionService(
        registry=SQLSourceRegistry(database),
        downloader=AsyncPDFDownloader(
            max_bytes=1024 * 1024,
            timeout_seconds=5,
            allow_private_networks=True,
            transport=httpx.MockTransport(publisher_response),
        ),
        store=ImmutablePDFStore(tmp_path / "artifacts"),
        extractor=PyMuPDFSpanExtractor(),
    )
    return database, service


async def register_version(service: IngestionService) -> str:
    publisher = await service.create_publisher(
        PublisherCreate(
            name="Synthetic Guideline Society",
            allowed_domains=["guidelines.example.org"],
        )
    )
    source = await service.create_source(
        SourceCreate(
            publisher_id=publisher.publisher_id,
            title="Synthetic Guideline",
            source_class="E1",
            jurisdiction="TEST",
            canonical_url="https://guidelines.example.org/guide",
        )
    )
    version = await service.create_source_version(
        SourceVersionCreate(source_id=source.source_id, version_label="2026")
    )
    return version.source_version_id


async def test_successful_ingestion_persists_hash_spans_and_qa_boundary(tmp_path) -> None:
    content = make_pdf()
    database, service = await build_service(tmp_path, content)
    version_id = await register_version(service)

    outcome = await service.acquire(
        version_id,
        AcquisitionCreate(
            url="https://guidelines.example.org/guide.pdf",
            expected_sha256=hashlib.sha256(content).hexdigest(),
        ),
    )

    assert outcome.status is SourceStatus.QA_REQUIRED
    assert outcome.artifact is not None
    assert outcome.artifact.sha256 == hashlib.sha256(content).hexdigest()
    assert outcome.extraction is not None
    assert outcome.extraction.trust_status is EvidenceTrustStatus.VERIFIED_NATIVE
    assert outcome.extraction.span_count > 0
    async with database.session() as session:
        version = await session.get(SourceVersionRow, version_id)
        span_count = await session.scalar(select(func.count()).select_from(ExtractedSpanRow))
    assert version is not None
    assert version.approved_for_retrieval is False
    assert span_count == outcome.extraction.span_count
    await database.close()


async def test_hash_failure_is_quarantined_and_can_be_returned_for_retry(tmp_path) -> None:
    database, service = await build_service(tmp_path, make_pdf())
    version_id = await register_version(service)

    outcome = await service.acquire(
        version_id,
        AcquisitionCreate(
            url="https://guidelines.example.org/guide.pdf",
            expected_sha256="0" * 64,
        ),
    )

    assert outcome.status is SourceStatus.QUARANTINED
    assert outcome.quarantine is not None
    assert outcome.quarantine.reason_code == "SHA256_MISMATCH"
    open_events = await service.list_quarantine()
    assert [event.quarantine_event_id for event in open_events] == [
        outcome.quarantine.quarantine_event_id
    ]

    resolved = await service.resolve_quarantine(
        outcome.quarantine.quarantine_event_id,
        QuarantineResolution(resolved_by="qa-reviewer", note="Retry with corrected digest"),
    )
    assert resolved.resolved_at is not None
    retried = await service.acquire(
        version_id,
        AcquisitionCreate(url="https://guidelines.example.org/guide.pdf"),
    )
    assert retried.status is SourceStatus.QA_REQUIRED
    await database.close()


async def test_database_constraints_reject_noncanonical_artifact_records(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'constraints.sqlite3'}")
    await database.create_schema_for_tests()

    with pytest.raises(IntegrityError):
        async with database.session() as session:
            session.add(
                ArtifactRow(
                    artifact_id="ART_INVALID",
                    sha256="too-short",
                    byte_size=0,
                    media_type="text/html",
                    storage_key="invalid",
                    created_at=utc_now(),
                )
            )

    await database.close()
