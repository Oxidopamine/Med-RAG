"""Retrieval-only sweep over K and lane: is 46% coverage the corpus, the retriever or the policy?

Section 3.6 of [docs/correctness-measurement-plan.md](../docs/correctness-measurement-plan.md).
A retrieval-only sweep costs no Gemini calls and changes no rendered claim, so it
invalidates no label. It queries the live collection directly through Qdrant's
`query_points`, one lane at a time, for K in {10, 20, 50, 100} and lane in {sparse, dense,
hybrid at `rrf_k` 60}, under the same release filter serving applies. `ServingRetrievalService`
has no flag to disable a lane, so the sweep does not go through it; the benchmark runner's
`RetrievalMode` values are the reference and are read, not modified. The raw rankings carry
no duplicate suppression, which serving applies before its cut, so the hybrid top ten here
is not byte-identical to the served ten.

Reported, exploratory, as recall at K per lane:

* of the evidence IDs production A cited on its answered questions;
* of the IDs the Section 7 oracle marks `PRESENT` for the abstentions, so that a steep rise
  from K = 10 to 50 says the abstentions are a retrieval ceiling and a flat line says they
  are a corpus limit; and
* coverage of the gate's required roles at each K, since the gate is applied at top_k 10
  and its blocks may be an artefact of the cut. Roles are qualified by form exactly as
  serving qualifies them: `PRIMARY_SUPPORT` counts only on a recommendation-bearing kind.

    python scripts/retrieval_sweep.py \\
        --run data/local/cm/production-a.json \\
        --oracle data/local/cm/oracle-production-a.json \\
        --bundle data/local/validated-who-smart-hiv-release.json \\
        --vectors data/local/benchmark-source-derived/qwen3-0.6b-release-vectors.json \\
        --collection corpus_cr_b6155a25415b25f3ba787b3b036da10d--vp-7d236ba6d3b956445782e60a \\
        --output benchmarks/analysis/retrieval-sweep-production-a.json \\
        --embedding-backend verified-local ...

The output carries evidence IDs, ranks and rates and no passage text.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.corpus_steward.cli import (  # noqa: E402
    _add_embedding_backend_arguments,
    _build_embedding_backend,
    _load_index_inputs,
)
from app.corpus_steward.qdrant_index import QdrantRESTClient  # noqa: E402
from app.reasoning.presentation import PassagePresenter  # noqa: E402
from app.reasoning.retrieval_service import (  # noqa: E402
    REQUIRED_ANSWER_ROLES,
    ROLES_REQUIRING_RECOMMENDATION,
)
from app.schemas.domain import SERVABLE_LIFECYCLE_STATES  # noqa: E402

LANES = ("sparse", "dense", "hybrid")
DEFAULT_KS = (10, 20, 50, 100)


def release_filter(corpus_release_id: str) -> dict[str, Any]:
    """The serving filter, without the optional jurisdiction and language narrowing."""

    return {
        "must": [
            {"key": "corpus_release_id", "match": {"value": corpus_release_id}},
            {"key": "approval_status", "match": {"value": "APPROVED"}},
            {
                "key": "lifecycle_status",
                "match": {"any": [status.value for status in SERVABLE_LIFECYCLE_STATES]},
            },
        ]
    }


def fuse_rrf(rankings: Mapping[str, Sequence[str]], *, rrf_k: int) -> list[str]:
    """Reciprocal-rank fusion over lane rankings; ties broken by evidence ID for determinism."""

    scores: dict[str, float] = {}
    for ordered in rankings.values():
        for rank, evidence_id in enumerate(ordered, start=1):
            scores[evidence_id] = scores.get(evidence_id, 0.0) + 1.0 / (rrf_k + rank)
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [evidence_id for evidence_id, _ in ordered]


def recall_at_k(ranking: Sequence[str], targets: Iterable[str], k: int) -> float | None:
    wanted = list(dict.fromkeys(targets))
    if not wanted:
        return None
    top = set(ranking[:k])
    return sum(1 for evidence_id in wanted if evidence_id in top) / len(wanted)


def roles_covered(
    ranking: Sequence[str], qualified_roles: Mapping[str, frozenset[str]], k: int
) -> bool:
    covered: set[str] = set()
    for evidence_id in ranking[:k]:
        covered |= qualified_roles.get(evidence_id, frozenset())
    return all(role.value in covered for role in REQUIRED_ANSWER_ROLES)


def sweep_metrics(
    rankings: Mapping[str, Mapping[str, Sequence[str]]],
    *,
    cited: Mapping[str, Sequence[str]],
    present: Mapping[str, str],
    qualified_roles: Mapping[str, frozenset[str]],
    ks: Sequence[int] = DEFAULT_KS,
) -> dict[str, Any]:
    """Per lane and K: cited-ID recall, oracle-presence recall, and required-role coverage.

    `rankings[question_id][lane]` is the ordered evidence-ID list; `cited` maps answered
    questions to the IDs their claims cite; `present` maps abstained questions to the ID the
    oracle marked PRESENT.
    """

    out: dict[str, Any] = {}
    for lane in LANES:
        out[lane] = {}
        for k in ks:
            cited_values = [
                recall_at_k(rankings[q][lane], ids, k)
                for q, ids in cited.items()
                if q in rankings
            ]
            cited_values = [v for v in cited_values if v is not None]
            all_cited = [
                set(ids) <= set(rankings[q][lane][:k])
                for q, ids in cited.items()
                if q in rankings and ids
            ]
            present_hits = [
                evidence_id in rankings[q][lane][:k]
                for q, evidence_id in present.items()
                if q in rankings
            ]
            covered = [roles_covered(rankings[q][lane], qualified_roles, k) for q in rankings]
            out[lane][str(k)] = {
                "cited_id_recall_mean": round(sum(cited_values) / len(cited_values), 4)
                if cited_values
                else None,
                "answered_questions_with_every_cited_id": (
                    sum(all_cited),
                    len(all_cited),
                ),
                "oracle_present_recall": (sum(present_hits), len(present_hits)),
                "required_roles_covered": (sum(covered), len(covered)),
            }
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="production A")
    parser.add_argument("--oracle", type=Path, default=None, help="abstention oracle output")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--qdrant-api-key", default=None)
    parser.add_argument("--qdrant-timeout", type=float, default=30.0)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--ks", default=",".join(str(k) for k in DEFAULT_KS))
    parser.add_argument("--limit", type=int, default=None)
    _add_embedding_backend_arguments(parser)
    return parser


def _outcome(record: dict[str, Any]) -> str:
    generation = record.get("generation")
    if generation is None:
        return "ABSTAINED"
    if "error" in generation or generation.get("reason_code") == "GENERATION_UNAVAILABLE":
        return "ERROR"
    return "ABSTAINED" if generation.get("abstained") else "ANSWERED"


async def main() -> int:
    arguments = build_parser().parse_args()
    ks = tuple(int(k.strip()) for k in arguments.ks.split(",") if k.strip())
    limit = max(ks)

    run = json.loads(arguments.run.read_text(encoding="utf-8"))
    records = list(run["results"])
    if arguments.limit is not None:
        records = records[: arguments.limit]
    oracle = (
        json.loads(arguments.oracle.read_text(encoding="utf-8")) if arguments.oracle else None
    )

    bundle, vectors = _load_index_inputs(arguments)
    evidence = {record.evidence_id: record for record in bundle.evidence}
    presenter = PassagePresenter.for_release(evidence.values())
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
    query_filter = release_filter(vectors.content.corpus_release_id)
    qualified: dict[str, frozenset[str]] = {}

    def qualified_roles(evidence_id: str) -> frozenset[str]:
        if evidence_id not in qualified:
            record = evidence[evidence_id]
            roles = set(record.evidence_roles)
            if not presenter.render(record).is_recommendation_bearing:
                roles -= set(ROLES_REQUIRING_RECOMMENDATION)
            qualified[evidence_id] = frozenset(role.value for role in roles)
        return qualified[evidence_id]

    async def lane_ranking(vector_name: str, query: Any) -> list[str]:
        raw = await qdrant.query_points(
            arguments.collection,
            {
                "query": query,
                "using": vector_name,
                "filter": query_filter,
                "limit": limit,
                "with_payload": ["evidence_id"],
                "with_vector": False,
            },
        )
        ordered: list[str] = []
        for point in raw:
            evidence_id = (point.get("payload") or {}).get("evidence_id")
            if not isinstance(evidence_id, str) or evidence_id not in evidence:
                raise SystemExit(
                    f"Qdrant returned an evidence ID outside the release: {evidence_id!r}"
                )
            ordered.append(evidence_id)
        return ordered

    rankings: dict[str, dict[str, list[str]]] = {}
    cited: dict[str, list[str]] = {}
    present: dict[str, str] = {}
    started = time.perf_counter()
    for index, record in enumerate(records, start=1):
        question_id = record["question_id"]
        embedded = (await backend.embed_queries([record["question"]]))[0]
        dense = await lane_ranking(vectors.content.dense.name, list(embedded.dense))
        sparse = await lane_ranking(
            vectors.content.sparse.name,
            {"indices": list(embedded.sparse.indices), "values": list(embedded.sparse.values)},
        )
        rankings[question_id] = {
            "sparse": sparse,
            "dense": dense,
            "hybrid": fuse_rrf({"dense": dense, "sparse": sparse}, rrf_k=arguments.rrf_k),
        }
        for evidence_id in {e for lane in rankings[question_id].values() for e in lane}:
            qualified_roles(evidence_id)
        outcome = _outcome(record)
        if outcome == "ANSWERED":
            cited[question_id] = list(
                dict.fromkeys(
                    e
                    for claim in record["generation"]["claims"]
                    for e in claim.get("evidence_ids", [])
                )
            )
        elif outcome == "ABSTAINED" and oracle is not None:
            verdict = (oracle.get("records") or {}).get(question_id) or {}
            if verdict.get("verdict") == "PRESENT" and verdict.get("evidence_id"):
                present[question_id] = verdict["evidence_id"]
        print(f"[{index:>3}/{len(records)}] {question_id:<16} {outcome}")

    metrics = sweep_metrics(
        rankings, cited=cited, present=present, qualified_roles=qualified, ks=ks
    )
    positions = {
        question_id: {
            lane: {
                evidence_id: (ordered.index(evidence_id) + 1 if evidence_id in ordered else None)
                for evidence_id in (cited.get(question_id) or [])
                + ([present[question_id]] if question_id in present else [])
            }
            for lane, ordered in lanes.items()
        }
        for question_id, lanes in rankings.items()
    }
    output = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "run": str(arguments.run),
        "oracle": str(arguments.oracle) if arguments.oracle else None,
        "collection": arguments.collection,
        "corpus_release_id": vectors.content.corpus_release_id,
        "release_filter": query_filter,
        "rrf_k": arguments.rrf_k,
        "ks": list(ks),
        "questions": len(rankings),
        "answered_with_citations": len(cited),
        "abstentions_with_oracle_present": len(present),
        "required_roles": [role.value for role in REQUIRED_ANSWER_ROLES],
        "note": (
            "Raw lane rankings under the serving release filter, without duplicate "
            "suppression; exploratory. A generation sweep at a different K changes every "
            "claim and is Horizon 2."
        ),
        "metrics": metrics,
        "rank_of_targets": positions,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print()
    for lane in LANES:
        cells = "  ".join(
            f"K={k}: cited {metrics[lane][str(k)]['cited_id_recall_mean']} "
            f"roles {metrics[lane][str(k)]['required_roles_covered']}"
            for k in ks
        )
        print(f"{lane:<7} {cells}")
    print(f"wrote {arguments.output}")
    await qdrant.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
