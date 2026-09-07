"""Deterministic, source-anchored extraction for narrative guideline PDFs.

## Why this is a separate extractor and not a change to `DAKSourceExtractor`

`QAService._replay` re-runs the extractor over the preserved source bytes and requires,
per `source_unit_id`, that `content_exact`, `content_search` and `anchors` match the
stored evidence record exactly. The extractor's output *is* the anchor-replay property.
Editing `DAKSourceExtractor._pdf` in place would mean the HIV release - whose `MAIN` asset
holds `pdf:page:N` units - could never be replayed again, losing that property
retroactively for records already inside a signed release. So the narrative path gets its
own extractor identity, exactly as it gets its own materializer name, version and run-id
function. See docs/narrative-corpus-composition.md, D2.

## Why units are block groups rather than pages

A page of guideline prose is 1-4 KB spanning several unrelated recommendations, so a page
that matches on one paragraph drags in everything else on it. Blocks are the granularity
PyMuPDF already reports with bounding boxes, so a block group anchors to the region it
actually quotes rather than to a whole page.

Blocks are grouped, never split. A unit boundary always falls on a boundary the extractor
was handed, so no anchor ever covers a region the source did not delimit; a single block
larger than the budget becomes its own oversized unit rather than being cut.

## The boilerplate rule, and why it is shaped the way it is

Measured on the WHO 2021 consolidated HIV guidelines (594 pages, 8,870 text blocks):

- Naive exact-text repetition finds **nothing**. No block text repeats on even 20% of
  pages, because this publisher concatenates the page folio into the running header - the
  first block of a page reads `'118 Consolidated guidelines on HIV prevention, ...'`, so
  every page's header is a distinct string. Folios are therefore masked before comparison.
- With folios masked the header appears, and it **alternates**: the document title on 264
  pages (44%) and the chapter title on the rest, across eight variants. A rule keyed to a
  high whole-document frequency would catch at most two of them, so the threshold is a
  small fraction of pages rather than a majority.
- Genuine section headings that recur - `Background`, `Research gaps`, `Implementation
  considerations` - are *not* boilerplate and must survive. They do because they flow with
  the text rather than sitting in a fixed band (`Background` occurs at 29 different y
  positions).

Two corrections landed after measuring the rule against 165 known recommendations, and
both are worth keeping visible because the first version looked right and was not:

- **Front matter is paginated in roman.** Masking only arabic digits left every
  front-matter folio a unique string that could never repeat, so 32 of 594 pages kept
  their running header. A folio is a page number in whatever numbering scheme the section
  uses.
- **A running header is not always one block.** WHO's front matter puts the folio and the
  title in two, so an ordinal "first block on the page" test reaches the folio and never
  the title. Margin position is therefore a union of the ordinal test and a *band* derived
  from where blocks actually sit - the topmost bins occupied on most pages, terminated by
  a gutter. The union rather than a replacement means nothing the ordinal test caught can
  be lost.

Hence the conjunction below: margin position, positional stability, repetition, and
brevity all have to hold. The asymmetry is deliberate - keeping a boilerplate line costs
retrieval quality, while dropping a real one silently removes clinical text from the
corpus, so every clause is a reason *not* to drop. Measured after both corrections: 11
distinct fingerprints dropped, every one a running header or a bare folio, the longest 102
characters, and all 165 known recommendations still present in the surviving units.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

import pymupdf

from app.corpus_steward.evidence_extractor import (
    PDF_MEDIA_TYPE,
    EvidenceExtractionError,
    ExtractedAsset,
    ExtractedEvidenceUnit,
    _search_view,
)
from app.schemas.corpus import LocatorKind, SourceAnchor, canonical_sha256

NARRATIVE_EXTRACTOR_NAME = "narrative-block-grouped"
NARRATIVE_EXTRACTOR_VERSION = "1.0.0"

# Constants of this extractor version. Changing any of them changes every unit id and
# every anchor, which is a re-materialization - so they are chosen once, by measurement,
# and a change means a version bump rather than a tweak.
#
# _UNIT_CHAR_BUDGET: measured block lengths on the WHO guideline are median 103, p90 485,
# p95 724 characters. A 1,200-character budget yields units of roughly one to four blocks
# without routinely splitting a paragraph away from its heading.
_UNIT_CHAR_BUDGET = 1200
# _BOILERPLATE_PAGE_FRACTION / _BOILERPLATE_MIN_PAGES: the eight header variants on the
# WHO guideline appear on 13-264 pages each; the most frequent genuine heading that could
# be confused with one appears on 10. A 2% floor (12 pages there) separates them with
# margin and scales with document length, and the absolute floor keeps the rule meaningful
# on a short document.
_BOILERPLATE_PAGE_FRACTION = 0.02
_BOILERPLATE_MIN_PAGES = 3
# _BOILERPLATE_MAX_CHARS: a running header is a line, not a paragraph. Anything longer is
# body text wherever it sits.
_BOILERPLATE_MAX_CHARS = 200
# _Y_BIN_POINTS: positional stability tolerance. Header blocks on the WHO guideline sit at
# y=12 on every page; 20 points is wider than any observed jitter and far narrower than
# the 24-point gutter between the header band and the first body block.
_Y_BIN_POINTS = 20.0
# _HEADER_BAND_*: a running header can occupy more than one block - WHO's front matter
# puts the folio and the title in two, so an ordinal 'first block on the page' test
# reaches the folio and never the title. The band is derived from where blocks actually
# sit: the topmost bins occupied on most pages, terminated by a gutter. Without a
# gutter there is no header band, only the top of the body text, and nothing is
# treated as a margin by position.
_HEADER_BAND_MAX_BINS = 2
_HEADER_BAND_MIN_PAGE_FRACTION = 0.5
# _BOILERPLATE_MIN_INFORMATIVE_CHARS: masking digits is what makes a folio-bearing header
# comparable across pages, and it is also the rule's sharpest edge - two short body lines
# differing only by a number share a fingerprint. A candidate must therefore carry enough
# non-digit text to be recognisable as the *same line* rather than the same shape, which
# `Body 13.` does not. The bare folio is the one legitimate exception and is matched
# exactly. No such false positive occurs on the WHO guideline; this is a guard against
# publishers whose page furniture is terser.
_BOILERPLATE_MIN_INFORMATIVE_CHARS = 12

_DIGITS = re.compile(r"\d+")
# A folio is a page number wherever it sits in the numbering scheme. WHO paginates its
# front matter in lower-case roman, so masking only arabic left every front-matter
# folio a unique string that could never repeat - measured: 32 of 594 pages kept their
# running header for exactly this reason. Anchored to the whole token so a word that
# happens to be roman-shaped inside a sentence is untouched.
_ROMAN_FOLIO = re.compile(r"^[ivxlcdm]+$", re.IGNORECASE)
_FOLIO = "#"


@dataclass(frozen=True)
class _Block:
    ordinal: int
    bbox: tuple[float, float, float, float]
    text: str


def _fingerprint(text: str) -> str:
    """Folio-masked, whitespace-normalised, case-folded view for repetition testing."""

    normalized = _DIGITS.sub("#", _search_view(text).casefold())
    return " ".join(
        _FOLIO if _ROMAN_FOLIO.match(token) else token for token in normalized.split(" ")
    )


def _is_recognisable(fingerprint: str) -> bool:
    """Enough non-digit text to identify a repeated line, or a bare page number."""

    if fingerprint == _FOLIO:
        return True
    informative = sum(1 for character in fingerprint if character != _FOLIO)
    return informative >= _BOILERPLATE_MIN_INFORMATIVE_CHARS


def _y_bin(top: float) -> int:
    return int(top // _Y_BIN_POINTS)


class NarrativeSourceExtractor:
    """Block-grouped extraction for narrative PDFs, with running boilerplate removed."""

    name = NARRATIVE_EXTRACTOR_NAME
    version = NARRATIVE_EXTRACTOR_VERSION

    def extract(self, content: bytes, *, media_type: str, source_uri: str) -> ExtractedAsset:
        if media_type != PDF_MEDIA_TYPE:
            raise EvidenceExtractionError(
                "UNSUPPORTED_NARRATIVE_MEDIA_TYPE",
                f"narrative materialization does not support {media_type}",
            )
        try:
            document = pymupdf.open(stream=content, filetype="pdf")
        except Exception as error:  # PyMuPDF exposes several parser exception classes.
            raise EvidenceExtractionError("PDF_OPEN_FAILED", str(error)) from error
        try:
            if document.page_count < 1:
                raise EvidenceExtractionError("PDF_HAS_NO_PAGES", "PDF contains no pages")
            pages = [self._blocks(document, index) for index in range(document.page_count)]
            band = self._header_band(pages)
            boilerplate = self._boilerplate_keys(pages, band)
            units: list[ExtractedEvidenceUnit] = []
            empty = 0
            dropped = 0
            for page_index, blocks in enumerate(pages):
                retained = []
                for position, block in enumerate(blocks):
                    if self._is_boilerplate(block, position, blocks, boilerplate, band):
                        dropped += 1
                        continue
                    retained.append(block)
                if not retained:
                    empty += 1
                    continue
                units.extend(
                    self._group(retained, page_number=page_index + 1, source_uri=source_uri)
                )
            return ExtractedAsset(
                # The unit of account is the *block group*, not the page.
                #
                # `AssetCoverage.verify_accounting` requires
                # `materialized + empty == expected` and `materialized == len(evidence_ids)`,
                # which hard-wires one evidence record per source unit. That holds for a
                # page and for a spreadsheet row; it cannot hold for block groups, because
                # one page yields several. The schema is frozen by signed history and
                # cannot gain a field, so the count is reported in the vocabulary the
                # schema actually uses.
                #
                # What that gives up, stated rather than hidden: this number no longer
                # independently proves every page was reached, because it is derived from
                # the same enumeration it describes. That proof moves to the narrative
                # census - materialization recomputes `unit_inventory_sha256` from the
                # bytes it extracts and refuses to promote when it disagrees with the
                # separately signed prior statement, which is a stronger check than
                # counting was. `empty_source_units` still names pages that produced
                # nothing, so a page lost to over-aggressive boilerplate removal is
                # visible in the report rather than silent.
                expected_source_units=len(units) + empty,
                empty_source_units=empty,
                units=tuple(units),
                dropped_boilerplate_blocks=dropped,
            )
        finally:
            document.close()

    @staticmethod
    def _blocks(document: pymupdf.Document, page_index: int) -> tuple[_Block, ...]:
        page = document.load_page(page_index)
        blocks: list[_Block] = []
        for block in page.get_text("blocks", sort=True):
            text = str(block[4]).strip()
            if not text or int(block[6]) != 0:
                continue
            blocks.append(
                _Block(
                    ordinal=len(blocks),
                    bbox=tuple(float(value) for value in block[:4]),
                    text=text,
                )
            )
        return tuple(blocks)

    @staticmethod
    def _header_band(pages: list[tuple[_Block, ...]]) -> frozenset[int]:
        """The y-bins a running header occupies, or nothing if the document has no band."""

        page_count = len(pages)
        if not page_count:
            return frozenset()
        occupancy: Counter[int] = Counter()
        for blocks in pages:
            # Per page, so one page with many blocks in a bin cannot manufacture a band.
            for bin_index in {_y_bin(block.bbox[1]) for block in blocks}:
                occupancy[bin_index] += 1
        if not occupancy:
            return frozenset()
        floor = _HEADER_BAND_MIN_PAGE_FRACTION * page_count
        # Anchored on the topmost bin that is *populated on most pages*, not on the
        # topmost bin that any block ever touched. A single stray block above the header -
        # a cover mark, one mispositioned figure - would otherwise sit alone in the lowest
        # bin, fail the floor immediately, and disable band detection for the whole
        # document, leaving every running header in the corpus.
        candidates = [index for index, pages_seen in occupancy.items() if pages_seen >= floor]
        if not candidates:
            return frozenset()
        index = min(candidates)
        band: list[int] = []
        while occupancy.get(index, 0) >= floor and len(band) < _HEADER_BAND_MAX_BINS:
            band.append(index)
            index += 1
        if not band or occupancy.get(index, 0) >= floor:
            # Either nothing sits at the top of most pages, or the text runs on with no
            # gutter separating a header from the body. Neither is a band.
            return frozenset()
        return frozenset(band)

    @staticmethod
    def _boilerplate_keys(
        pages: list[tuple[_Block, ...]], band: frozenset[int]
    ) -> frozenset[tuple[str, int]]:
        """Margin-block fingerprints that recur at a stable offset across the document.

        Counted per page rather than per occurrence, so a line appearing twice on one page
        cannot reach the threshold by itself.
        """

        page_count = len(pages)
        threshold = max(
            _BOILERPLATE_MIN_PAGES,
            math.ceil(_BOILERPLATE_PAGE_FRACTION * page_count),
        )
        seen: defaultdict[tuple[str, int], set[int]] = defaultdict(set)
        for page_index, blocks in enumerate(pages):
            for position, block in enumerate(blocks):
                if not _is_margin(position, blocks, band):
                    continue
                if len(block.text) > _BOILERPLATE_MAX_CHARS:
                    continue
                fingerprint = _fingerprint(block.text)
                if not _is_recognisable(fingerprint):
                    continue
                seen[(fingerprint, _y_bin(block.bbox[1]))].add(page_index)
        return frozenset(key for key, hits in seen.items() if len(hits) >= threshold)

    @staticmethod
    def _is_boilerplate(
        block: _Block,
        position: int,
        blocks: tuple[_Block, ...],
        boilerplate: frozenset[tuple[str, int]],
        band: frozenset[int] = frozenset(),
    ) -> bool:
        if not _is_margin(position, blocks, band):
            return False
        if len(block.text) > _BOILERPLATE_MAX_CHARS:
            return False
        return (_fingerprint(block.text), _y_bin(block.bbox[1])) in boilerplate

    @staticmethod
    def _group(
        blocks: list[_Block], *, page_number: int, source_uri: str
    ) -> list[ExtractedEvidenceUnit]:
        units: list[ExtractedEvidenceUnit] = []
        current: list[_Block] = []
        length = 0
        for block in blocks:
            addition = len(block.text) + (2 if current else 0)
            if current and length + addition > _UNIT_CHAR_BUDGET:
                units.append(_unit(current, page_number=page_number, source_uri=source_uri))
                current, length = [], 0
                addition = len(block.text)
            current.append(block)
            length += addition
        if current:
            units.append(_unit(current, page_number=page_number, source_uri=source_uri))
        return units


def _is_margin(
    position: int, blocks: tuple[_Block, ...], band: frozenset[int] = frozenset()
) -> bool:
    """Where running headers and footers land.

    Two tests, deliberately a union rather than a replacement. The ordinal one - first or
    last block in reading order - catches a footer and a single-block header. The band one
    catches every block sitting in the document's header band, which is what a header split
    across a folio block and a title block needs. Widening rather than replacing means
    nothing the ordinal rule already caught can be lost.
    """

    if position == 0 or position == len(blocks) - 1:
        return True
    return _y_bin(blocks[position].bbox[1]) in band


def _unit(blocks: list[_Block], *, page_number: int, source_uri: str) -> ExtractedEvidenceUnit:
    exact = "\n\n".join(block.text for block in blocks)
    return ExtractedEvidenceUnit(
        # The ordinal indexes the page's text blocks in reading order *before* boilerplate
        # removal, so a unit id names a position in the document rather than a position in
        # this extractor's filtered view of it. Replay reproduces both identically; the
        # earlier numbering is simply the one that stays meaningful when read by a human
        # against the source page.
        source_unit_id=f"pdf:page:{page_number}:block:{blocks[0].ordinal}",
        content_exact=exact,
        content_search=_search_view(exact),
        anchors=tuple(
            SourceAnchor(
                kind=LocatorKind.PDF,
                source_uri=source_uri,
                pdf_page=page_number,
                bbox=block.bbox,
            )
            for block in blocks
        ),
    )


def unit_inventory_digest(units: tuple[ExtractedEvidenceUnit, ...]) -> str:
    """The census digest, recomputed from what was actually extracted.

    Must stay byte-identical with `narrative_analyzer`'s construction: this is the whole
    point of the comparison, and two spellings of "the same digest" would make the check
    pass or fail for reasons unrelated to the bytes.
    """

    pairs = [
        {
            "source_unit_id": unit.source_unit_id,
            "content_sha256": hashlib.sha256(unit.content_exact.encode("utf-8")).hexdigest(),
        }
        for unit in units
    ]
    return canonical_sha256({"units": pairs})
