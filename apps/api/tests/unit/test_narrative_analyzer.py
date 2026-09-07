"""The narrative document census: what it counts, and what it refuses to open.

The property under test throughout is that `unit_inventory_sha256` is *recomputable*. A
signed census nobody can independently reproduce is a rubber stamp, and the whole reason
docs/narrative-only-materialization.md adds this stage rather than making the structured
report optional is to keep an independent prior statement that materialization must agree
with.
"""

import hashlib

import pymupdf
import pytest

from app.corpus_steward.evidence_extractor import DAKSourceExtractor
from app.corpus_steward.narrative_analyzer import (
    NarrativeAnalysisError,
    analyse_narrative_document,
)
from app.corpus_steward.narrative_extractor import NarrativeSourceExtractor
from app.schemas.corpus import canonical_sha256

SOURCE_URI = "https://guidance.example.test/consolidated-guidelines"


def _pdf(*pages: str, encrypt: bool = False) -> bytes:
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((72, 96), text)
    if encrypt:
        payload = document.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            user_pw="user",
        )
    else:
        payload = document.tobytes()
    document.close()
    return payload


def _recompute(content: bytes, extractor) -> str:
    """The independent path back to `unit_inventory_sha256`, as materialization runs it."""

    extracted = extractor.extract(content, media_type="application/pdf", source_uri=SOURCE_URI)
    pairs = [
        {
            "source_unit_id": unit.source_unit_id,
            "content_sha256": hashlib.sha256(unit.content_exact.encode("utf-8")).hexdigest(),
        }
        for unit in extracted.units
    ]
    return canonical_sha256({"units": pairs})


def _analyse(content: bytes, media_type: str = "application/pdf"):
    return analyse_narrative_document(
        content,
        asset_id="GUIDELINE",
        artifact_sha256=hashlib.sha256(content).hexdigest(),
        media_type=media_type,
        source_uri=SOURCE_URI,
    )


def test_census_counts_units_and_is_independently_recomputable() -> None:
    content = _pdf(
        "Rapid ART initiation should be offered following diagnosis.",
        "Viral load should be measured at six and twelve months.",
    )
    analysis, safety = _analyse(content)

    assert safety.unsafe == ()
    assert analysis.declaration.page_count == 2
    assert analysis.unit_count == 2
    assert analysis.byte_size == len(content)
    assert analysis.unit_inventory_sha256 is not None

    # Exactly what materialization will do: extract, hash, compare.
    assert _recompute(content, NarrativeSourceExtractor()) == analysis.unit_inventory_sha256


def test_a_census_taken_with_the_wrong_extractor_cannot_agree() -> None:
    """The census and materialization must enumerate identically or the check inverts.

    `DAKSourceExtractor` emits one unit per page; the narrative path emits block groups.
    A census taken with the former could never be reproduced by the latter, so the digest
    comparison would fail on every correct run instead of only on a corrupted one. This
    pins the two together: if someone repoints the census, this test says why not.
    """

    content = _pdf(
        "Rapid ART initiation should be offered following diagnosis.",
        "Viral load should be measured at six and twelve months.",
    )
    analysis, _ = _analyse(content)

    assert _recompute(content, DAKSourceExtractor()) != analysis.unit_inventory_sha256


def test_census_is_deterministic_across_runs() -> None:
    content = _pdf("A recommendation.", "Another recommendation.")
    first, _ = _analyse(content)
    second, _ = _analyse(content)
    assert first == second


def test_pages_without_text_are_counted_as_pages_but_not_as_units() -> None:
    """A blank page is part of the document and is not a unit of evidence."""

    content = _pdf("Only this page carries text.", "")
    analysis, _ = _analyse(content)
    assert analysis.declaration.page_count == 2
    assert analysis.unit_count == 1


def test_encrypted_document_is_flagged_and_not_enumerated() -> None:
    """Safety is decided before enumeration, so unsafe bytes are never extracted."""

    analysis, safety = _analyse(_pdf("Confidential guidance.", encrypt=True))
    assert "ENCRYPTED" in safety.unsafe
    assert analysis.declaration.encrypted is True
    # No census over a document that failed safety.
    assert analysis.unit_count == 0
    assert analysis.unit_inventory_sha256 is None


def test_unparseable_bytes_are_refused_with_a_reason_code() -> None:
    with pytest.raises(NarrativeAnalysisError) as error:
        _analyse(b"this is not a pdf at all")
    assert error.value.reason_code == "PDF_OPEN_FAILED"


def test_non_pdf_media_type_is_refused_before_opening() -> None:
    with pytest.raises(NarrativeAnalysisError) as error:
        _analyse(_pdf("Text."), media_type="application/vnd.ms-excel")
    assert error.value.reason_code == "UNSUPPORTED_NARRATIVE_MEDIA_TYPE"


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        # The failure that motivated this test: a loose pattern matched the "by" inside
        # "by-nc" and reported a NonCommercial licence as permissive CC-BY.
        ("CC BY-NC 4.0", "CC-BY-NC-4.0"),
        ("CC BY-NC-ND 4.0", "CC-BY-NC-ND-4.0"),
        ("CC BY-SA 4.0", "CC-BY-SA-4.0"),
        ("CC BY 4.0", "CC-BY-4.0"),
        ("CC BY-NC-SA 3.0 IGO", "CC-BY-NC-SA-3.0-IGO"),
        # The long form WHO IRIS actually writes into dc:rights.
        ("Attribution-NonCommercial-ShareAlike 3.0 IGO", "CC-BY-NC-SA-3.0-IGO"),
        ("Attribution-NonCommercial 4.0 International", "CC-BY-NC-4.0"),
        ("Attribution 4.0 International", "CC-BY-4.0"),
        # Unrecognised statements yield None so LICENSE_POLICY warns rather than
        # comparing against a licence nobody declared.
        ("All rights reserved", None),
        ("Crown copyright", None),
        ("", None),
    ],
)
def test_license_identifier_never_reports_a_restriction_as_permissive(
    statement: str, expected: str | None
) -> None:
    from app.corpus_steward.narrative_analyzer import _license_identifier

    assert _license_identifier(statement) == expected


def test_analysis_carries_no_clinical_text() -> None:
    """The report is a census. Nothing in it may reproduce the source.

    This is what allows the report to be stored and attested without the stage becoming a
    redistribution of a licensed document.
    """

    secret = "Dolutegravir is the preferred first-line anchor drug."
    analysis, _ = _analyse(_pdf(secret))
    serialized = analysis.model_dump_json()
    assert secret not in serialized
    for fragment in ("Dolutegravir", "anchor drug", "preferred first-line"):
        assert fragment not in serialized
