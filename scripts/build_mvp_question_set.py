"""Rebuild the MVP coverage question set's sampling frame and seeded draw.

This is a *measurement input* builder, not part of the governed corpus pipeline and not a
benchmark generator. It does not sign, digest-bind, or register anything. See
docs/mvp-definition.md for why the MVP question set deliberately sits outside the frozen
generation policy that governs benchmarks/questions/.

It reproduces two of the three construction steps: extracting the recommendation
population from the parent guideline, and drawing the seeded sample. It does *not*
reproduce the questions themselves - those were authored by instantiating Ely generic
forms and live in the committed JSON, because a question is a judgement rather than a
derivation. Re-running this verifies the frame and the draw a question set claims.

Input document (not committed - 8.4 MB, CC BY-NC-SA 3.0 IGO, fetch it yourself):

    WHO. Consolidated guidelines on HIV prevention, testing, treatment, service delivery
    and monitoring: recommendations for a public health approach. Geneva; July 2021.
    ISBN 9789240031593. https://www.who.int/publications/i/item/9789240031593
    NCBI Bookshelf mirror: https://www.ncbi.nlm.nih.gov/books/n/who342899/pdf/

Usage:

    python scripts/build_mvp_question_set.py --pdf <path-to-2021-consolidated-guidelines.pdf>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF

# "Summary recommendations" runs from printed xv to xlii. Page indices are pinned rather
# than searched: the running header appears on alternate pages only, so a header scan
# silently drops half the section. If a future edition shifts these, the assertion in
# _extract fails loudly rather than returning a short frame.
FIRST_PAGE, LAST_PAGE = 16, 43
EXPECTED_CHAPTERS = {2, 3, 4, 5, 6, 7}

SEED = 20260827
SAMPLE_N = 50
MIN_STATEMENT_CHARS = 120

RUNNING = re.compile(r"^(Summary recommendations|Consolidated guidelines on HIV)")
CHAPTER = re.compile(r"^Chapter (\d+):\s*(.+?)(?:\s*\(continued\))?\s*$")
GRADE = re.compile(r"\((strong|conditional) recommendation[,;]?\s*([^)]*?certainty[^)]*)\)", re.IGNORECASE)
FOLIO = re.compile(r"^[ivxlcIVXLC]+\s+")
BULLET = re.compile(r"^[•●▪\-–]")
SECTION_HEADING = re.compile(r"^\d+\.\d+")
FOOTNOTE = re.compile(r"^[a-z] [A-Z]")
DEONTIC = re.compile(
    r"\b(recommend(s|ed)?|suggest(s|ed)?|should|may be|can be|must|offer(ed)?"
    r"|is preferred|are preferred|initiat\w+|provid\w+)\b",
    re.IGNORECASE,
)


def _extract(pdf: Path) -> list[dict]:
    """Every statement-shaped block in the Summary recommendations section."""
    doc = fitz.open(pdf)
    records: list[dict] = []
    chapter = chapter_no = heading = None

    for index in range(FIRST_PAGE, LAST_PAGE + 1):
        for block in doc[index].get_text("blocks"):
            text = FOLIO.sub("", " ".join(block[4].split()))
            if not text or RUNNING.match(text):
                continue
            match = CHAPTER.match(text)
            if match:
                chapter_no, chapter, heading = int(match.group(1)), match.group(2).strip(), None
                continue
            if len(text) < 80:
                heading = text
                continue
            grade = GRADE.search(text)
            records.append(
                {
                    "id": f"WHO2021-C{chapter_no}-{len(records) + 1:03d}",
                    "chapter_no": chapter_no,
                    "chapter": chapter,
                    "subheading": heading,
                    "pdf_page_index": index,
                    "statement": text,
                    "strength": grade.group(1).lower() if grade else None,
                    "certainty": " ".join(grade.group(2).split()) if grade else None,
                    "kind": "RECOMMENDATION" if grade else "GOOD_PRACTICE_OR_UNGRADED",
                }
            )

    found = {r["chapter_no"] for r in records}
    if found != EXPECTED_CHAPTERS:
        raise SystemExit(
            f"chapter set {sorted(found)} != expected {sorted(EXPECTED_CHAPTERS)}; the page "
            "range no longer covers Summary recommendations - re-derive FIRST_PAGE/LAST_PAGE"
        )
    return records


def _frame(records: list[dict]) -> tuple[list[dict], Counter]:
    """GRADE-rated recommendations only, minus headings, bullets and footnotes.

    Good practice statements are dropped deliberately. They are real guidance, but the
    coverage claim is about the guideline's *formal recommendations* - countable,
    unambiguous, and what a digital adaptation kit operationalizes.
    """
    kept, dropped = [], Counter()
    for record in records:
        statement = record["statement"]
        if record["kind"] != "RECOMMENDATION":
            dropped["not GRADE-rated"] += 1
        elif len(statement) < MIN_STATEMENT_CHARS:
            dropped["too short (heading or orphan rating)"] += 1
        elif BULLET.match(statement):
            dropped["bullet fragment"] += 1
        elif SECTION_HEADING.match(statement):
            dropped["section heading"] += 1
        elif FOOTNOTE.match(statement):
            dropped["footnote"] += 1
        elif not DEONTIC.search(statement):
            dropped["no deontic verb"] += 1
        else:
            kept.append(record)
    return kept, dropped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True, help="2021 consolidated guidelines PDF")
    parser.add_argument(
        "--compare",
        type=Path,
        default=Path("benchmarks/questions/mvp-coverage-who-hiv-v1.json"),
        help="committed question set to verify the rebuilt draw against",
    )
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    records = _extract(args.pdf)
    frame, dropped = _frame(records)
    frame.sort(key=lambda r: r["id"])

    import random

    sample = random.Random(SEED).sample(frame, SAMPLE_N)
    sample.sort(key=lambda r: (r["chapter_no"], r["id"]))

    print(f"extracted statements : {len(records)}")
    print(f"graded recommendations: {sum(1 for r in records if r['kind'] == 'RECOMMENDATION')}")
    print(f"frame size            : {len(frame)}   dropped: {dict(dropped)}")
    print(f"drawn                 : {len(sample)} at seed {SEED}")
    for (number, name), count in sorted(Counter((r["chapter_no"], r["chapter"]) for r in sample).items()):
        print(f"  ch{number} {name[:44]:46s} {count}")

    if args.compare.exists():
        committed = json.loads(args.compare.read_text(encoding="utf-8"))
        expected = [item["recommendation_id"] for item in committed["items"]]
        actual = [r["id"] for r in sample]
        if expected == actual:
            print(f"\nOK: draw reproduces {args.compare}")
        else:
            print(f"\nMISMATCH against {args.compare}")
            print(f"  only in committed: {sorted(set(expected) - set(actual))}")
            print(f"  only in rebuilt  : {sorted(set(actual) - set(expected))}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
