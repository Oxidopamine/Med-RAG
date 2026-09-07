"""Block-grouped narrative extraction: what becomes a unit, and what is thrown away.

Two properties carry the weight here. The first is that this extractor is *additive* -
`DAKSourceExtractor` still enumerates PDF pages exactly as it did, because the HIV release
holds `pdf:page:N` units that anchor replay must keep reproducing. The second is that
boilerplate removal is conservative: keeping a running header costs retrieval quality,
while dropping a real line silently removes clinical text from a corpus that claims every
rendered claim is bound to a source, so the tests below push on the *false positive* side.
"""

import pymupdf
import pytest

from app.corpus_steward.evidence_extractor import DAKSourceExtractor
from app.corpus_steward.narrative_extractor import (
    _BOILERPLATE_MAX_CHARS,
    _UNIT_CHAR_BUDGET,
    NarrativeSourceExtractor,
)

SOURCE_URI = "https://guidance.example.test/guideline"


def _pdf(pages: list[list[tuple[float, str]]]) -> bytes:
    """Build a PDF from explicit (y, text) placements so block geometry is controlled.

    Long strings go through `insert_textbox`, which wraps; `insert_text` writes one line
    and the page edge silently truncates it, which would make a "too long to be a header"
    fixture arrive as a short one.
    """

    document = pymupdf.open()
    for placements in pages:
        page = document.new_page()
        for y, text in placements:
            if len(text) > 60:
                page.insert_textbox(
                    pymupdf.Rect(72, y, 523, y + 14 * (len(text) // 60 + 2)),
                    text,
                    fontsize=9,
                )
            else:
                page.insert_text((72, y), text, fontsize=9)
    payload = document.tobytes()
    document.close()
    return payload


def _extract(content: bytes):
    return NarrativeSourceExtractor().extract(
        content, media_type="application/pdf", source_uri=SOURCE_URI
    )


def test_blocks_group_under_the_budget_and_never_cross_a_page() -> None:
    paragraph = "Adults with confirmed hypertension should begin treatment. " * 6
    pages = [[(120.0 + index * 90, f"{index} {paragraph}") for index in range(4)]] * 2
    asset = _extract(_pdf(pages))

    assert len(asset.units) > 2, "a page of four long paragraphs should split into units"
    for unit in asset.units:
        assert {anchor.pdf_page for anchor in unit.anchors} == {
            int(unit.source_unit_id.split(":")[2])
        }, "a unit's anchors must all lie on the page its id names"


def test_a_single_block_larger_than_the_budget_is_never_split() -> None:
    oversized = "clinical guidance text " * 200
    assert len(oversized) > _UNIT_CHAR_BUDGET
    asset = _extract(_pdf([[(120.0, oversized)]]))

    assert len(asset.units) == 1
    assert len(asset.units[0].content_exact) > _UNIT_CHAR_BUDGET
    assert asset.units[0].content_search == oversized.strip()
    assert len(asset.units[0].anchors) == 1


def test_a_running_header_is_dropped_even_though_the_folio_makes_each_one_unique() -> None:
    """The WHO publisher concatenates the page number into the header block.

    Measured on the real 2021 consolidated guidelines: no block text repeats on even 20%
    of pages for exactly this reason, so a rule comparing raw text finds nothing.
    """

    pages = [
        [(20.0, f"{number} Consolidated guidelines on hypertension"), (200.0, f"Body {number}.")]
        for number in range(10, 20)
    ]
    asset = _extract(_pdf(pages))

    assert asset.dropped_boilerplate_blocks == 10
    joined = " ".join(unit.content_exact for unit in asset.units)
    assert "Consolidated guidelines on hypertension" not in joined
    assert "Body 15." in joined


def test_a_recurring_body_heading_survives() -> None:
    """`Background` and `Research gaps` recur throughout a WHO guideline and are content.

    They survive because they flow with the text rather than sitting at a fixed offset,
    and because they are not the first or last block on their page.
    """

    pages = [
        [
            (20.0, f"{number} Consolidated guidelines on hypertension"),
            (120.0 + (number % 4) * 40, "Background"),
            (300.0, f"The evidence for page {number} is summarised here."),
        ]
        for number in range(10, 20)
    ]
    asset = _extract(_pdf(pages))

    joined = " ".join(unit.content_exact for unit in asset.units)
    assert joined.count("Background") == 10
    assert "Consolidated guidelines" not in joined


def test_a_long_margin_block_is_body_text_wherever_it_sits() -> None:
    long_line = "This paragraph opens every page and is far too long to be a header. " * 4
    assert len(long_line) > _BOILERPLATE_MAX_CHARS
    pages = [[(120.0, long_line), (400.0, f"Tail {number}.")] for number in range(6)]
    asset = _extract(_pdf(pages))

    assert asset.dropped_boilerplate_blocks == 0


def test_a_line_appearing_on_too_few_pages_is_kept() -> None:
    pages = [[(20.0, "Rare heading"), (200.0, f"Body {number}.")] for number in range(2)]
    asset = _extract(_pdf(pages))

    assert asset.dropped_boilerplate_blocks == 0
    assert "Rare heading" in " ".join(unit.content_exact for unit in asset.units)


def test_extraction_is_reproducible_because_replay_depends_on_it() -> None:
    content = _pdf(
        [
            [
                (20.0, f"{n} Guideline title"),
                (150.0, f"First paragraph {n}."),
                (300.0, f"Second {n}."),
            ]
            for n in range(10, 18)
        ]
    )
    first = _extract(content)
    second = _extract(content)

    assert [unit.source_unit_id for unit in first.units] == [
        unit.source_unit_id for unit in second.units
    ]
    assert [unit.content_exact for unit in first.units] == [
        unit.content_exact for unit in second.units
    ]
    assert [unit.anchors for unit in first.units] == [unit.anchors for unit in second.units]


def test_a_page_left_empty_by_boilerplate_removal_is_counted_not_dropped() -> None:
    pages = [[(20.0, f"{number} Guideline title")] for number in range(10, 15)]
    asset = _extract(_pdf(pages))

    assert asset.units == ()
    assert asset.expected_source_units == 5
    assert asset.empty_source_units == 5, "coverage accounting must still see every page"


def test_unit_ids_name_a_position_in_the_source_not_in_the_filtered_view() -> None:
    """Block 0 is the dropped header, so the first surviving unit is block 1."""

    pages = [[(20.0, f"{n} Guideline title"), (200.0, f"Body {n}.")] for n in range(10, 16)]
    asset = _extract(_pdf(pages))

    assert asset.units[0].source_unit_id == "pdf:page:1:block:1"


def test_a_roman_numeral_folio_is_masked_like_an_arabic_one() -> None:
    """Front matter is paginated in roman, and a folio is a page number either way.

    Masking only digits left every front-matter folio a unique string that could never
    repeat: measured on the real guideline, 32 of 594 pages kept their running header.
    """

    pages = [
        [(20.0, f"{folio} Consolidated guidelines on hypertension"), (200.0, f"Body {folio}.")]
        for folio in ("vi", "vii", "viii", "ix", "x", "xi")
    ]
    asset = _extract(_pdf(pages))

    joined = " ".join(unit.content_exact for unit in asset.units)
    assert "Consolidated guidelines on hypertension" not in joined
    assert asset.dropped_boilerplate_blocks == 6


def test_a_header_split_across_two_blocks_is_still_a_header() -> None:
    """The folio and the title can be separate blocks.

    An ordinal "first block on the page" test reaches the folio and never the title, so
    margin position also has to be decided by the band the header sits in.
    """

    topics = (
        "blood pressure targets",
        "statin eligibility",
        "renal monitoring",
        "potassium supplementation",
        "adherence support",
        "referral criteria",
        "smoking cessation",
        "dietary sodium",
    )
    pages = [
        [
            (14.0, str(10 + index)),
            (18.0, "Consolidated guidelines on hypertension"),
            (
                300.0,
                f"Guidance on {topic}: adults meeting the diagnostic threshold should "
                f"be assessed and offered treatment appropriate to {topic}, with review "
                "at three months and annually thereafter.",
            ),
        ]
        for index, topic in enumerate(topics)
    ]
    asset = _extract(_pdf(pages))

    joined = " ".join(unit.content_exact for unit in asset.units)
    assert "Consolidated guidelines on hypertension" not in joined
    assert "blood pressure targets" in joined
    assert "dietary sodium" in joined


def test_a_document_with_no_gutter_has_no_header_band() -> None:
    """Without a gap between the top line and the body there is no band, only body text.

    A band inferred from occupancy alone would treat the first line of every page as
    furniture, which on a densely set document is where content starts.
    """

    topics = ("sodium", "potassium", "adherence", "referral", "screening", "review")
    pages = [
        [
            (
                30.0 + offset * 60,
                # Rotated so no page's first or last block repeats another's, which would
                # trip the ordinal margin test and hide what this test is about.
                f"Guidance on {topics[(offset + number) % len(topics)]}: clinicians "
                f"should assess {topics[(offset + number) % len(topics)]} at each visit "
                "and record the finding for review at the next scheduled appointment.",
            )
            for offset in range(6)
        ]
        for number in range(8)
    ]
    asset = _extract(_pdf(pages))

    assert asset.dropped_boilerplate_blocks == 0


def test_coverage_accounts_in_block_groups_not_pages() -> None:
    """A deliberate trade-off, pinned so it cannot drift back silently.

    `AssetCoverage.verify_accounting` requires one evidence record per source unit, which
    block grouping breaks - one page yields several. The schema is frozen by signed
    history, so the count is reported in block groups. The page-level guarantee moves to
    the census digest comparison at materialization, which is a stronger check.
    """

    paragraph = "Guideline recommendation text that fills a block. " * 20
    pages = [[(80.0, paragraph), (400.0, paragraph)] for _ in range(3)]
    asset = _extract(_pdf(pages))

    assert len(asset.units) > 3, "the fixture must actually exercise grouping"
    assert asset.expected_source_units == len(asset.units) + asset.empty_source_units


def test_the_dak_extractor_still_emits_page_units() -> None:
    """The frozen path. HIV evidence records anchor to `pdf:page:N` and must keep doing so."""

    content = _pdf([[(20.0, "Title"), (200.0, "Body one.")], [(200.0, "Body two.")]])
    asset = DAKSourceExtractor().extract(
        content, media_type="application/pdf", source_uri=SOURCE_URI
    )

    assert [unit.source_unit_id for unit in asset.units] == ["pdf:page:1", "pdf:page:2"]
    assert asset.dropped_boilerplate_blocks == 0
    assert "Title" in asset.units[0].content_exact


def test_the_narrative_extractor_refuses_a_spreadsheet() -> None:
    with pytest.raises(Exception) as error:
        NarrativeSourceExtractor().extract(
            b"not a pdf",
            media_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            source_uri=SOURCE_URI,
        )
    assert getattr(error.value, "reason_code", "") == "UNSUPPORTED_NARRATIVE_MEDIA_TYPE"
