from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, ingestion, questions
from app.core.config import get_settings
from app.corpus.releases import SQLCorpusReleaseRepository
from app.ingestion.downloader import AsyncPDFDownloader
from app.ingestion.extractor import PyMuPDFSpanExtractor
from app.ingestion.registry import SQLSourceRegistry
from app.ingestion.service import IngestionService
from app.ingestion.storage import ImmutablePDFStore
from app.persistence.database import Database
from app.reasoning.question_service import QuestionService
from app.reasoning.serving_bootstrap import build_serving_runtime


class _ResearchEvidenceDetails:
    """Adapts the release repository to the provider shape, on the research route.

    A named class rather than a lambda so the call site says which of the two resolvers
    is in use, and so nothing can pass the repository itself by accident and get silent
    empty results.
    """

    def __init__(self, releases: SQLCorpusReleaseRepository) -> None:
        self._releases = releases

    async def evidence_details(self, corpus_release_id, evidence_ids):
        return await self._releases.research_evidence_details(corpus_release_id, evidence_ids)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    database = Database(settings.database_url)
    corpus_releases = SQLCorpusReleaseRepository(database)
    # Research serving is off unless asked for, and asking for it does not activate
    # anything: the governed pointer is untouched and the release this serves is stamped
    # RESEARCH_UNACTIVATED all the way to the client. When it is off, the release
    # provider stays the governed one, which returns None until a signed activation
    # exists, and the service abstains with RETRIEVAL_PIPELINE_NOT_CONFIGURED.
    serving = build_serving_runtime(
        settings,
        # The research resolver, not the activated one: an unactivated release has no
        # pointer, and the governed `evidence_details` correctly returns nothing for it.
        # Every per-record guarantee is identical; only the pointer check differs.
        evidence_details_provider=_ResearchEvidenceDetails(corpus_releases),
    )
    service = QuestionService(
        active_release_provider=(
            serving.active_release if serving is not None else corpus_releases.active_release
        ),
        pipeline=serving.pipeline if serving is not None else None,
    )
    ingestion_service = IngestionService(
        registry=SQLSourceRegistry(database),
        downloader=AsyncPDFDownloader(
            max_bytes=settings.ingestion_max_pdf_bytes,
            timeout_seconds=settings.ingestion_request_timeout_seconds,
            allow_private_networks=settings.ingestion_allow_private_networks,
        ),
        store=ImmutablePDFStore(settings.artifact_store_path),
        extractor=PyMuPDFSpanExtractor(),
    )
    app.state.database = database
    app.state.corpus_releases = corpus_releases
    app.state.question_service = service
    app.state.ingestion_service = ingestion_service
    yield
    await service.close()
    await database.close()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        summary="Research-only evidence-gated guideline QA API",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_origin_regex=settings.api_cors_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["*"],
    )
    application.include_router(health.router)
    application.include_router(questions.router, prefix=settings.api_prefix)
    application.include_router(ingestion.router, prefix=settings.api_prefix)
    return application


app = create_app()
