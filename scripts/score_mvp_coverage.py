"""Read a classified coverage run and compute the pre-registered quantities.

`run_mvp_coverage_stage1.py` deliberately leaves every `bucket` null, and nothing until
now read one back, so a classified file had no path to a number. This closes that: it
takes the run plus the buckets a human filled and applies the decision rule from
[docs/mvp-definition.md](../docs/mvp-definition.md) exactly as written, without
reinterpreting it.

Three properties matter more than the arithmetic.

**It refuses to score a partial classification.** A missing bucket is not treated as an
abstention, a failure, or a zero. `p_answered` over a subset silently answers a different
question than the one pre-registered - the denominator is the whole draw - so an
unclassified question is an error, not a gap to route around.

**It checks the classification against what the system actually did.** A question the
system answered cannot carry an `ABSTAINED_*` bucket and vice versa. That is not
pedantry: those are the two halves of the measurement and a transposed row moves mass
between `p_answered` and its complement in the direction of whichever mistake was made.

**`p_wrong` is reported before `p_answered`.** The definition makes one
`ANSWERED_WRONG` stop the MVP regardless of coverage, so coverage is the subordinate
number and the output says so. With zero occurrences the residual is printed as a
one-sided upper bound and never as "zero", because n = 49 cannot distinguish a safe
system from a system whose failure rate is under about 6%.

Usage:

    python scripts/score_mvp_coverage.py --run <run.json> [--buckets <buckets.json>]
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ANSWERED_PREFIX = "ANSWERED_"
ABSTAINED_PREFIX = "ABSTAINED_"


def _load_runner():
    """Load the runner by path to share its thresholds and interval, not restate them.

    Reproducing `wilson_interval` here would let the scorer and the runner drift into
    reporting different intervals for the same counts, and the pre-registered rule is a
    comparison against an interval. `scripts/` is not an importable package, so this is
    the same by-path load the runner itself uses for `ask.py`.
    """

    path = Path(__file__).resolve().parent / "run_mvp_coverage_stage1.py"
    spec = importlib.util.spec_from_file_location("medrag_coverage_runner", path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable in-tree
        raise SystemExit(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def system_outcome(record: dict[str, Any]) -> str:
    """What the system did, independent of what a reader concluded about it.

    ERROR is a generation failure: a quota failure, a declined finish, or a model
    behaviour recorded under its own `error_class`. It is a missing measurement, never an
    abstention, and it carries no bucket. A legacy record that filed a quota failure as a
    `GENERATION_UNAVAILABLE` abstention reads as an error too.
    """

    generation = record.get("generation")
    if generation is None:
        return "ABSTAINED"
    if "error" in generation or generation.get("reason_code") == "GENERATION_UNAVAILABLE":
        return "ERROR"
    if generation.get("abstained"):
        return "ABSTAINED"
    return "ANSWERED"


def resolve_buckets(
    run: dict[str, Any], buckets_document: dict[str, Any] | None
) -> dict[str, str | None]:
    """Prefer an external buckets file, falling back to buckets inlined in the run."""

    inline = {record["question_id"]: record.get("bucket") for record in run["results"]}
    if buckets_document is None:
        return inline
    external = buckets_document.get("buckets") or {}
    return {
        question_id: external.get(question_id) or inline.get(question_id)
        for question_id in inline
    }


def validate(
    run: dict[str, Any], buckets: dict[str, str | None], allowed: tuple[str, ...]
) -> list[str]:
    problems: list[str] = []
    for record in run["results"]:
        question_id = record["question_id"]
        bucket = buckets.get(question_id)
        if system_outcome(record) == "ERROR":
            if bucket is not None:
                problems.append(
                    f"{question_id}: generation error record classified {bucket} - a missing "
                    "measurement carries no bucket and enters no denominator"
                )
            continue
        if bucket is None:
            problems.append(f"{question_id}: not classified")
            continue
        if bucket not in allowed:
            problems.append(f"{question_id}: unknown bucket {bucket!r}")
            continue
        outcome = system_outcome(record)
        expected = ANSWERED_PREFIX if outcome == "ANSWERED" else ABSTAINED_PREFIX
        if not bucket.startswith(expected):
            problems.append(
                f"{question_id}: system {outcome} but classified {bucket} - "
                "a transposed row moves mass between p_answered and its complement"
            )
    return problems


def score(run: dict[str, Any], buckets: dict[str, str | None], runner: Any) -> dict[str, Any]:
    error_records = [r["question_id"] for r in run["results"] if system_outcome(r) == "ERROR"]
    counts = collections.Counter(
        buckets[r["question_id"]] for r in run["results"] if system_outcome(r) != "ERROR"
    )
    total = len(run["results"]) - len(error_records)
    answered = sum(count for bucket, count in counts.items() if bucket.startswith(ANSWERED_PREFIX))
    wrong = counts.get("ANSWERED_WRONG", 0)
    avoidable = counts.get("ABSTAINED_AVOIDABLE", 0)

    lower, upper = runner.wilson_interval(answered, total)
    if upper < runner.STAGE1_FLOOR:
        decision = "CORPUS_CANNOT_CARRY_THE_PRODUCT"
        reading = (
            f"The whole interval sits below the {runner.STAGE1_FLOOR} floor. Narrative "
            "materialization moves from roadmap item 6 to blocking."
        )
    elif lower > runner.STAGE1_CEILING:
        decision = "PROCEED_ON_THIS_CORPUS"
        reading = (
            f"The whole interval sits above the {runner.STAGE1_CEILING} ceiling. The MVP "
            "proceeds on this corpus as scoped."
        )
    else:
        decision = "GO_TO_STAGE_2"
        reading = (
            f"The interval spans {runner.STAGE1_FLOOR}-{runner.STAGE1_CEILING} territory, "
            "which stage 1 cannot resolve. Extend to n = 150; the decision is then made on "
            "the shape of ABSTAINED_CORRECT, not on a threshold."
        )

    by_chapter: dict[str, dict[str, int]] = {}
    for record in run["results"]:
        if system_outcome(record) == "ERROR":
            continue
        bucket = buckets[record["question_id"]]
        key = f"{record['chapter_no']} {record['chapter']}"
        entry = by_chapter.setdefault(key, {"answered": 0, "total": 0, "abstained_correct": 0})
        entry["total"] += 1
        if bucket.startswith(ANSWERED_PREFIX):
            entry["answered"] += 1
        elif bucket == "ABSTAINED_CORRECT":
            entry["abstained_correct"] += 1

    return {
        "questions": total,
        "error_records": {
            "count": len(error_records),
            "question_ids": error_records,
            "policy": "missing measurements, excluded from every denominator",
        },
        "bucket_counts": dict(sorted(counts.items())),
        "p_wrong": {
            "answered_wrong": wrong,
            "rate": round(wrong / total, 4),
            "stops_the_mvp": wrong > 0,
            # Reported as a bound rather than as zero: the definition is explicit that
            # this screens out a bad system and cannot certify a good one.
            "zero_occurrence_upper_bound_95": (
                round(runner.zero_occurrence_upper_bound(total), 4) if wrong == 0 else None
            ),
        },
        "p_answered": {
            "answered": answered,
            "rate": round(answered / total, 4),
            "wilson_95": [round(lower, 4), round(upper, 4)],
            "decision": decision,
            "reading": reading,
        },
        "abstention_split": {
            "abstained_correct": counts.get("ABSTAINED_CORRECT", 0),
            "abstained_avoidable": avoidable,
            # Scored against the system rather than the corpus, and the negative set for
            # priority item 4 must exclude it.
            "avoidable_are_system_defects": avoidable > 0,
        },
        "by_chapter": dict(sorted(by_chapter.items())),
        "provenance": {
            "registered": run.get("registered"),
            "generation_provider": run.get("generation_provider"),
            "generation_binding": run.get("generation_binding"),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument(
        "--buckets",
        type=Path,
        default=None,
        help="a build_claim_audit_worksheet.py template with buckets filled in",
    )
    parser.add_argument("--output", type=Path, default=None, help="also write the score as JSON")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    runner = _load_runner()
    run = json.loads(arguments.run.read_text(encoding="utf-8"))
    buckets_document = (
        json.loads(arguments.buckets.read_text(encoding="utf-8")) if arguments.buckets else None
    )
    buckets = resolve_buckets(run, buckets_document)

    problems = validate(run, buckets, tuple(run.get("bucket_scheme") or runner.BUCKETS))
    if problems:
        print(f"cannot score: {len(problems)} problem(s)", file=sys.stderr)
        for problem in problems[:20]:
            print(f"  {problem}", file=sys.stderr)
        if len(problems) > 20:
            print(f"  ... and {len(problems) - 20} more", file=sys.stderr)
        print(
            "\nScoring a partial classification would answer a different question than the "
            "one pre-registered.",
            file=sys.stderr,
        )
        return 1

    result = score(run, buckets, runner)

    print("=" * 72)
    wrong = result["p_wrong"]
    if wrong["stops_the_mvp"]:
        print(f"p_wrong           : {wrong['answered_wrong']} ANSWERED_WRONG - STOPS THE MVP")
        print("  One occurrence stops the MVP regardless of coverage, and becomes the next")
        print("  piece of work. The coverage number below does not override this.")
    else:
        print("p_wrong           : 0 observed")
        print(
            f"  residual 95% upper bound {wrong['zero_occurrence_upper_bound_95']} - "
            "a screening result, not a safety claim"
        )
    print()
    answered = result["p_answered"]
    print(f"p_answered        : {answered['answered']}/{result['questions']} = {answered['rate']}")
    print(f"Wilson 95%        : {answered['wilson_95']}")
    print(f"decision          : {answered['decision']}")
    print(f"  {answered['reading']}")
    print()
    if result["error_records"]["count"]:
        print(
            f"error records     : {result['error_records']['count']} excluded as missing "
            f"measurements ({', '.join(result['error_records']['question_ids'])})"
        )
    print(f"buckets           : {result['bucket_counts']}")
    split = result["abstention_split"]
    print(
        f"abstentions       : {split['abstained_correct']} correct, "
        f"{split['abstained_avoidable']} avoidable"
    )
    if split["avoidable_are_system_defects"]:
        print("  ABSTAINED_AVOIDABLE is scored against the system, and must be excluded")
        print("  from the plausible-negative set built in priority item 4.")
    print()
    print("by chapter:")
    for chapter, entry in result["by_chapter"].items():
        rate = entry["answered"] / entry["total"]
        print(f"  {chapter:44} {entry['answered']:>2}/{entry['total']:<2} {rate:.2f}")

    provenance = result["provenance"]
    print()
    if not provenance["registered"]:
        print("NOT REGISTERED: the question set was not REVIEWED. This is a harness check.")
    if provenance["generation_provider"] != "claude":
        print(
            f"Lane: {provenance['generation_provider']}. This number belongs to the lane that "
            "produced it and is not the sealed candidate's."
        )
    if not provenance["generation_binding"]:
        print("Model not recorded in this run: it cannot be reproduced or re-compared.")

    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nwrote {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
