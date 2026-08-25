from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from app.schemas.domain import EvidenceTrustStatus


class PDFExtractionError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ExtractedSpan:
    pdf_page: int
    block_index: int
    line_index: int
    span_index: int
    text_exact: str
    bbox: tuple[float, float, float, float]
    font_name: str | None
    font_size: float | None
    font_flags: int | None


@dataclass(frozen=True)
class ExtractionResult:
    extractor_name: str
    extractor_version: str
    trust_status: EvidenceTrustStatus
    page_count: int
    spans: tuple[ExtractedSpan, ...]
    diagnostics: dict[str, Any]


class PyMuPDFSpanExtractor:
    name = "PyMuPDF"

    def extract(self, path: Path) -> ExtractionResult:
        spans: list[ExtractedSpan] = []
        text_pages = 0
        invalid_bbox_count = 0
        image_only_pages: list[int] = []
        try:
            with pymupdf.open(path) as document:
                page_count = document.page_count
                for page_index, page in enumerate(document):
                    page_has_text = False
                    page_has_images = bool(page.get_images(full=True))
                    page_dict = page.get_text("dict", sort=True)
                    for block_index, block in enumerate(page_dict.get("blocks", [])):
                        if block.get("type") != 0:
                            continue
                        for line_index, line in enumerate(block.get("lines", [])):
                            for span_index, span in enumerate(line.get("spans", [])):
                                text = str(span.get("text", ""))
                                if not text.strip():
                                    continue
                                raw_bbox = span.get("bbox")
                                if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
                                    invalid_bbox_count += 1
                                    continue
                                bbox = tuple(float(value) for value in raw_bbox)
                                if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                                    invalid_bbox_count += 1
                                    continue
                                page_has_text = True
                                spans.append(
                                    ExtractedSpan(
                                        pdf_page=page_index + 1,
                                        block_index=block_index,
                                        line_index=line_index,
                                        span_index=span_index,
                                        text_exact=text,
                                        bbox=bbox,
                                        font_name=(
                                            str(span["font"])
                                            if span.get("font") is not None
                                            else None
                                        ),
                                        font_size=(
                                            float(span["size"])
                                            if span.get("size") is not None
                                            else None
                                        ),
                                        font_flags=(
                                            int(span["flags"])
                                            if span.get("flags") is not None
                                            else None
                                        ),
                                    )
                                )
                    if page_has_text:
                        text_pages += 1
                    elif page_has_images:
                        image_only_pages.append(page_index + 1)
        except Exception as error:
            raise PDFExtractionError(
                "PDF_EXTRACTION_FAILED", "PyMuPDF could not extract the stored artifact"
            ) from error

        if not spans:
            trust_status = EvidenceTrustStatus.QUARANTINED
        elif image_only_pages or invalid_bbox_count:
            trust_status = EvidenceTrustStatus.SUSPECT
        else:
            trust_status = EvidenceTrustStatus.VERIFIED_NATIVE
        return ExtractionResult(
            extractor_name=self.name,
            extractor_version=pymupdf.__version__,
            trust_status=trust_status,
            page_count=page_count,
            spans=tuple(spans),
            diagnostics={
                "text_page_count": text_pages,
                "image_only_pages": image_only_pages,
                "invalid_bbox_count": invalid_bbox_count,
            },
        )
