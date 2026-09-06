"""The closed-book arm: every question to Gemini with no passages, in the harness's file shape.

Section 3.4 of [docs/correctness-measurement-plan.md](../docs/correctness-measurement-plan.md).
`GroundedAnswerComposer.compose` returns `NO_EVIDENCE_RETRIEVED` on an empty passage tuple
before any model call, and `GeminiGenerationAdapter` is hard-wired to the `ModelAnswer`
contract whose claims require an `evidence_id`, so this arm calls the model through
`scripts/gemini_json.py`, which reproduces the adapter's request shape.

The prompt is the production `SYSTEM_PROMPT` with its passage rules removed and nothing
else changed. Production's five rules are: use only the supplied passages; cite evidence
IDs; set `sufficient_evidence` false when the passages do not answer; report disagreement
between passages as a typed conflict; do not restate a passage that does not bear on the
question. The first, second and fourth are passage rules and go; the third and fifth keep
their sense with `passages` replaced by `your knowledge`. The arms therefore differ in
retrieval and in the absence of the citation and conflict rules, and the manuscript records
both differences.

`knows_answer` is the closed-book abstention field, named so that a closed-book abstention
is never conflated with `MODEL_DECLARED_INSUFFICIENT` downstream. The output has the
coverage harness's top-level shape: `results[*].generation` carries `abstained`, `claims`
with empty `evidence_ids`, or an error record with its class; `retrieval` is null, so the
publisher redacts nothing. Attribution is not defined for a closed-book claim.

    export MEDRAG_VERTEX_PROJECT_ID=<project> MEDRAG_VERTEX_REGION=global
    export MEDRAG_GEMINI_MODEL_ID=gemini-3.7-flash MEDRAG_GEMINI_SEED=20260906
    python scripts/run_closed_book.py \\
        --questions benchmarks/questions/mvp-coverage-who-hiv-v2.json \\
        --output data/local/cm/closed-book-164.json \\
        --run-label closed-book-164 --notes "<start and end wall clock>"

`--source-primed` runs the optional secondary arm with "from your knowledge of the WHO
consolidated HIV guidelines" appended: the source-primed upper bound, exploratory,
budgeted separately, written to its own file.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]

CLOSED_BOOK_PROMPT = """You answer clinical questions.

Rules:
- If you do not know the answer, set knows_answer to false and explain what is missing. \
Guessing is a failure; saying you do not know is a correct outcome.
- Do not state a claim unless it bears on the question asked."""

SOURCE_PRIMED_SUFFIX = " Answer from your knowledge of the WHO consolidated HIV guidelines."

REVIEWED_STATUS_PREFIX = "REVIEWED"
BUCKETS = (
    "ANSWERED_CORRECT",
    "ANSWERED_DEFECTIVE",
    "ANSWERED_WRONG",
    "ABSTAINED_CORRECT",
    "ABSTAINED_AVOIDABLE",
)


class ClosedBookClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4_000)


class ClosedBookAnswer(BaseModel):
    """`{"knows_answer": bool, "claims": [{"text": str}], "insufficiency_note": str | null}`."""

    model_config = ConfigDict(extra="forbid")

    knows_answer: bool
    claims: list[ClosedBookClaim] = Field(max_length=40)
    insufficiency_note: str | None = None


def _load_gemini_json():
    path = REPO_ROOT / "scripts" / "gemini_json.py"
    spec = importlib.util.spec_from_file_location("medrag_gemini_json", path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable in-tree
        raise SystemExit(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-label", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--only-question-ids", default=None, metavar="ID[,ID...]")
    parser.add_argument("--merge-base", type=Path, default=None)
    parser.add_argument("--allow-draft", action="store_true")
    parser.add_argument(
        "--source-primed",
        action="store_true",
        help="the optional secondary arm: append the WHO guideline priming sentence",
    )
    return parser


def load_questions(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    items = [item for item in document.get("items", []) if item.get("usable", False)]
    return document, items


def user_content(question: str, *, source_primed: bool) -> str:
    text = f"Question:\n{question.strip()}"
    if source_primed:
        text += "\n" + SOURCE_PRIMED_SUFFIX.strip()
    return text


def generation_record(result: dict[str, Any]) -> dict[str, Any]:
    """Map a `generate_json` result onto the harness's `generation` shape."""

    if result["error"] is not None:
        return {
            "error": result["error"],
            "error_class": result["error_class"],
            "abstained": None,
            "attempts": result["attempts"],
            "prompt_sha256": result["prompt_sha256"],
        }
    try:
        answer = ClosedBookAnswer.model_validate(result["json"])
    except ValidationError as error:
        return {
            "error": f"response did not satisfy the closed-book contract: {error}",
            "error_class": "CONTRACT_VALIDATION",
            "abstained": None,
            "attempts": result["attempts"],
            "prompt_sha256": result["prompt_sha256"],
        }
    if not answer.knows_answer:
        return {
            "abstained": True,
            "reason_code": "CLOSED_BOOK_DOES_NOT_KNOW",
            "knows_answer": False,
            "message": answer.insufficiency_note or "",
            "prompt_sha256": result["prompt_sha256"],
        }
    if not answer.claims:
        return {
            "abstained": True,
            "reason_code": "CLOSED_BOOK_NO_CLAIMS",
            "knows_answer": True,
            "message": answer.insufficiency_note or "",
            "prompt_sha256": result["prompt_sha256"],
        }
    return {
        "abstained": False,
        "knows_answer": True,
        "claims": [{"text": claim.text, "evidence_ids": []} for claim in answer.claims],
        "conflicts": [],
        "verification": {
            "rendered_claims": len(answer.claims),
            "supported_claims": 0,
            "withheld_claims": 0,
        },
        "insufficiency_note": answer.insufficiency_note,
        "prompt_sha256": result["prompt_sha256"],
    }


def outcome_of(record: dict[str, Any]) -> str:
    generation = record.get("generation") or {}
    if "error" in generation:
        return "ERROR"
    return "ABSTAINED" if generation.get("abstained") else "ANSWERED"


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = [outcome_of(record) for record in records]
    error_classes: dict[str, int] = {}
    for record, outcome in zip(records, outcomes, strict=True):
        if outcome == "ERROR":
            name = record["generation"].get("error_class") or "UNCLASSIFIED"
            error_classes[name] = error_classes.get(name, 0) + 1
    answered = outcomes.count("ANSWERED")
    abstained = outcomes.count("ABSTAINED")
    measured = answered + abstained
    return {
        "questions_run": len(records),
        "answered": answered,
        "abstained": abstained,
        "answered_rate": round(answered / measured, 4) if measured else None,
        "error_records": outcomes.count("ERROR"),
        "error_classes": error_classes,
        "claims": sum(
            len((r.get("generation") or {}).get("claims") or [])
            for r, o in zip(records, outcomes, strict=True)
            if o == "ANSWERED"
        ),
        "interpretation": {
            "quantity": "closed-book answer rate",
            "reading": (
                "No retrieval, no citation rule, no conflict rule: what the model answers "
                "from parametric knowledge. Attribution is undefined for these claims; only "
                "the agreement axis of the plan applies, and no p_wrong bound exists."
            ),
        },
    }


def merge_records(
    base: list[dict[str, Any]], fresh: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    replacements = {record["question_id"]: record for record in fresh}
    merged = [replacements.pop(record["question_id"], record) for record in base]
    merged.extend(replacements.values())
    return merged


def write_output(
    arguments: argparse.Namespace,
    document: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    registered: bool,
    elapsed: float | None,
    binding: dict[str, Any],
    config_sha256: str,
) -> dict[str, Any]:
    summary = summarize(records)
    payload = {
        "schema_version": 1,
        "run_kind": "CLOSED_BOOK",
        "run_label": arguments.run_label,
        "notes": arguments.notes,
        "registered": registered,
        "ablation": {
            "is_production": False,
            "disabled_mechanisms": ["retrieval", "citation_rule", "conflict_rule"],
            "description": (
                "closed book: no passages, no citation rule, no conflict rule; the "
                "insufficiency affordance is kept as knows_answer"
            ),
        },
        "generation_ran": True,
        "generation_provider": "gemini",
        "generation_binding": binding,
        "prompt": {
            "system_prompt": CLOSED_BOOK_PROMPT,
            "source_primed": bool(arguments.source_primed),
            "source_primed_suffix": SOURCE_PRIMED_SUFFIX if arguments.source_primed else None,
            "response_schema": ClosedBookAnswer.model_json_schema(),
            "request_config_sha256": config_sha256,
        },
        "question_set": {
            "set_id": document.get("set_id"),
            "path": str(arguments.questions),
            "sha256": hashlib.sha256(arguments.questions.read_bytes()).hexdigest(),
            "review_status": document.get("review_status"),
            "sampling": document.get("sampling"),
        },
        "retrieval_configuration": None,
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


async def main() -> int:
    arguments = build_parser().parse_args()
    gemini_json = _load_gemini_json()

    document, items = load_questions(arguments.questions)
    if arguments.limit is not None:
        items = items[: arguments.limit]
    if arguments.only_question_ids:
        wanted = {q.strip() for q in arguments.only_question_ids.split(",") if q.strip()}
        unknown = wanted - {item["question_id"] for item in items}
        if unknown:
            raise SystemExit(
                f"--only-question-ids names questions not in the set: {sorted(unknown)}"
            )
        items = [item for item in items if item["question_id"] in wanted]
    base_records: list[dict[str, Any]] = []
    if arguments.merge_base is not None:
        base = json.loads(arguments.merge_base.read_text(encoding="utf-8"))
        base_records = list(base.get("results") or [])
        if arguments.run_label is None:
            arguments.run_label = base.get("run_label")

    review_status = str(document.get("review_status", ""))
    registered = review_status.upper().startswith(REVIEWED_STATUS_PREFIX)
    if not registered and not arguments.allow_draft:
        raise SystemExit(
            f"question set review_status is {review_status!r}, not REVIEWED; pass "
            "--allow-draft to run a shakedown stamped registered=false"
        )

    parameters = gemini_json.parameters_from_environment()
    if parameters.thinking_budget is not None:
        raise SystemExit(
            "thinking_budget must be pinned by absence; a non-null binding is discarded"
        )
    client = gemini_json.GeminiJsonClient(parameters)
    binding = gemini_json.binding_of(parameters)
    schema = gemini_json.closed_schema(ClosedBookAnswer)
    config_sha256 = client.config_sha256(CLOSED_BOOK_PROMPT, schema)
    print(f"model    : {binding}")
    print(f"questions: {len(items)}")
    print(f"config   : {config_sha256[:16]}…")

    records: list[dict[str, Any]] = []
    started = time.perf_counter()
    for index, item in enumerate(items, start=1):
        result = await client.generate_json(
            system_prompt=CLOSED_BOOK_PROMPT,
            user_content=user_content(item["question"], source_primed=arguments.source_primed),
            schema=schema,
        )
        generation = generation_record(result)
        record = {
            "question_id": item["question_id"],
            "recommendation_id": item.get("recommendation_id", ""),
            "chapter_no": item.get("chapter_no"),
            "chapter": item.get("chapter", ""),
            "ely_form_no": item.get("ely_form_no"),
            "question": item["question"],
            "question_flags": list(item.get("flags", []) or ()),
            "source_term_overlap": item.get("source_term_overlap"),
            "gate_reason": None,
            "retrieval": None,
            "generation": generation,
            "bucket": None,
            "reviewer_note": None,
        }
        records.append(record)
        status = {
            "ANSWERED": f"ANSWERED {len(generation.get('claims') or [])} claims",
            "ABSTAINED": f"ABSTAIN {generation.get('reason_code')}",
            "ERROR": f"ERROR {generation.get('error_class')}",
        }[outcome_of(record)]
        print(f"[{index:>3}/{len(items)}] {item['question_id']:<16} {status}")
        write_output(
            arguments,
            document,
            merge_records(base_records, records),
            registered=registered,
            elapsed=None,
            binding=binding,
            config_sha256=config_sha256,
        )

    summary = write_output(
        arguments,
        document,
        merge_records(base_records, records),
        registered=registered,
        elapsed=time.perf_counter() - started,
        binding=binding,
        config_sha256=config_sha256,
    )
    print()
    print("=" * 72)
    for key in ("questions_run", "answered", "abstained", "error_records", "claims"):
        print(f"{key:<18}: {summary[key]}")
    if summary["error_records"]:
        print("errors are missing measurements; re-run them with --only-question-ids")
        print("and --merge-base before quoting any rate")
    print(f"wrote {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
