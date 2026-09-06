"""Derive the pre-registered buckets from the label file, mechanically, never at the keyboard.

Section 4.4 of [docs/correctness-measurement-plan.md](../docs/correctness-measurement-plan.md).
Writes the shape `score_mvp_coverage.py --buckets` reads, `{"buckets": {question_id:
bucket}}`, matching the skeleton `--buckets-template` emits. The scorer's `validate()`
rejects any bucket whose `ANSWERED_` or `ABSTAINED_` prefix disagrees with the record's
system outcome, so the derivation reads the run's `generation` field, not the labels alone.

Per claim, `support` is UNSUPPORTED if joint attribution is CONTRADICTORY or NO_SUPPORT;
PARTIAL if joint attribution is EXTRAPOLATORY, or if any pair is CONTRADICTORY or
NO_SUPPORT while the joint label is not; SUPPORTED otherwise. Per record: ANSWERED_WRONG if
any claim is UNSUPPORTED; otherwise ANSWERED_DEFECTIVE if any claim is PARTIAL, or
`presentation_defect` is true, or any claim carries `eligibility_drop`; otherwise
ANSWERED_CORRECT. An abstention is ABSTAINED_AVOIDABLE if retrievable presence is PRESENT
and ABSTAINED_CORRECT otherwise: from the human labels on the adjudicated abstentions, and
from the oracle's verdict on the rest only when its known-item control cleared the floor of
Section 7.2. When the floor is not cleared the judge-derived buckets are withheld and left
null, which the scorer refuses; that is the failure branch, reported here, and Q1 is then
computed directly from the labels by `cm_statistics.py`.

The rules live in `cm_statistics.py` so the statistics and the scorer's input cannot drift;
this script loads them by path.

    python scripts/derive_buckets.py \\
        --labels data/local/cm/labels-author-20260910.json \\
        --run data/local/cm/production-a.json \\
        [--oracle data/local/cm/oracle-production-a.json] \\
        --output data/local/cm/production-a-buckets.json
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_cm_statistics():
    path = REPO_ROOT / "scripts" / "cm_statistics.py"
    spec = importlib.util.spec_from_file_location("medrag_cm_statistics", path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable in-tree
        raise SystemExit(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def derive(
    run: dict[str, Any],
    labels: dict[str, Any],
    oracle: dict[str, Any] | None,
    *,
    statistics: Any,
) -> dict[str, Any]:
    floor_cleared = statistics._oracle_floor_cleared(oracle)
    derived = statistics.derive_buckets(run, labels, oracle, oracle_cleared_floor=floor_cleared)
    buckets = derived["buckets"]
    sources = derived["sources"]

    problems: list[str] = []
    unlabelled_answered: list[str] = []
    for record in run["results"]:
        question_id = record["question_id"]
        outcome = statistics.outcome_of(record)
        bucket = buckets.get(question_id)
        if outcome == "ANSWERED" and sources.get(question_id) == "unlabelled":
            unlabelled_answered.append(question_id)
        if bucket is None:
            continue
        expected = "ANSWERED_" if outcome == "ANSWERED" else "ABSTAINED_"
        if outcome == "ERROR" or not bucket.startswith(expected):
            problems.append(f"{question_id}: system {outcome} but derived {bucket}")

    counts = collections.Counter(bucket for bucket in buckets.values() if bucket is not None)
    unclassified = sorted(q for q, bucket in buckets.items() if bucket is None)
    return {
        "run": None,
        "labels": None,
        "oracle": None,
        "oracle_floor_cleared": floor_cleared,
        "buckets": buckets,
        "bucket_sources": dict(collections.Counter(sources.values())),
        "bucket_counts": dict(sorted(counts.items())),
        "unclassified": unclassified,
        "unlabelled_answered_records": unlabelled_answered,
        "prefix_problems": problems,
        "rule": (
            "plan Section 4.4: support from joint and pair attribution; ANSWERED_WRONG on any "
            "UNSUPPORTED claim; DEFECTIVE on PARTIAL, presentation_defect or eligibility_drop; "
            "abstentions from human presence labels, then the oracle if its floor is cleared"
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    statistics = _load_cm_statistics()
    run = json.loads(arguments.run.read_text(encoding="utf-8"))
    labels = json.loads(arguments.labels.read_text(encoding="utf-8"))
    oracle = json.loads(arguments.oracle.read_text(encoding="utf-8")) if arguments.oracle else None
    result = derive(run, labels, oracle, statistics=statistics)
    result["run"] = str(arguments.run)
    result["labels"] = str(arguments.labels)
    result["oracle"] = str(arguments.oracle) if arguments.oracle else None

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"buckets           : {result['bucket_counts']}")
    print(f"sources           : {result['bucket_sources']}")
    print(f"oracle floor      : {'cleared' if result['oracle_floor_cleared'] else 'not cleared'}")
    if result["unclassified"]:
        print(
            f"unclassified      : {len(result['unclassified'])} records carry no bucket; the "
            "scorer will refuse this file. Report the distribution over classified records "
            "with the unclassified abstentions as a named row (plan Section 4.4)."
        )
    print(f"wrote {arguments.output}")
    if result["unlabelled_answered_records"]:
        print(
            f"\n{len(result['unlabelled_answered_records'])} answered records carry no label; "
            "no confirmatory quantity is computed on a partial census.",
            file=sys.stderr,
        )
        return 1
    if result["prefix_problems"]:
        for problem in result["prefix_problems"]:
            print(f" - {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
