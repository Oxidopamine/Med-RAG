"""How concentrated retrieval is across the questions of a coverage run.

Section 5.1 of [docs/correctness-measurement-plan.md](../docs/correctness-measurement-plan.md)
quotes four figures about the retrieved sets of the stage-2 run, computed by hand on
2026-09-06 pending this script. They characterise the corpus and bound only the
cross-question form of mis-citation: a claim citing a passage that was retrieved for a
different question. The grounding predicate, `set(cited).issubset(retrieved_ids)`, rejects
that form and accepts every citation inside the question's own set whatever the text says,
which is the intra-question failure Section 5.2 measures with human labels.

The four quantities, over the retrieved sets of every record in the run:

* **distinct IDs and slots.** How many evidence IDs fill the `n × top_k` retrieved slots.
* **recurring-slot share.** The share of slots holding an ID that also appears in at least
  one other question's set. High means the membership predicate is permissive rather than
  tight, because the same passages are offered to many questions.
* **pair-sharing rate.** The share of question pairs whose retrieved sets intersect.
* **per-pair collision rate.** For one retrieved (question, ID), the probability that one
  given other question's set contains that ID, averaged over every slot.

    python scripts/retrieval_overlap.py \
        --run benchmarks/results/coverage-stage2-gemini-3.7-flash.json \
        --output benchmarks/analysis/retrieval-overlap-stage2.json

Reads the published, redacted run: no passage text is needed, only evidence IDs.
"""

from __future__ import annotations

import argparse
import collections
import itertools
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN = REPO_ROOT / "benchmarks" / "results" / "coverage-stage2-gemini-3.7-flash.json"


def retrieved_sets(run: dict[str, Any]) -> dict[str, list[str]]:
    """Evidence IDs per question in retrieval order, duplicates within a set preserved."""

    sets: dict[str, list[str]] = {}
    for record in run["results"]:
        passages = (record.get("retrieval") or {}).get("passages") or []
        sets[record["question_id"]] = [passage["evidence_id"] for passage in passages]
    return sets


def overlap_statistics(sets: dict[str, list[str]]) -> dict[str, Any]:
    question_ids = list(sets)
    questions = len(question_ids)
    slots = sum(len(ids) for ids in sets.values())
    membership: dict[str, set[str]] = collections.defaultdict(set)
    for question_id, ids in sets.items():
        for evidence_id in ids:
            membership[evidence_id].add(question_id)

    recurring_slots = sum(
        1
        for question_id, ids in sets.items()
        for evidence_id in ids
        if len(membership[evidence_id] - {question_id}) > 0
    )
    pairs = 0
    sharing_pairs = 0
    for left, right in itertools.combinations(question_ids, 2):
        pairs += 1
        if set(sets[left]) & set(sets[right]):
            sharing_pairs += 1
    collision_probabilities = [
        len(membership[evidence_id] - {question_id}) / (questions - 1)
        for question_id, ids in sets.items()
        for evidence_id in ids
    ] if questions > 1 else []
    mean_collision = (
        sum(collision_probabilities) / len(collision_probabilities)
        if collision_probabilities
        else 0.0
    )
    recurrence = collections.Counter(len(owners) for owners in membership.values())
    return {
        "questions": questions,
        "slots": slots,
        "distinct_evidence_ids": len(membership),
        "recurring_slots": recurring_slots,
        "recurring_slot_share": round(recurring_slots / slots, 4) if slots else None,
        "question_pairs": pairs,
        "question_pairs_sharing_a_passage": sharing_pairs,
        "pair_sharing_rate": round(sharing_pairs / pairs, 4) if pairs else None,
        "mean_per_pair_collision_probability": round(mean_collision, 4),
        "questions_per_evidence_id": {
            str(count): recurrence[count] for count in sorted(recurrence)
        },
        "reading": (
            "The recurring-slot share characterises the corpus and says how permissive "
            "the evidence-ID membership predicate is; the per-pair collision rate is the "
            "chance that one particular other question's set contains a given ID. Neither "
            "bounds the intra-question failure, a claim citing a retrieved passage that does "
            "not support it, which only the human attribution labels measure."
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    run = json.loads(arguments.run.read_text(encoding="utf-8"))
    statistics = overlap_statistics(retrieved_sets(run))
    statistics["run"] = str(arguments.run)
    for key in (
        "questions",
        "slots",
        "distinct_evidence_ids",
        "recurring_slots",
        "recurring_slot_share",
        "question_pairs_sharing_a_passage",
        "pair_sharing_rate",
        "mean_per_pair_collision_probability",
    ):
        print(f"{key:<40} {statistics[key]}")
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(statistics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"wrote {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
