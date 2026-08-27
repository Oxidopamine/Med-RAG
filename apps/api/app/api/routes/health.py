from fastapi import APIRouter, Request

from app.schemas.corpus import ReleaseServingMode

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> dict[str, str | bool | None]:
    """Report the release the question service will actually answer from.

    Deliberately not the governed pointer. Under research serving the pointer is empty
    by design while the service answers from a validated release, so reading the pointer
    here would tell a client that answers will be withheld while answers are being
    produced. That is a worse error than the one it replaced: the interface would be
    wrong about the system in the direction of understating what it is doing.

    `approved_corpus_available` stays strictly about *approval*, so a research release
    reports false - it has not passed activation. `serving_mode` is what distinguishes
    "nothing will be answered" from "answers will come from a release that is not
    clinically accepted", and a client that shows one message for both is wrong.
    """

    registry_available = True
    try:
        release = await request.app.state.release_provider()
    except Exception:
        registry_available = False
        release = None

    serving_mode = release.serving_mode if release is not None else None
    return {
        "status": "corpus-release-foundation-ready",
        "corpus_registry_available": registry_available,
        "approved_corpus_available": serving_mode is ReleaseServingMode.ACTIVATED,
        "corpus_release_id": (release.corpus_release_id if release is not None else None),
        "serving_mode": serving_mode.value if serving_mode is not None else None,
        # Never true from this endpoint. Clinical acceptance is not a runtime property.
        "clinical_use_allowed": False,
    }
