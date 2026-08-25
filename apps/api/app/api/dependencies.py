import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from app.core.config import Settings, get_settings
from app.ingestion.service import IngestionService
from app.reasoning.question_service import QuestionService

ingestion_key_header = APIKeyHeader(name="X-Ingestion-Key", auto_error=False)


def get_question_service(request: Request) -> QuestionService:
    return request.app.state.question_service


def get_ingestion_service(request: Request) -> IngestionService:
    return request.app.state.ingestion_service


async def require_ingestion_auth(
    settings: Annotated[Settings, Depends(get_settings)],
    ingestion_key: Annotated[str | None, Depends(ingestion_key_header)],
) -> None:
    if settings.ingestion_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ingestion API is disabled until INGESTION_API_KEY is configured",
        )
    if ingestion_key is None or not secrets.compare_digest(
        ingestion_key, settings.ingestion_api_key
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid ingestion credentials",
        )
