"""Measure F1 and F2 across several WHO Digital Adaptation Kits, not just HIV.

The README reports two structural findings about the WHO SMART HIV DAK:

* **F1** - 40.4% of the release is exact duplicate content that content hashing could not
  see, because the Annex A ``all`` worksheet is a verbatim copy of all thirteen topic
  worksheets with one extra column recording which tab each row came from.
* **F2** - 91.0% of the records carrying the recommendation-bearing role label cannot bear
  a recommendation, because roles are assigned by source *asset* and the data dictionary
  is one asset.

Both are currently facts about one spreadsheet. A reviewer's first question is whether
they describe the DAK *artifact class* or just this file. This measures the same two
properties on every DAK that publishes the same four-annex structure, so the answer is a
table rather than an argument.

## What is measured, and what is deliberately not

Both measures here are **mechanical properties of the published workbooks**. Nothing in
this script classifies content, reads clinical meaning, or runs the serving path:

* *Duplication* is exact row equality after normalization. The aggregate-sheet detector
  looks for the specific shape F1 found - a sheet whose rows reproduce other sheets' rows
  with one extra column - and reports the column that distinguishes it, so a match can be
  checked by eye rather than believed.
* *Recommendation-bearing mass* is a row count across the data-dictionary and
  decision-support workbooks. It is the artifact-level fact underneath F2: a DAK's data
  dictionary is many times larger than its decision-support logic, and a pipeline that
  labels evidence by source asset inherits that ratio as a labelling error. It is **not**
  a re-run of the form classifier, and it does not reproduce the 91.0% figure, which is
  release-specific and depends on what QA approved.

The HIV row is computed the same way as every other row, from the same published annexes,
so the comparison is like for like. It will not exactly equal the release-level numbers in
the README, which are measured post-QA over approved evidence records - a different
denominator, stated here rather than reconciled away.

Usage:

    python scripts/audit_dak_structure.py \\
        --manifest data/local/dak-audit/workbook-manifest.json \\
        --hiv-annex-a data/local/source-documents/WHO_HIV_DAK_2_ANNEX_A.xlsx \\
        --hiv-annex-b data/local/source-documents/WHO_HIV_DAK_2_ANNEX_B.xlsx \\
        --hiv-annex-c data/local/source-documents/WHO_HIV_DAK_2_ANNEX_C.xlsx \\
        --output data/local/dak-audit/structure-report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

# A sheet counts as an aggregate copy of another when it reproduces at least this share of
# that sheet's distinct rows. Not 1.0: a published aggregate can drop a stray blank or
# carry a corrected cell, and a threshold that only fires on perfection would report
# nothing while the reader can plainly see the copy.
AGGREGATE_MATCH_THRESHOLD = 0.90

# Annex roles, keyed by the substring IRIS uses in its own annex titles.
ANNEX_KINDS = {
    "core data dictionary": "DATA_DICTIONARY",
    "decision support logic": "DECISION_SUPPORT",
    "decision-support logic": "DECISION_SUPPORT",
    "indicators table": "INDICATORS",
    "functional and non-functional requirements": "REQUIREMENTS",
}


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def sheet_rows(worksheet: Any) -> list[tuple[str, ...]]:
    """Every non-empty row, as normalized cell values with trailing blanks removed."""

    rows: list[tuple[str, ...]] = []
    for raw in worksheet.iter_rows(values_only=True):
        cells = [_normalize(value) for value in raw]
        while cells and not cells[-1]:
            cells.pop()
        if any(cells):
            rows.append(tuple(cells))
    return rows


def _fingerprint(row: tuple[str, ...]) -> str:
    """Over the *substantive* cells only, which is how the serving layer fingerprints.

    Blank cells are dropped rather than positionally preserved. Without this the same
    logical row normalizes to different lengths in two sheets - a topic sheet trims its
    trailing empties, while the aggregate sheet's extra tab-name cell stops the trim -
    and an exact copy reads as a non-match.
    """

    substantive = [cell for cell in row if cell]
    return hashlib.sha256("\x1f".join(substantive).encode("utf-8")).hexdigest()[:16]


def _drop_one_fingerprints(row: tuple[str, ...]) -> set[str]:
    """Fingerprints of this row with any single cell removed.

    This is what makes the aggregate-sheet test see through the extra column: the `all`
    sheet's row is the topic sheet's row plus one value, so dropping that one value must
    reproduce the original exactly.
    """

    substantive = tuple(cell for cell in row if cell)
    return {
        _fingerprint(substantive[:index] + substantive[index + 1 :])
        for index in range(len(substantive))
    }


def analyse_workbook(path: Path) -> dict[str, Any]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheets: dict[str, list[tuple[str, ...]]] = {}
    for name in workbook.sheetnames:
        sheets[name] = sheet_rows(workbook[name])
    workbook.close()

    per_sheet = {
        name: {
            "rows": len(rows),
            "distinct_rows": len({_fingerprint(row) for row in rows}),
            "columns_max": max((len(row) for row in rows), default=0),
        }
        for name, rows in sheets.items()
    }

    # Which sheets does each candidate aggregate? A sheet is only tested against sheets
    # that are not itself, and only when it is the wider of the two - the copy carries the
    # extra column, so it cannot be narrower than what it copies.
    aggregates: list[dict[str, Any]] = []
    for candidate, candidate_rows in sheets.items():
        if not candidate_rows:
            continue
        reduced: set[str] = set()
        for row in candidate_rows:
            reduced |= _drop_one_fingerprints(row)
        covered: list[dict[str, Any]] = []
        candidate_width = max((len(row) for row in candidate_rows), default=0)
        for other, other_rows in sheets.items():
            if other == candidate or not other_rows:
                continue
            # The copy carries the extra column, so it cannot be narrower than what it
            # copies. Without this the containment relation is tested both ways and mutual
            # pairs appear - A aggregates B *and* B aggregates A - which would make the
            # table claim more than the method supports.
            if candidate_width <= max((len(row) for row in other_rows), default=0):
                continue
            distinct = {_fingerprint(row) for row in other_rows}
            if not distinct:
                continue
            hits = len(distinct & reduced)
            share = hits / len(distinct)
            if share >= AGGREGATE_MATCH_THRESHOLD:
                covered.append(
                    {"sheet": other, "distinct_rows": len(distinct),
                     "reproduced": hits, "share": round(share, 4)}
                )
        if covered:
            aggregates.append(
                {
                    "sheet": candidate,
                    "rows": len(candidate_rows),
                    "aggregates_sheets": sorted(covered, key=lambda item: -item["reproduced"]),
                    "reproduced_rows_total": sum(item["reproduced"] for item in covered),
                }
            )

    total_rows = sum(len(rows) for rows in sheets.values())
    all_fingerprints = Counter(
        _fingerprint(row) for rows in sheets.values() for row in rows
    )
    duplicate_rows = total_rows - len(all_fingerprints)

    return {
        "sheets": len(sheets),
        "rows_total": total_rows,
        "distinct_rows_workbook": len(all_fingerprints),
        "duplicate_rows": duplicate_rows,
        "duplicate_share": round(duplicate_rows / total_rows, 4) if total_rows else None,
        "per_sheet": per_sheet,
        "aggregate_sheets": aggregates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--hiv-annex-a", type=Path, required=True)
    parser.add_argument("--hiv-annex-b", type=Path, required=True)
    parser.add_argument("--hiv-annex-c", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    targets: list[dict[str, Any]] = [
        {"family": "hiv", "kind": "DATA_DICTIONARY", "file": arguments.hiv_annex_a},
        {"family": "hiv", "kind": "DECISION_SUPPORT", "file": arguments.hiv_annex_b},
        {"family": "hiv", "kind": "INDICATORS", "file": arguments.hiv_annex_c},
    ]
    for entry in json.loads(arguments.manifest.read_text(encoding="utf-8")):
        kind = ANNEX_KINDS.get(entry["annex"].strip().casefold())
        if kind is None or kind == "REQUIREMENTS":
            continue
        targets.append({"family": entry["family"], "kind": kind, "file": Path(entry["file"])})

    report: dict[str, Any] = {"workbooks": [], "by_family": {}}
    for target in targets:
        path = target["file"]
        if not path.exists():
            print(f"missing: {path}")
            continue
        analysis = analyse_workbook(path)
        record = {**target, "file": str(path), **analysis}
        report["workbooks"].append(record)
        agg = analysis["aggregate_sheets"]
        print(
            f'{target["family"]:4s} {target["kind"]:17s} '
            f'sheets={analysis["sheets"]:3d} rows={analysis["rows_total"]:6d} '
            f'dup={analysis["duplicate_rows"]:6d} ({(analysis["duplicate_share"] or 0)*100:5.1f}%) '
            f'aggregate_sheet={agg[0]["sheet"] if agg else "-"}'
        )

    for family in sorted({record["family"] for record in report["workbooks"]}):
        rows = {
            record["kind"]: record
            for record in report["workbooks"]
            if record["family"] == family
        }
        dictionary = rows.get("DATA_DICTIONARY", {}).get("distinct_rows_workbook", 0)
        decision = rows.get("DECISION_SUPPORT", {}).get("distinct_rows_workbook", 0)
        indicators = rows.get("INDICATORS", {}).get("distinct_rows_workbook", 0)
        denominator = dictionary + decision + indicators
        report["by_family"][family] = {
            "distinct_data_dictionary_rows": dictionary,
            "distinct_decision_support_rows": decision,
            "distinct_indicator_rows": indicators,
            # The share of the corpus that a source-asset role labeller would mark
            # recommendation-bearing but whose form cannot carry a recommendation. This is
            # the artifact-level analogue of F2, measured pre-QA.
            "data_dictionary_share": round(dictionary / denominator, 4) if denominator else None,
            "duplicate_share_data_dictionary": rows.get("DATA_DICTIONARY", {}).get(
                "duplicate_share"
            ),
        }

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print("\nper family")
    print(f'{"family":8s} {"dd rows":>9s} {"dsl rows":>9s} {"ind rows":>9s} {"dd share":>9s}')
    for family, summary in report["by_family"].items():
        share = summary["data_dictionary_share"]
        print(
            f'{family:8s} {summary["distinct_data_dictionary_rows"]:9d} '
            f'{summary["distinct_decision_support_rows"]:9d} '
            f'{summary["distinct_indicator_rows"]:9d} '
            f'{(share if share is not None else 0)*100:8.1f}%'
        )
    print(f"\nwrote {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
