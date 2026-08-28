"""Render a single page of a source PDF to a PNG, for licence-cleared page reproduction.

Nothing here decides whether a page *may* be reproduced. That is settled before these
functions are reached, by `SQLCorpusReleaseRepository.source_page_artifact`, which will
not hand back an artifact for a source whose licence withholds page rendering. What this
module owes the caller is that a page it does render is the page that was asked for, from
the bytes that were sealed, at a bounded cost.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pymupdf

# A page is rendered to be looked at on a screen, not to be redistributed as a facsimile.
# 144 DPI is two device pixels per PDF point, which is legible on a high-density display
# and still lands a typical page around a few hundred kilobytes.
DEFAULT_RENDER_DPI = 144
MAXIMUM_RENDER_DPI = 300
MINIMUM_RENDER_DPI = 72

PNG_MEDIA_TYPE = "image/png"


class PageRenderError(RuntimeError):
    """The requested page could not be produced from the artifact as sealed."""


# A page label is the number the document prints on the page, which is routinely not its
# ordinal - front matter runs in roman numerals, and an annex can restart at 1. It travels
# as a header rather than in the corpus because it is a property of the artifact being
# rendered, readable at the moment the PDF is open, and reading it here costs nothing and
# re-seals nothing.
MAXIMUM_PAGE_LABEL_LENGTH = 32


@dataclass(frozen=True)
class RenderedPage:
    png: bytes
    page_number: int
    page_count: int
    width: float
    height: float
    dpi: int
    #: The page's printed label, or None when the document defines no page labels.
    label: str | None


def _open_verified(path: Path, *, expected_sha256: str, expected_size: int):
    """Open the artifact only if the bytes on disk are still the sealed bytes.

    The store is immutable by contract, so this should never fire. It is here because the
    alternative to checking is rendering whatever happens to be at that path and serving
    it under a WHO source's name.
    """

    try:
        raw = path.read_bytes()
    except OSError as error:
        raise PageRenderError("source artifact is not readable") from error
    if len(raw) != expected_size:
        raise PageRenderError("source artifact byte size does not match the sealed size")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise PageRenderError("source artifact digest does not match the sealed digest")
    try:
        return pymupdf.open(stream=raw, filetype="pdf")
    except Exception as error:  # pymupdf raises bare exceptions for malformed input
        raise PageRenderError("source artifact is not a readable PDF") from error


def render_page(
    path: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    page_number: int,
    dpi: int = DEFAULT_RENDER_DPI,
) -> RenderedPage:
    """Rasterise one 1-indexed page to PNG.

    `page_number` is 1-indexed because that is what a `PDF` anchor's `pdf_page` carries
    and what a citation prints; converting at the boundary keeps the off-by-one in one
    place instead of at every call site.
    """

    if dpi < MINIMUM_RENDER_DPI or dpi > MAXIMUM_RENDER_DPI:
        raise PageRenderError("requested render DPI is outside the permitted range")
    document = _open_verified(path, expected_sha256=expected_sha256, expected_size=expected_size)
    try:
        if page_number < 1 or page_number > document.page_count:
            raise PageRenderError("requested page is outside the document")
        page = document[page_number - 1]
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        return RenderedPage(
            label=_page_label(page),
            png=pixmap.tobytes("png"),
            page_number=page_number,
            page_count=document.page_count,
            # The unscaled point-space box, which is the space a `PDF` anchor's bbox is
            # expressed in. The client scales the box by the image's pixel size over
            # these, so it never needs to know the DPI this was rendered at.
            width=page.rect.width,
            height=page.rect.height,
            dpi=dpi,
        )
    finally:
        document.close()


def _page_label(page) -> str | None:
    """The printed page label, when the document defines one and it can be sent as one.

    Most PDFs define no page labels at all and answer with an empty string, which is
    reported as "no label" rather than as an empty one. The length and character bounds
    are not cosmetic: this value becomes an HTTP header, and a document is free to label
    a page with something long or non-latin-1 that a header cannot carry. A label that
    cannot travel intact is dropped, because the page ordinal underneath it is already
    correct and a mangled printed page number is worse than none.
    """

    try:
        label = (page.get_label() or "").strip()
    except Exception:  # pymupdf raises bare exceptions for malformed label trees
        return None
    if not label or len(label) > MAXIMUM_PAGE_LABEL_LENGTH:
        return None
    try:
        label.encode("latin-1")
    except UnicodeEncodeError:
        return None
    return label
