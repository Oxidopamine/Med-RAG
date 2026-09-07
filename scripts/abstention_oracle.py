"""Was the recommendation the question was drawn from reachable in the corpus this system serves?

Section 7 of [docs/correctness-measurement-plan.md](../docs/correctness-measurement-plan.md).
The parent guideline cannot be the oracle: every question was drawn from it, so an oracle
over the parent returns "present" for essentially every abstention and measures its own
recall. The oracle therefore runs over the 5,145-record DAK release the system serves from,
and the quantity is named **retrievable corpus presence** everywhere it appears: `ABSENT`
over-counts `ABSTAINED_CORRECT` by whatever this retriever misses.

For each abstained question of production A and, as the known-item control, each of its
answered questions:

1. Query the live collection through Qdrant directly, sparse lane and dense lane, with both
   the question and the gold `source_statement` as queries, `limit 200` each, under the same
   release filter as serving. **The known-item recall is computed on these four rankings
   before anything else is added**: for an answered question, whether any evidence ID the
   model cited appears in their union. That figure is the oracle's recall floor. Then the
   four rankings are unioned with the ten passages the run actually retrieved, fused by RRF
   at k = 60, deduplicated by evidence ID, and the top 25 fused candidates carried into
   step 2; the share of cited IDs surviving the cut is the shortlist's retention rate.
2. The shortlist is rendered in full and judged in calls of at most 15 candidates each,
   taking the union of PRESENT verdicts: a single call over a two-hundred-passage context is
   a needle task with a known lost-in-the-middle failure. Output PRESENT with the evidence
   ID, or ABSENT, with a rationale.

Floors, from the plan: if the pre-union known-item recall is below 0.90 the shortlist is
widened to 50 and both figures are reported; if it is below 0.80 at 50, the corpus-presence
split is a lower bound on `ABSTAINED_AVOIDABLE` only and the judge-derived buckets for the
unread abstentions are withheld, which `derive_buckets.py` and `cm_statistics.py` apply.

Released outputs carry the prompt template and the sha256 of each filled prompt, never the
filled prompt.

    python scripts/abstention_oracle.py \\
        --run data/local/cm/production-a.json \\
        --questions benchmarks/questions/mvp-coverage-who-hiv-v2.json \\
        --bundle data/local/validated-who-smart-hiv-release.json \\
        --vectors data/local/benchmark-source-derived/qwen3-0.6b-release-vectors.json \\
        --collection corpus_cr_b6155a25415b25f3ba787b3b036da10d--vp-7d236ba6d3b956445782e60a \\
        --judge gemini-2.5-flash \\
        --output data/local/cm/oracle-production-a.json \\
        --embedding-backend verified-local ...
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import importlib.util
import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

DEFAULT_SEED = 20260906
LIMIT_PER_LANE = 200
SHORTLIST = 25
WIDENED_SHORTLIST = 50
CHUNK = 15
RECALL_FLOOR = 0.90
WIDENED_FLOOR = 0.80

ORACLE_PROMPT = """You check whether a set of guideline passages contains a given recommendation.

You see a clinical question, the recommendation statement it was drawn from, and numbered \
candidate passages with their evidence IDs. Decide whether any candidate states the \
recommendation, or the specific guidance the recommendation gives for the situation in the \
question, closely enough that a reader could act on it. Wording may differ; a passage that \
only mentions the topic without stating the guidance does not count.

Answer with present true and the evidence_id of the best candidate, or present false and a \
null evidence_id, with one sentence of rationale. Use only the candidates shown."""


class OracleVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    present: bool
    evidence_id: str | None = None
    rationale: str


def _load_by_path(name: str):
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"medrag_{name}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable in-tree
        raise SystemExit(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------------------
# Pure pieces
# --------------------------------------------------------------------------------------


def outcome_of(record: dict[str, Any]) -> str:
    generation = record.get("generation")
    if generation is None:
        return "ABSTAINED"
    if "error" in generation or generation.get("reason_code") == "GENERATION_UNAVAILABLE":
        return "ERROR"
    return "ABSTAINED" if generation.get("abstained") else "ANSWERED"


def cited_ids(record: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            evidence_id
            for claim in (record.get("generation") or {}).get("claims") or []
            for evidence_id in claim.get("evidence_ids") or []
        )
    )


def served_ids(record: dict[str, Any]) -> list[str]:
    return [p["evidence_id"] for p in (record.get("retrieval") or {}).get("passages") or []]


def known_item_hit(cited: Sequence[str], rankings: Mapping[str, Sequence[str]]) -> bool | None:
    """Whether any cited ID appears in the union of the four raw rankings."""

    if not cited:
        return None
    union = {evidence_id for ordered in rankings.values() for evidence_id in ordered}
    return any(evidence_id in union for evidence_id in cited)


def fuse_shortlist(
    rankings: Mapping[str, Sequence[str]],
    served: Sequence[str],
    *,
    rrf_k: int,
    size: int,
    fuse: Callable[..., list[str]],
) -> list[str]:
    """RRF over the four rankings and the served ten, deduplicated, cut at `size`."""

    combined = dict(rankings)
    if served:
        combined["served"] = list(served)
    return fuse(combined, rrf_k=rrf_k)[:size]


def chunked(items: Sequence[str], size: int) -> list[list[str]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def union_verdicts(chunks: Sequence[dict[str, Any]]) -> tuple[str | None, str | None]:
    """PRESENT if any chunk said so (first evidence ID wins); ABSENT if every chunk answered
    and none did; None if no chunk answered."""

    answered = [c for c in chunks if c.get("present") is not None]
    if not answered:
        return None, None
    for chunk in answered:
        if chunk["present"]:
            return "PRESENT", chunk.get("evidence_id")
    return "ABSENT", None


def widen_rule(recall: float | None, size: int) -> tuple[int, str]:
    """The pre-stated floors: widen to 50 below 0.90; below 0.80 at 50 the split is a bound."""

    if recall is None:
        return size, "no answered controls: floor not assessable"
    if recall >= RECALL_FLOOR:
        return size, f"pre-union known-item recall {recall:.3f} clears {RECALL_FLOOR}"
    if size < WIDENED_SHORTLIST:
        return (
            WIDENED_SHORTLIST,
            f"recall {recall:.3f} below {RECALL_FLOOR}: shortlist widened to {WIDENED_SHORTLIST}",
        )
    if recall >= WIDENED_FLOOR:
        return size, f"recall {recall:.3f} at {size}: between the floors, both figures reported"
    return size, (
        f"recall {recall:.3f} below {WIDENED_FLOOR} at {size}: presence split is a lower bound on "
        "ABSTAINED_AVOIDABLE only; judge-derived buckets withheld"
    )


def oracle_user_content(
    question: str,
    gold: str | None,
    candidates: Sequence[str],
    passage_text: Callable[[str], str | None],
) -> str:
    lines = [f"Question:\n{question.strip()}", ""]
    lines += [f"Recommendation statement:\n{(gold or '(none)').strip()}", ""]
    for number, evidence_id in enumerate(candidates, start=1):
        text = passage_text(evidence_id) or "(passage text unavailable)"
        lines.append(f"[{number}] evidence_id: {evidence_id}")
        lines.append(text.strip())
        lines.append("")
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    from app.corpus_steward.cli import _add_embedding_backend_arguments

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="production A")
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--judge", required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--qdrant-api-key", default=None)
    parser.add_argument("--qdrant-timeout", type=float, default=30.0)
    parser.add_argument("--limit-per-lane", type=int, default=LIMIT_PER_LANE)
    parser.add_argument("--shortlist", type=int, default=SHORTLIST)
    parser.add_argument("--chunk", type=int, default=CHUNK)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--rubric", type=Path, default=None)
    parser.add_argument(
        "--limit", type=int, default=None, help="questions per kind, for a shakedown"
    )
    parser.add_argument("--skip-judge", action="store_true", help="retrieval and floors only")
    _add_embedding_backend_arguments(parser)
    return parser


async def main() -> int:
    arguments = build_parser().parse_args()
    from app.corpus_steward.cli import _build_embedding_backend, _load_index_inputs
    from app.corpus_steward.qdrant_index import QdrantRESTClient
    from app.reasoning.presentation import PassagePresenter

    sweep = _load_by_path("retrieval_sweep")
    gemini_json = _load_by_path("gemini_json")

    run = json.loads(arguments.run.read_text(encoding="utf-8"))
    questions = json.loads(arguments.questions.read_text(encoding="utf-8"))
    gold = {item["question_id"]: item.get("source_statement") for item in questions["items"]}
    abstained = [r for r in run["results"] if outcome_of(r) == "ABSTAINED"]
    controls = [r for r in run["results"] if outcome_of(r) == "ANSWERED"]
    if arguments.limit is not None:
        abstained, controls = abstained[: arguments.limit], controls[: arguments.limit]

    bundle, vectors = _load_index_inputs(arguments)
    evidence = {record.evidence_id: record for record in bundle.evidence}
    presenter = PassagePresenter.for_release(evidence.values())
    text_cache: dict[str, str | None] = {}

    def passage_text(evidence_id: str) -> str | None:
        if evidence_id not in text_cache:
            record = evidence.get(evidence_id)
            text_cache[evidence_id] = presenter.render(record).text if record else None
        return text_cache[evidence_id]

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
    query_filter = sweep.release_filter(vectors.content.corpus_release_id)

    async def lane(vector_name: str, query: Any) -> list[str]:
        raw = await qdrant.query_points(
            arguments.collection,
            {
                "query": query,
                "using": vector_name,
                "filter": query_filter,
                "limit": arguments.limit_per_lane,
                "with_payload": ["evidence_id"],
                "with_vector": False,
            },
        )
        return [
            p["payload"]["evidence_id"]
            for p in raw
            if (p.get("payload") or {}).get("evidence_id") in evidence
        ]

    async def four_rankings(record: dict[str, Any]) -> dict[str, list[str]]:
        rankings: dict[str, list[str]] = {}
        for name, text in (
            ("question", record["question"]),
            ("gold", gold.get(record["question_id"]) or ""),
        ):
            if not text.strip():
                continue
            embedded = (await backend.embed_queries([text]))[0]
            rankings[f"dense:{name}"] = await lane(vectors.content.dense.name, list(embedded.dense))
            rankings[f"sparse:{name}"] = await lane(
                vectors.content.sparse.name,
                {"indices": list(embedded.sparse.indices), "values": list(embedded.sparse.values)},
            )
        return rankings

    # Phase 1: retrieval for every question, and the known-item floor before any union.
    records: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()
    for kind, group in (("answered_control", controls), ("abstained", abstained)):
        for record in group:
            rankings = await four_rankings(record)
            cited = cited_ids(record)
            records[record["question_id"]] = {
                "kind": kind,
                "reason": (record.get("generation") or {}).get("reason_code")
                or record.get("gate_reason"),
                "cited": cited,
                "cited_in_pre_union_rankings": known_item_hit(cited, rankings)
                if kind == "answered_control"
                else None,
                "_rankings": rankings,
                "_served": served_ids(record),
                "_record": record,
            }
            print(f"retrieved {record['question_id']:<16} {kind}")
    hits = [
        r["cited_in_pre_union_rankings"]
        for r in records.values()
        if r["kind"] == "answered_control"
    ]
    hits = [h for h in hits if h is not None]
    recall = sum(hits) / len(hits) if hits else None
    shortlist_size, floor_note = widen_rule(recall, arguments.shortlist)
    print(f"known-item recall pre-union: {recall}  -> shortlist {shortlist_size}: {floor_note}")

    # Phase 2: shortlist, retention, and the judge.
    for entry in records.values():
        shortlist = fuse_shortlist(
            entry.pop("_rankings"),
            entry.pop("_served"),
            rrf_k=arguments.rrf_k,
            size=shortlist_size,
            fuse=sweep.fuse_rrf,
        )
        entry["shortlist"] = shortlist
        entry["cited_in_shortlist"] = (
            any(e in shortlist for e in entry["cited"])
            if entry["kind"] == "answered_control" and entry["cited"]
            else None
        )

    parameters = gemini_json.parameters_from_environment().model_copy(
        update={"model_id": arguments.judge, "seed": arguments.seed, "temperature": 0.0}
    )
    client = gemini_json.GeminiJsonClient(parameters)
    schema = gemini_json.closed_schema(OracleVerdict)
    prompt_digests: dict[str, list[str]] = {}
    errors: list[dict[str, Any]] = []
    if not arguments.skip_judge:
        for number, (question_id, entry) in enumerate(records.items(), start=1):
            record = entry["_record"]
            chunks: list[dict[str, Any]] = []
            prompt_digests[question_id] = []
            for candidates in chunked(entry["shortlist"], arguments.chunk):
                content = oracle_user_content(
                    record["question"], gold.get(question_id), candidates, passage_text
                )
                result = await client.generate_json(
                    system_prompt=ORACLE_PROMPT, user_content=content, schema=schema
                )
                prompt_digests[question_id].append(result["prompt_sha256"])
                if result["error"] is not None:
                    errors.append(
                        {
                            "question_id": question_id,
                            **{k: result[k] for k in ("error", "error_class", "attempts")},
                        }
                    )
                    chunks.append({"present": None, "candidates": candidates})
                    continue
                try:
                    verdict = OracleVerdict.model_validate(result["json"])
                except ValidationError as error:
                    errors.append(
                        {
                            "question_id": question_id,
                            "error": str(error)[:300],
                            "error_class": "CONTRACT_VALIDATION",
                        }
                    )
                    chunks.append({"present": None, "candidates": candidates})
                    continue
                evidence_id = verdict.evidence_id if verdict.evidence_id in candidates else None
                chunks.append(
                    {
                        "present": verdict.present,
                        "evidence_id": evidence_id,
                        "rationale": verdict.rationale,
                        "candidates": candidates,
                    }
                )
            verdict_label, evidence_id = union_verdicts(chunks)
            entry["verdict"] = verdict_label
            entry["evidence_id"] = evidence_id
            entry["chunks"] = chunks
            print(
                f"[{number:>3}/{len(records)}] {question_id:<16} {entry['kind']:<16} "
                f"{verdict_label}"
            )
    for entry in records.values():
        entry.pop("_record", None)

    controls_present = [
        e for e in records.values() if e["kind"] == "answered_control" and e.get("verdict")
    ]
    output = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "judge": arguments.judge,
        "seed": arguments.seed,
        "binding": gemini_json.binding_of(parameters),
        "run": str(arguments.run),
        "run_label": run.get("run_label"),
        "rubric_sha256": (
            hashlib.sha256(arguments.rubric.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            if arguments.rubric
            else None
        ),
        "collection": arguments.collection,
        "release_filter": query_filter,
        "limit_per_lane": arguments.limit_per_lane,
        "rrf_k": arguments.rrf_k,
        "shortlist_size_initial": arguments.shortlist,
        "shortlist_size": shortlist_size,
        "chunk": arguments.chunk,
        "known_item_recall_pre_union": {
            "hits": sum(hits),
            "n": len(hits),
            "rate": round(recall, 4) if recall is not None else None,
        },
        "shortlist_retention": {
            "hits": sum(1 for e in records.values() if e.get("cited_in_shortlist")),
            "n": sum(1 for e in records.values() if e.get("cited_in_shortlist") is not None),
        },
        "floor": {"note": floor_note, "recall_floor": RECALL_FLOOR, "widened_floor": WIDENED_FLOOR},
        "controls_judged_present": {
            "present": sum(1 for e in controls_present if e["verdict"] == "PRESENT"),
            "n": len(controls_present),
        },
        "prompt_template": ORACLE_PROMPT,
        "config_sha256": client.config_sha256(ORACLE_PROMPT, schema),
        "prompt_sha256": prompt_digests,
        "records": records,
        "errors": errors,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "name": (
            "retrievable corpus presence: ABSENT over-counts ABSTAINED_CORRECT by whatever "
            "this retriever misses"
        ),
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"errors {len(errors)}  wrote {arguments.output}")
    await qdrant.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
