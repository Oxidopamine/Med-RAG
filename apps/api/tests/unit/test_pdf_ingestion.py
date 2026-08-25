import hashlib

import pymupdf
import pytest

from app.ingestion.extractor import PyMuPDFSpanExtractor
from app.ingestion.storage import ArtifactValidationError, ImmutablePDFStore
from app.schemas.domain import EvidenceTrustStatus


def make_pdf(text: str | None = "Synthetic recommendation") -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    if text is not None:
        page.insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


def test_artifacts_are_content_addressed_immutable_and_extract_exact_spans(tmp_path) -> None:
    content = make_pdf("Use the synthetic pathway.")
    digest = hashlib.sha256(content).hexdigest()
    store = ImmutablePDFStore(tmp_path / "artifacts")

    first = store.put(content, expected_sha256=digest.upper())
    second = store.put(content)
    result = PyMuPDFSpanExtractor().extract(first.path)

    assert first == second
    assert first.storage_key == f"{digest[:2]}/{digest}.pdf"
    assert result.trust_status is EvidenceTrustStatus.VERIFIED_NATIVE
    assert result.page_count == 1
    assert result.spans[0].text_exact == "Use the synthetic pathway."
    assert result.spans[0].pdf_page == 1
    left, top, right, bottom = result.spans[0].bbox
    assert right > left
    assert bottom > top


def test_textless_pdf_is_quarantined_by_extraction_trust_policy(tmp_path) -> None:
    store = ImmutablePDFStore(tmp_path / "artifacts")
    artifact = store.put(make_pdf(None))

    result = PyMuPDFSpanExtractor().extract(artifact.path)

    assert result.trust_status is EvidenceTrustStatus.QUARANTINED
    assert result.spans == ()


def test_invalid_pdf_and_hash_mismatch_never_enter_storage(tmp_path) -> None:
    store = ImmutablePDFStore(tmp_path / "artifacts")

    with pytest.raises(ArtifactValidationError, match="not a PDF") as invalid:
        store.put(b"not a pdf")
    assert invalid.value.reason_code == "INVALID_PDF_HEADER"

    with pytest.raises(ArtifactValidationError, match="expected SHA-256") as mismatch:
        store.put(make_pdf(), expected_sha256="0" * 64)
    assert mismatch.value.reason_code == "SHA256_MISMATCH"
    assert not list((tmp_path / "artifacts").rglob("*.pdf"))
