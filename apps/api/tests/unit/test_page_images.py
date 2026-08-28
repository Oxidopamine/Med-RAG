import hashlib

import pymupdf
import pytest

from app.corpus.page_images import (
    DEFAULT_RENDER_DPI,
    MAXIMUM_RENDER_DPI,
    MINIMUM_RENDER_DPI,
    PageRenderError,
    render_page,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def write_pdf(path, *, pages: int = 3, width: float = 300, height: float = 400):
    """A small multi-page PDF, so page selection and the page box are both observable."""

    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page(width=width, height=height)
        page.insert_text((40, 60), f"Page {index + 1}")
    document.save(path)
    document.close()
    raw = path.read_bytes()
    return hashlib.sha256(raw).hexdigest(), len(raw)


def test_renders_the_requested_page_and_reports_the_point_space_box(tmp_path) -> None:
    path = tmp_path / "source.pdf"
    digest, size = write_pdf(path)

    rendered = render_page(
        path, expected_sha256=digest, expected_size=size, page_number=2
    )

    assert rendered.png.startswith(PNG_MAGIC)
    assert rendered.page_number == 2
    assert rendered.page_count == 3
    # Points, not pixels: this is the space a PDF anchor's bbox is measured in, and the
    # client divides one by the other to place a region.
    assert (rendered.width, rendered.height) == (300, 400)
    assert rendered.dpi == DEFAULT_RENDER_DPI


def test_higher_dpi_produces_a_larger_image_of_the_same_page(tmp_path) -> None:
    path = tmp_path / "source.pdf"
    digest, size = write_pdf(path)

    low = render_page(
        path, expected_sha256=digest, expected_size=size, page_number=1, dpi=MINIMUM_RENDER_DPI
    )
    high = render_page(
        path, expected_sha256=digest, expected_size=size, page_number=1, dpi=MAXIMUM_RENDER_DPI
    )

    assert len(high.png) > len(low.png)
    # The page box is a property of the document, not of how finely it was rasterised.
    assert (high.width, high.height) == (low.width, low.height)


def test_altered_bytes_are_refused_rather_than_rendered(tmp_path) -> None:
    """The store is immutable by contract; this is what happens if it ever is not.

    Rendering whatever is at the path would serve unknown content under a named
    publisher's source, which is the one outcome worth failing loudly for.
    """

    path = tmp_path / "source.pdf"
    digest, size = write_pdf(path)

    with pytest.raises(PageRenderError, match="digest"):
        render_page(
            path, expected_sha256="0" * 64, expected_size=size, page_number=1
        )
    with pytest.raises(PageRenderError, match="byte size"):
        render_page(
            path, expected_sha256=digest, expected_size=size + 1, page_number=1
        )


def test_pages_outside_the_document_are_refused(tmp_path) -> None:
    path = tmp_path / "source.pdf"
    digest, size = write_pdf(path)

    for page_number in (0, -1, 4):
        with pytest.raises(PageRenderError, match="outside the document"):
            render_page(
                path,
                expected_sha256=digest,
                expected_size=size,
                page_number=page_number,
            )


def test_render_dpi_is_bounded(tmp_path) -> None:
    path = tmp_path / "source.pdf"
    digest, size = write_pdf(path)

    for dpi in (MINIMUM_RENDER_DPI - 1, MAXIMUM_RENDER_DPI + 1):
        with pytest.raises(PageRenderError, match="DPI"):
            render_page(
                path,
                expected_sha256=digest,
                expected_size=size,
                page_number=1,
                dpi=dpi,
            )


def test_a_non_pdf_artifact_is_refused(tmp_path) -> None:
    path = tmp_path / "not-a-pdf.bin"
    path.write_bytes(b"this is not a PDF")
    raw = path.read_bytes()

    with pytest.raises(PageRenderError, match="readable PDF"):
        render_page(
            path,
            expected_sha256=hashlib.sha256(raw).hexdigest(),
            expected_size=len(raw),
            page_number=1,
        )


def test_reports_the_label_the_document_prints_on_the_page(tmp_path) -> None:
    """A printed page number is routinely not the ordinal.

    Front matter runs in roman numerals and an annex can restart at 1, so the number a
    reader will find on the paper is the one worth citing. It is read at render time
    rather than carried in the corpus because it is a property of the artifact being
    opened, and reading it here re-seals nothing.
    """

    path = tmp_path / "labelled.pdf"
    document = pymupdf.open()
    for _ in range(3):
        document.new_page(width=300, height=400)
    document.set_page_labels([{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1}])
    document.save(path)
    document.close()
    raw = path.read_bytes()
    digest, size = hashlib.sha256(raw).hexdigest(), len(raw)

    rendered = render_page(path, expected_sha256=digest, expected_size=size, page_number=3)

    assert rendered.label == "iii"
    # The ordinal is always correct and always reported alongside it.
    assert rendered.page_number == 3


def test_reports_no_label_when_the_document_defines_none(tmp_path) -> None:
    """Most PDFs define no page labels and answer with an empty string.

    Reported as "no label" rather than as an empty one, so a caller never prints a blank
    where a printed page number would go.
    """

    path = tmp_path / "unlabelled.pdf"
    digest, size = write_pdf(path)

    rendered = render_page(path, expected_sha256=digest, expected_size=size, page_number=1)

    assert rendered.label is None
