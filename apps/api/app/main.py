from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, questions
from app.core.config import get_settings
from app.reasoning.question_service import QuestionService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    service = QuestionService(
        approved_corpus_available=settings.approved_corpus_available,
    )
    app.state.question_service = service
    yield
    await service.close()


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
    return application


app = create_app()
