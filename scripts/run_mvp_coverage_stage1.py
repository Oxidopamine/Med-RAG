"""Run the MVP coverage question set through the serving path and record the outcome.

This is the harness for stage 1 of the coverage measurement pre-registered in
[docs/mvp-definition.md](../docs/mvp-definition.md). It runs the sampled questions
through the same retrieval path `scripts/ask.py` drives, one question at a time, and
writes a results file whose per-question `bucket` field is left **null** for a human to
fill in.

Two properties of the design carry the pre-registration into code.

**It will not silently measure a draft question set.** The definition requires
`review_status` to reach `REVIEWED` before stage 1 runs, because "a coverage number
measured on unreviewed questions inherits whatever bias the drafting introduced". A draft
set therefore needs `--allow-draft`, and any run of one is stamped `registered: false` in
its own output. A file that says `registered: false` is a harness shakedown, not the
measurement, and nothing downstream should read its interval as the answer.

**It does not classify.** `bucket` stays null. The definition says to classify before
reading rather than after, and a script that guessed `ANSWERED_CORRECT` from the fact
that an answer came back would be inventing the one judgement the measurement exists to
collect. The runner records what the system did; a reader records what it means.

## Generation is optional, and the retrieval-only run is the useful one first

Without `--generate` this stops after retrieval and the role-completeness gate, which
needs no model credentials at all. That partial run is worth having on its own, because
the number it produces is an **upper bound on `p_answered`** for two independent reasons:

* a question that clears the gate can still abstain later, at grounding, when
  `GroundedAnswerComposer` discards claims their cited passages do not support; and
* the gate is role-shaped, not relevance-shaped. `retrieval_service` documents this
  directly - `is_answerable` means "a complete set of role kinds was retrieved", and a
  complete set of off-topic passages satisfies it.

Both push the same way: the true `p_answered` is at most the answerable rate measured
here. So if the Wilson interval on *this* number already sits entirely below the
pre-registered 0.25 floor, the true interval does too, and the "this corpus cannot carry
the product" branch resolves without a single model call. A high number here decides
nothing - it only says the gate is not the binding constraint.

    python scripts/run_mvp_coverage_stage1.py \
        --questions benchmarks/questions/mvp-coverage-who-hiv-v1.json \
        --bundle data/local/validated-who-smart-hiv-release.json \
        --vectors data/local/.../release-vectors.json \
        --collection <collection> \
        --output data/local/mvp-coverage-stage1-run.json \
        --embedding-backend candidate ...
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Same reason as `scripts/ask.py`: decision-rule text carries mathematical operators the
# Windows cp1252 console cannot encode, and a passage is evidence that must print as
# stored rather than be transliterated.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.corpus_steward.cli import (
    _add_embedding_backend_arguments,
    _build_embedding_backend,
    _load_index_inputs,
)
from app.corpus_steward.qdrant_index import QdrantRESTClient
from app.corpus_steward.query_expansion import (
    CONFLICT_AWARE_QUERY_REVISION,
    EXACT_TERMINOLOGY_REVISION,
    DeterministicQueryExpander,
)
from app.reasoning.answer_service import (
    GroundedAnswerComposer,
    RetrievedPassage,
)
from app.reasoning.presentation import PassagePresenter
from app.reasoning.retrieval_service import (
    ServingRetrievalResult,
    ServingRetrievalService,
)

# The buckets from the MVP definition. Recorded in the output so the file states its own
# coding scheme rather than depending on a reader having the document open.
BUCKETS: tuple[str, ...] = (
    "ANSWERED_CORRECT",
    "ANSWERED_DEFECTIVE",
    "ANSWERED_WRONG",
    "ABSTAINED_CORRECT",
    "ABSTAINED_AVOIDABLE",
)

# Pre-registered stage-1 thresholds. A Wilson 95% interval entirely below the floor
# condemns the corpus; entirely above the ceiling proceeds; anything else goes to stage 2.
STAGE1_FLOOR = 0.25
STAGE1_CEILING = 0.50

REVIEWED_STATUS_PREFIX = "REVIEWED"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--qdrant-api-key", default=None)
    parser.add_argument("--qdrant-timeout", type=float, default=30.0)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--candidate-limit", type=int, default=100)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--expand", action="store_true", help="add deterministic expansions")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="also compose an answer for questions that clear the gate",
    )
    parser.add_argument(
        "--generation-provider",
        choices=("claude", "gemini"),
        default="claude",
        help="which model backs --generate; claude is the intended production lane",
    )
    parser.add_argument(
        "--allow-draft",
        action="store_true",
        help=(
            "run against a question set whose review_status is not REVIEWED. The run is "
            "stamped registered=false and is a harness shakedown, not the measurement."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="stop after N questions; for validating the harness, never for a real run",
    )
    parser.add_argument(
        "--passage-text",
        type=int,
        default=600,
        help="characters of each passage's rendered text to retain in the output",
    )
    _add_embedding_backend_arguments(parser)
    return parser


def wilson_interval(successes: int, total: int, z: float = 1.959963985) -> tuple[float, float]:
    """The Wilson score interval, which every other gate in this project is stated as.

    Chosen over the normal approximation because the pre-registered decision compares an
    interval against 0.25 and 0.50 at n = 49, where the approximation misbehaves near the
    ends and would move the decision.
    """

    if total == 0:
        return (0.0, 1.0)
    proportion = successes / total
    denominator = 1.0 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    spread = (
        z
        / denominator
        * math.sqrt(proportion * (1.0 - proportion) / total + z * z / (4.0 * total * total))
    )
    return (max(0.0, center - spread), min(1.0, center + spread))


def zero_occurrence_upper_bound(total: int, alpha: float = 0.05) -> float:
    """One-sided 95% upper bound on a rate observed zero times in `total` trials.

    Exact rather than the rule-of-three approximation: `1 - alpha**(1/n)` reproduces the
    5.8% at n = 50 and 2.0% at n = 150 that the MVP definition quotes, and 3/n does not.
    """

    if total <= 0:
        return 1.0
    return 1.0 - alpha ** (1.0 / total)


def digest_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class QuestionItem:
    question_id: str
    question: str
    chapter_no: Any
    chapter: str
    recommendation_id: str
    ely_form_no: Any
    flags: tuple[str, ...]
    source_term_overlap: Any


def load_questions(path: Path, *, limit: int | None) -> tuple[dict[str, Any], list[QuestionItem]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    items: list[QuestionItem] = []
    for raw in document.get("items", []):
        # `usable` is the set's own exclusion flag. The one UNUSABLE_FRAGMENT in v1 is an
        # anaphoric statement with no referent; the definition excludes it and runs at
        # n = 49 rather than redrawing to reach a round number.
        if not raw.get("usable", False):
            continue
        items.append(
            QuestionItem(
                question_id=raw["question_id"],
                question=raw["question"],
                chapter_no=raw.get("chapter_no"),
                chapter=raw.get("chapter", ""),
                recommendation_id=raw.get("recommendation_id", ""),
                ely_form_no=raw.get("ely_form_no"),
                flags=tuple(raw.get("flags", []) or ()),
                source_term_overlap=raw.get("source_term_overlap"),
            )
        )
    if limit is not None:
        items = items[:limit]
    return document, items


def passage_record(passage: Any, *, text_limit: int) -> dict[str, Any]:
    return {
        "evidence_id": passage.evidence_id,
        "fused_score": round(passage.fused_score, 6),
        "lanes": list(passage.lanes),
        "evidence_roles": [role.value for role in passage.evidence_roles],
        "qualified_roles": [role.value for role in passage.qualified_roles],
        "kind": passage.kind.value,
        "is_recommendation_bearing": passage.is_recommendation_bearing,
        "render_allowed": passage.render_allowed,
        "source_version_id": passage.source_version_id,
        "duplicates_suppressed": list(passage.duplicates_suppressed),
        # The presentation view, which is what a reader classifying this question sees.
        # Truncated because the file is meant to be read, and the citable content is
        # `content_exact` in the release, reachable by `evidence_id`.
        "rendered_text": passage.rendered_text[:text_limit],
        "rendered_text_truncated": len(passage.rendered_text) > text_limit,
    }


def retrieval_record(result: ServingRetrievalResult, *, text_limit: int) -> dict[str, Any]:
    return {
        "is_answerable": result.is_answerable,
        "passage_count": len(result.passages),
        "missing_required_roles": [role.value for role in result.missing_required_roles],
        "lane_failures": list(result.lane_failures),
        "suppressed_duplicate_count": result.suppressed_duplicate_count,
        "disqualified_role_claims": [
            {"evidence_id": evidence_id, "role": role.value}
            for evidence_id, role in result.disqualified_role_claims
        ],
        "expansions_used": list(result.expansions_used),
        "latency_ms": round(result.latency_ms, 1),
        "passages": [passage_record(p, text_limit=text_limit) for p in result.passages],
    }


def gate_reason(result: ServingRetrievalResult) -> str | None:
    """The abstention reason code the serving path would carry, or None if it passed.

    Mirrors what `QuestionService` reports so the recorded reason is the product's own
    vocabulary rather than this script's paraphrase of it.
    """

    if not result.passages:
        return "NO_EVIDENCE_RETRIEVED"
    if result.missing_required_roles:
        return "INCOMPLETE_EVIDENCE_ROLE_SET"
    return None


async def compose_answer(
    composer: GroundedAnswerComposer, question: str, result: ServingRetrievalResult
) -> dict[str, Any]:
    composed = await composer.compose(
        question,
        tuple(
            RetrievedPassage(
                evidence_id=passage.evidence_id,
                text=passage.rendered_text,
                evidence_roles=tuple(role.value for role in passage.evidence_roles),
                source_version_label=passage.source_version_id,
            )
            for passage in result.passages
        ),
    )
    if composed.abstention is not None:
        return {
            "abstained": True,
            "reason_code": composed.abstention.reason_code,
            "message": composed.abstention.message,
        }
    verification = composed.verification
    return {
        "abstained": False,
        "claims": [
            {"text": claim.text, "evidence_ids": list(claim.evidence_ids)}
            for claim in composed.claims
        ],
        "conflicts": list(composed.conflicts),
        "verification": {
            "rendered_claims": verification.rendered_claims,
            "supported_claims": verification.supported_claims,
            "withheld_claims": verification.withheld_claims,
        },
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    gate_passed = sum(1 for record in records if record["retrieval"]["is_answerable"])
    lower, upper = wilson_interval(gate_passed, total)
    reasons: dict[str, int] = {}
    for record in records:
        reason = record["gate_reason"]
        if reason is not None:
            reasons[reason] = reasons.get(reason, 0) + 1

    # Read strictly as a bound: the gate is an upper bound on p_answered, so only the
    # "condemns" branch of the pre-registered rule can fire on it. Clearing the ceiling
    # here says nothing, because grounding has not run.
    if upper < STAGE1_FLOOR:
        reading = (
            "The interval on the upper bound lies entirely below the 0.25 floor, so the "
            "true p_answered interval does too. The pre-registered 'corpus cannot carry "
            "the product' branch is reachable on this evidence alone."
        )
    elif lower >= STAGE1_FLOOR:
        reading = (
            "The upper bound clears the 0.25 floor, so the corpus is not condemned by "
            "retrieval alone. This decides nothing further: p_answered requires the "
            "grounded run and a human classification of every question."
        )
    else:
        reading = (
            "The interval on the upper bound straddles the 0.25 floor, so retrieval "
            "alone cannot reach the condemning branch either way."
        )

    return {
        "questions_run": total,
        "gate_passed": gate_passed,
        "gate_passed_rate": round(gate_passed / total, 4) if total else None,
        "gate_passed_wilson_95": [round(lower, 4), round(upper, 4)],
        "gate_reason_counts": reasons,
        "interpretation": {
            "quantity": "upper bound on p_answered",
            "why_upper_bound": [
                "grounding can still abstain after the gate passes",
                "the gate tests role completeness, not topical relevance",
            ],
            "stage1_floor": STAGE1_FLOOR,
            "stage1_ceiling": STAGE1_CEILING,
            "reading": reading,
        },
        "p_wrong_zero_occurrence_upper_bound_95": round(zero_occurrence_upper_bound(total), 4)
        if total
        else None,
    }


async def main() -> int:
    arguments = build_parser().parse_args()

    document, items = load_questions(arguments.questions, limit=arguments.limit)
    review_status = str(document.get("review_status", ""))
    registered = review_status.upper().startswith(REVIEWED_STATUS_PREFIX)
    if not registered and not arguments.allow_draft:
        raise SystemExit(
            f"question set review_status is {review_status!r}, not REVIEWED.\n"
            "docs/mvp-definition.md requires review before stage 1 runs, because a "
            "coverage number measured on unreviewed questions inherits the drafting "
            "bias. Pass --allow-draft to run it anyway as a harness shakedown; the "
            "output will be stamped registered=false."
        )
    if not registered:
        print("!! UNREGISTERED RUN: question set is not REVIEWED.")
        print("!! This is a harness shakedown. Its interval is not the measurement.")
        print()

    bundle, vectors = _load_index_inputs(arguments)
    evidence = {record.evidence_id: record for record in bundle.evidence}
    print(f"release  : {vectors.content.corpus_release_id}")
    print(f"evidence : {len(evidence)} approved records")
    print(f"questions: {len(items)} usable")

    backend = _build_embedding_backend(
        arguments,
        baseline_dense_dimension=vectors.content.dense.dimension,
        baseline_sparse_dimension=vectors.content.sparse.dimension,
        maximum_batch_size=1,
        expected_dense=vectors.content.dense,
        expected_sparse=vectors.content.sparse,
    )
    qdrant = QdrantRESTClient(
        arguments.qdrant_url,
        api_key=arguments.qdrant_api_key,
        timeout_seconds=arguments.qdrant_timeout,
    )
    # Release-load work, not per-question latency: the presenter scans the release for
    # the decision-table header rows it takes column labels from.
    presenter = PassagePresenter.for_release(evidence.values())
    service = ServingRetrievalService(
        qdrant,
        backend,
        presenter=presenter,
        candidate_limit=arguments.candidate_limit,
        top_k=arguments.top_k,
        rrf_k=arguments.rrf_k,
    )

    expander = None
    if arguments.expand:
        expander = DeterministicQueryExpander(
            terminology_revision=EXACT_TERMINOLOGY_REVISION,
            safety_query_revision=CONFLICT_AWARE_QUERY_REVISION,
        )

    composer: GroundedAnswerComposer | None = None
    if arguments.generate:
        # Imported here rather than at module scope so a retrieval-only run needs none of
        # the generation extras installed.
        from scripts.ask import gemini_parameters, vertex_parameters  # type: ignore

        if arguments.generation_provider == "gemini":
            from app.reasoning.gemini_adapters import GeminiGenerationAdapter

            composer = GroundedAnswerComposer(GeminiGenerationAdapter(gemini_parameters()))
        else:
            from app.reasoning.generation_adapters import AnthropicGenerationAdapter

            composer = GroundedAnswerComposer(AnthropicGenerationAdapter(vertex_parameters()))

    records: list[dict[str, Any]] = []
    started = time.perf_counter()
    for index, item in enumerate(items, start=1):
        expansions: tuple = ()
        if expander is not None:
            expansions = expander.expand(item.question)
        result = await service.retrieve(
            item.question,
            collection=arguments.collection,
            corpus_release_id=vectors.content.corpus_release_id,
            dense_vector_name=vectors.content.dense.name,
            sparse_vector_name=vectors.content.sparse.name,
            evidence=evidence,
            expansions=expansions,
        )
        reason = gate_reason(result)
        record: dict[str, Any] = {
            "question_id": item.question_id,
            "recommendation_id": item.recommendation_id,
            "chapter_no": item.chapter_no,
            "chapter": item.chapter,
            "ely_form_no": item.ely_form_no,
            "question": item.question,
            "question_flags": list(item.flags),
            "source_term_overlap": item.source_term_overlap,
            "gate_reason": reason,
            "retrieval": retrieval_record(result, text_limit=arguments.passage_text),
            "generation": None,
            # Filled in by a human. The runner records what the system did; the bucket
            # records what it means, and the definition asks for that judgement to be
            # made against fixed criteria rather than inferred from the outcome.
            "bucket": None,
            "reviewer_note": None,
        }
        if composer is not None and result.is_answerable:
            record["generation"] = await compose_answer(composer, item.question, result)

        records.append(record)
        status = "GATE PASS" if result.is_answerable else f"ABSTAIN {reason}"
        print(f"[{index:>3}/{len(items)}] {item.question_id:<16} {status}")

        # Written after every question rather than once at the end: the run takes minutes
        # and a crash at question 40 should not discard the first 39.
        write_output(arguments, document, records, registered=registered, elapsed=None)

    elapsed = time.perf_counter() - started
    summary = write_output(arguments, document, records, registered=registered, elapsed=elapsed)

    print()
    print("=" * 72)
    print(f"questions run     : {summary['questions_run']}")
    print(f"gate passed       : {summary['gate_passed']} ({summary['gate_passed_rate']})")
    lower, upper = summary["gate_passed_wilson_95"]
    print(f"Wilson 95%        : [{lower}, {upper}]  (upper bound on p_answered)")
    for reason, count in sorted(summary["gate_reason_counts"].items()):
        print(f"  {reason:<32} {count}")
    print()
    print(summary["interpretation"]["reading"])
    print()
    print(f"wrote {arguments.output}")
    if not registered:
        print("stamped registered=false: this is not the measurement.")
    return 0


def write_output(
    arguments: argparse.Namespace,
    document: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    registered: bool,
    elapsed: float | None,
) -> dict[str, Any]:
    summary = summarize(records)
    payload = {
        "schema_version": 1,
        "run_kind": "MVP_COVERAGE_STAGE_1",
        # The single most important field in this file. False means the question set was
        # not reviewed, and the interval below is a harness check rather than the
        # pre-registered measurement.
        "registered": registered,
        "generation_ran": bool(arguments.generate),
        "generation_provider": arguments.generation_provider if arguments.generate else None,
        "question_set": {
            "set_id": document.get("set_id"),
            "path": str(arguments.questions),
            "sha256": digest_of(arguments.questions),
            "review_status": document.get("review_status"),
            "sampling": document.get("sampling"),
        },
        "retrieval_configuration": {
            "collection": arguments.collection,
            "bundle": str(arguments.bundle),
            "vectors": str(arguments.vectors),
            "embedding_backend": arguments.embedding_backend,
            "top_k": arguments.top_k,
            "candidate_limit": arguments.candidate_limit,
            "rrf_k": arguments.rrf_k,
            "expansions_enabled": bool(arguments.expand),
        },
        "bucket_scheme": list(BUCKETS),
        "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
        "summary": summary,
        "results": records,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return summary


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
