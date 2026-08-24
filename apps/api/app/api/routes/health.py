from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(settings: SettingsDependency) -> dict[str, str | bool]:
    return {
        "status": "research-foundation-ready",
        "approved_corpus_available": settings.approved_corpus_available,
        "clinical_use_allowed": False,
    }
