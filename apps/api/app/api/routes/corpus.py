"""The catalogue: what is served, what it carries, what is registered.

Read-only, and the one route that describes the corpus without reproducing any of it.
"""

from fastapi import APIRouter, Request

from app.corpus.releases import SQLCorpusReleaseRepository
from app.schemas.corpus import CorpusCatalogue

router = APIRouter(prefix="/corpus", tags=["corpus"])


@router.get("", response_model=CorpusCatalogue)
async def get_corpus_catalogue(request: Request) -> CorpusCatalogue:
    try:
        served = await request.app.state.release_provider()
    except Exception:
        served = None
    repository: SQLCorpusReleaseRepository = request.app.state.corpus_releases
    return await repository.catalogue(served)
