"""Ask one question end to end against a validated release.

This is the development driver for the serving path - retrieval, the role-completeness
gate, and grounded answer composition - without activating a release. Activation is a
separate signed gate that requires a sealed-holdout acceptance record, and opening the
holdout to look at an answer would spend a one-time custody-controlled claim.

Retrieval runs whether or not generation credentials are configured. With --generate
and Vertex ADC in place it also composes a grounded answer; without them it stops after
retrieval and says so, which is still the useful half while credentials are pending.

    python scripts/ask.py --question "When should ART be started?" \
        --bundle data/local/validated-who-smart-hiv-release.json \
        --vectors data/local/benchmark-source-derived/qwen3-0.6b-release-vectors.json \
        --collection <collection> \
        --embedding-backend candidate ...
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

# Decision-rule text carries mathematical operators (>=, <=) that the Windows cp1252
# console cannot encode, so printing a real passage raised UnicodeEncodeError. Reconfigure
# rather than transliterate: the passage is evidence and must print as stored.
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
from app.reasoning.gemini_adapters import (
    GeminiAdapterParameters,
    GeminiGenerationAdapter,
)
from app.reasoning.generation_adapters import AnthropicGenerationAdapter
from app.reasoning.generation_schemas import (
    GenerationAdapterParameters,
    GenerationEffort,
    GenerationProvider,
)
from app.reasoning.presentation import PassagePresenter
from app.reasoning.retrieval_service import ServingRetrievalService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--qdrant-api-key", default=None)
    parser.add_argument("--qdrant-timeout", type=float, default=30.0)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--candidate-limit", type=int, default=100)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--expand", action="store_true", help="add deterministic expansions")
    parser.add_argument("--generate", action="store_true", help="call the model")
    parser.add_argument(
        "--generation-provider",
        choices=("claude", "gemini"),
        default="claude",
        help="which model backs --generate; claude is the intended production lane",
    )
    parser.add_argument("--show-text", type=int, default=900, help="passage chars to print")
    _add_embedding_backend_arguments(parser)
    return parser


def vertex_parameters() -> GenerationAdapterParameters:
    """Read the Vertex binding from the environment.

    Deliberately explicit: an unset project or region is a configuration error, not a
    default to guess at, because guessing would silently bill the wrong project.
    """
    project = os.environ.get("MEDRAG_VERTEX_PROJECT_ID")
    region = os.environ.get("MEDRAG_VERTEX_REGION", "global")
    model_id = os.environ.get("MEDRAG_GENERATION_MODEL_ID", "claude-opus-5")
    if not project:
        raise SystemExit(
            "MEDRAG_VERTEX_PROJECT_ID is not set. Configure it (and run "
            "`gcloud auth application-default login`) before using --generate."
        )
    return GenerationAdapterParameters(
        provider=GenerationProvider.GCP_VERTEX,
        model_id=model_id,
        max_tokens=int(os.environ.get("MEDRAG_GENERATION_MAX_TOKENS", "16000")),
        effort=GenerationEffort(os.environ.get("MEDRAG_GENERATION_EFFORT", "high")),
        gcp_project_id=project,
        gcp_region=region,
    )


def gemini_parameters() -> GeminiAdapterParameters:
    """Read the Gemini comparator binding from the environment.

    Shares the Vertex project and region with the Claude lane: this reaches Gemini
    through Vertex, not the Gemini API, so it needs the same ADC and no API key.
    """
    project = os.environ.get("MEDRAG_VERTEX_PROJECT_ID")
    region = os.environ.get("MEDRAG_VERTEX_REGION", "global")
    model_id = os.environ.get("MEDRAG_GEMINI_MODEL_ID", "gemini-flash-latest")
    if not project:
        raise SystemExit(
            "MEDRAG_VERTEX_PROJECT_ID is not set. Configure it (and run "
            "`gcloud auth application-default login`) before using --generate."
        )
    seed = os.environ.get("MEDRAG_GEMINI_SEED")
    return GeminiAdapterParameters(
        model_id=model_id,
        gcp_project_id=project,
        gcp_region=region,
        max_output_tokens=int(os.environ.get("MEDRAG_GENERATION_MAX_TOKENS", "8192")),
        temperature=float(os.environ.get("MEDRAG_GEMINI_TEMPERATURE", "0.0")),
        seed=int(seed) if seed else None,
    )


async def main() -> int:
    arguments = build_parser().parse_args()

    bundle, vectors = _load_index_inputs(arguments)
    evidence = {record.evidence_id: record for record in bundle.evidence}
    print(f"release  : {vectors.content.corpus_release_id}")
    print(f"evidence : {len(evidence)} approved records")
    print(f"dense    : {vectors.content.dense.name} ({vectors.content.dense.dimension}d)")
    print(f"sparse   : {vectors.content.sparse.name}")

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
    # Built once, here, rather than lazily inside the first `retrieve`: it scans the
    # release for the decision-table header rows it takes column labels from, and that
    # is release-load work, not question-latency work.
    started_presenter = time.perf_counter()
    presenter = PassagePresenter.for_release(evidence.values())
    print(f"presenter: {(time.perf_counter() - started_presenter) * 1000:.0f} ms to build")

    service = ServingRetrievalService(
        qdrant,
        backend,
        presenter=presenter,
        candidate_limit=arguments.candidate_limit,
        top_k=arguments.top_k,
        rrf_k=arguments.rrf_k,
    )

    expansions: tuple = ()
    if arguments.expand:
        # Same revisions the accepted conflict-aware candidate was measured with.
        expander = DeterministicQueryExpander(
            terminology_revision=EXACT_TERMINOLOGY_REVISION,
            safety_query_revision=CONFLICT_AWARE_QUERY_REVISION,
        )
        expansions = expander.expand(arguments.question)
    result = await service.retrieve(
        arguments.question,
        collection=arguments.collection,
        corpus_release_id=vectors.content.corpus_release_id,
        dense_vector_name=vectors.content.dense.name,
        sparse_vector_name=vectors.content.sparse.name,
        evidence=evidence,
        expansions=expansions,
    )

    print()
    print(f"question : {arguments.question}")
    print(f"latency  : {result.latency_ms:.0f} ms")
    print(f"passages : {len(result.passages)}")
    if result.suppressed_duplicate_count:
        print(f"duplicates suppressed: {result.suppressed_duplicate_count}")
    if result.expansions_used:
        print(f"expansions: {len(result.expansions_used)}")
    if result.lane_failures:
        print(f"lane failures: {', '.join(result.lane_failures)}")
    if result.disqualified_role_claims:
        # Printed even when the answer still goes through: this is the corpus labelling
        # a role it cannot support, and it should be visible either way.
        print(
            f"role claims not counted: {len(result.disqualified_role_claims)} "
            "(declared by passages that cannot carry a recommendation)"
        )
    if result.missing_required_roles:
        print(
            "MISSING REQUIRED ROLES: "
            + ", ".join(role.value for role in result.missing_required_roles)
        )
    print(f"answerable: {result.is_answerable}")
    print()

    disqualified = {
        evidence_id for evidence_id, _ in result.disqualified_role_claims
    }
    for rank, passage in enumerate(result.passages, start=1):
        roles = ",".join(role.value for role in passage.evidence_roles)
        print(f"[{rank}] {passage.evidence_id}  score={passage.fused_score:.5f}")
        print(f"     lanes={'+'.join(passage.lanes)}  roles={roles}")
        note = "" if passage.evidence_id not in disqualified else "  (role not counted)"
        print(f"     kind={passage.kind.value}{note}")
        if passage.duplicates_suppressed:
            print(f"     duplicates: {', '.join(passage.duplicates_suppressed)}")
        body = passage.rendered_text[: arguments.show_text]
        for line in body.split("\n"):
            print(f"     {line}")
        print()

    if not arguments.generate:
        print("(retrieval only; pass --generate to compose an answer)")
        return 0

    if not result.is_answerable:
        # The gate is the point: an incomplete evidence set never reaches the model.
        print("ABSTAINED before generation: required evidence roles are missing.")
        return 0

    if arguments.generation_provider == "gemini":
        # Development comparator while the anthropic-* base models have no Vertex
        # quota. Every grounding rule below is unchanged by the swap.
        backend = GeminiGenerationAdapter(gemini_parameters())
    else:
        backend = AnthropicGenerationAdapter(vertex_parameters())
    print(f"model    : {arguments.generation_provider}")
    composer = GroundedAnswerComposer(backend)
    composed = await composer.compose(
        arguments.question,
        tuple(
            RetrievedPassage(
                # The rendered view, not `content_exact`: a model handed
                # `A145=HIV.D8 ...` spends its attention on column letters. The citation
                # is bound by `evidence_id`, so it still resolves to the exact approved
                # record.
                evidence_id=passage.evidence_id,
                text=passage.rendered_text,
                evidence_roles=tuple(role.value for role in passage.evidence_roles),
                source_version_label=passage.source_version_id,
            )
            for passage in result.passages
        ),
    )

    print("=" * 72)
    if composed.abstention is not None:
        print(f"ABSTAINED: {composed.abstention.reason_code}")
        print(composed.abstention.message)
        return 0
    for claim in composed.claims:
        print(f"- {claim.text}")
        print(f"  cites: {', '.join(claim.evidence_ids)}")
    for conflict in composed.conflicts:
        cited = ", ".join(conflict.evidence_ids)
        print(f"! conflict [{conflict.conflict_type}]: {conflict.summary}")
        if cited:
            print(f"  between: {cited}")
    verification = composed.verification
    print()
    print(
        f"claims rendered={verification.rendered_claims} "
        f"supported={verification.supported_claims} "
        f"withheld={verification.withheld_claims}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
