"""Page images for licence-cleared sources.

This is the only route in the API that reproduces a region of a source page, which is the
stricter of the two licence acts `docs/rendering-licence.md` separates. It is off for
every source whose `license_render_allowed` is false, which today is all of them: branch A
permits quoting a passage and withholds the page image. The route exists so that granting
the page permission is the only remaining step, not so that it has been granted.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.core.config import Settings, get_settings
from app.corpus.page_images import (
    DEFAULT_RENDER_DPI,
    MAXIMUM_RENDER_DPI,
    MINIMUM_RENDER_DPI,
    PNG_MEDIA_TYPE,
    PageRenderError,
    render_page,
)
from app.corpus.releases import (
    MAX_TABLE_NEIGHBOUR_RADIUS,
    SQLCorpusReleaseRepository,
)
from app.schemas.sources import TableRowNeighbour, TableRowNeighbourhood

router = APIRouter(prefix="/sources", tags=["sources"])

SettingsDependency = Annotated[Settings, Depends(get_settings)]

# One body for every refusal. Whether a source is unknown, absent from the release,
# licence-restricted, or missing from the artifact store, the client learns the same
# thing: there is no page here. Distinguishing them would describe a corpus the caller has
# not been granted, and the licence-restricted case is precisely the one worth not
# confirming.
_NO_PAGE = "No page image is available for this source"


@router.get(
    "/{source_id}/pages/{page_number}",
    response_class=Response,
    responses={
        200: {"content": {PNG_MEDIA_TYPE: {}}, "description": "Rendered page image"},
        404: {"description": "No page image is available for this source"},
    },
)
async def get_source_page(
    source_id: str,
    page_number: int,
    request: Request,
    settings: SettingsDependency,
    dpi: int = Query(
        default=DEFAULT_RENDER_DPI, ge=MINIMUM_RENDER_DPI, le=MAXIMUM_RENDER_DPI
    ),
) -> Response:
    release = await request.app.state.release_provider()
    if release is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_PAGE)

    releases: SQLCorpusReleaseRepository = request.app.state.corpus_releases
    artifact = await releases.source_page_artifact(release.corpus_release_id, source_id)
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_PAGE)

    store_root = settings.steward_artifact_store_path
    path = (store_root / artifact.storage_key).resolve()
    if store_root.resolve() not in path.parents:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_PAGE)

    try:
        rendered = render_page(
            path,
            expected_sha256=artifact.artifact_sha256,
            expected_size=artifact.byte_size,
            page_number=page_number,
            dpi=dpi,
        )
    except PageRenderError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_PAGE) from error

    return Response(
        content=rendered.png,
        media_type=PNG_MEDIA_TYPE,
        headers={
            # The artifact is immutable and addressed by digest, so a rendering of one of
            # its pages at a fixed DPI is immutable too. `private` because what may be
            # cached in a reader's browser may not be cached in a shared proxy: this is
            # licensed to the reader looking at it, not to anything in front of them.
            "Cache-Control": "private, max-age=3600",
            "ETag": f'"{artifact.artifact_sha256}-{rendered.page_number}-{rendered.dpi}"',
            # The client scales anchor bboxes, which are in PDF points, against the pixel
            # size of this image. Sending the point-space box saves it a second request
            # and keeps the DPI an implementation detail of this route.
            "X-Page-Width": f"{rendered.width:.4f}",
            "X-Page-Height": f"{rendered.height:.4f}",
            "X-Page-Count": str(rendered.page_count),
            # Only when the document defines one. The ordinal is always correct and always
            # sent; the printed label is the number the reader will find on the paper, and
            # a document that defines none has no such number to report.
            **({"X-Page-Label": rendered.label} if rendered.label else {}),
        },
    )


@router.get(
    "/{source_id}/tables/{table_id}/rows",
    response_model=TableRowNeighbourhood,
)
async def get_table_row_neighbourhood(
    source_id: str,
    table_id: str,
    request: Request,
    row: int = Query(ge=0, description="Zero-based row index to centre the window on"),
    radius: int = Query(default=3, ge=0, le=MAX_TABLE_NEIGHBOUR_RADIUS),
) -> TableRowNeighbourhood:
    """The rows around a cited row of one table.

    A spreadsheet row is this corpus's unit of evidence, and one row read alone is often
    not decidable - the row above opens the condition, the row below carries the
    exception. This is the neighbourhood a reader needs to check a citation, served from
    the same release the answer came from.

    It performs no licence act the answer did not already perform: each row is projected
    through the same path as cited evidence, so a source that may not be excerpted yields
    neighbours with addresses and no text. Every failure answers with an empty window
    rather than a status, on the same grounds as the page route above: which rows a
    release does not contain is a fact about a corpus the caller has not been granted.
    """

    release = await request.app.state.release_provider()
    if release is None:
        return TableRowNeighbourhood(
            source_id=source_id, table_id=table_id, anchor_row_index=row, radius=radius
        )

    releases: SQLCorpusReleaseRepository = request.app.state.corpus_releases
    neighbours = await releases.table_row_neighbourhood(
        release.corpus_release_id, source_id, table_id, row, radius=radius
    )
    return TableRowNeighbourhood(
        source_id=source_id,
        table_id=table_id,
        anchor_row_index=row,
        radius=radius,
        rows=[
            TableRowNeighbour(
                row_index=neighbour.row_index,
                # The corpus records rows zero-based; a spreadsheet numbers them from one.
                # Both are sent so a client never has to know which convention it holds,
                # which is the off-by-one that puts a citation on the wrong row.
                row_number=neighbour.row_index + 1,
                is_anchor_row=neighbour.is_anchor_row,
                evidence=neighbour.detail,
            )
            for neighbour in neighbours
        ],
    )
