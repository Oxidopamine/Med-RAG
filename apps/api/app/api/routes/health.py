from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> dict[str, str | bool | None]:
    registry_available = True
    try:
        active_release = await request.app.state.corpus_releases.active_release()
    except Exception:
        registry_available = False
        active_release = None
    return {
        "status": "corpus-release-foundation-ready",
        "corpus_registry_available": registry_available,
        "approved_corpus_available": active_release is not None,
        "corpus_release_id": (
            active_release.corpus_release_id if active_release is not None else None
        ),
        "clinical_use_allowed": False,
    }
