"""Build the serving pipeline from settings, or explain why it cannot be built.

`main.py` should not know how to verify a model artifact digest or which vector name a
release was indexed under. This module holds that, behind one function, so the
application's startup path reads as a decision — serve or do not serve — rather than as
a wiring procedure.

## What this deliberately does not do

**It does not activate anything.** Activation is a signed gate: a release reaches
`ACTIVE` through an activation decision backed by a sealed-holdout acceptance record,
recorded in the singleton pointer. Opening that holdout to look at an answer would spend
a one-time custody-controlled claim, which is not a thing a development convenience gets
to do. So this path serves a release in state `VALIDATED` *without* the pointer, and
every release it hands out is stamped `RESEARCH_UNACTIVATED` with a null `activated_at`.
The governed pointer stays empty and `SQLCorpusReleaseRepository.active_release` keeps
returning `None`.

**It does not decide that an answer is safe.** The role-completeness gate and the
grounding checks are unchanged and unaware of this module; research serving changes which
release is reachable, never what survives verification.

## Why it fails closed and loudly

A half-configured serving path is worse than none: it produces an API that looks wired,
answers nothing, and reports a reason that describes the wrong layer. So every input is
required together, checked at startup, and a missing one raises rather than degrading to
an abstention a reader would misread as a corpus limit.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.reasoning.answer_service import GroundedAnswerComposer
from app.reasoning.retrieval_service import ServingRetrievalService
from app.reasoning.serving_pipeline import EvidenceDetailProvider, ReleaseServingPipeline
from app.schemas.corpus import (
    ActiveCorpusRelease,
    CorpusReleaseBundle,
    ReleaseServingMode,
)


class ServingConfigurationError(RuntimeError):
    """Serving was requested and cannot be built. Never recoverable at runtime."""


@dataclass(frozen=True)
class ServingRuntime:
    """A built pipeline and the release identity it serves, kept together.

    They travel as a pair because the pipeline validates every question against the
    release it was constructed for; handing `QuestionService` one without the other is
    the mismatch `ReleaseServingPipeline.retrieve` exists to refuse.
    """

    pipeline: ReleaseServingPipeline
    release: ActiveCorpusRelease

    async def active_release(self) -> ActiveCorpusRelease:
        """The provider shape `QuestionService` expects.

        Constant rather than a lookup: this release came from a file that was read once
        at startup, and re-reading it per question would let the answer's stated
        provenance drift from the evidence actually loaded in memory.
        """

        return self.release


def _require(value: object | None, name: str) -> object:
    if value is None:
        raise ServingConfigurationError(
            f"SERVING_ENABLED is set but {name.upper()} is not. Research serving needs "
            "the release bundle, its vector batch, the collection, and the model "
            "artifacts together; a partial configuration would produce an API that "
            "looks wired and answers nothing."
        )
    return value


def _embedding_namespace(settings: Settings) -> argparse.Namespace:
    """Adapt settings to what the steward's artifact verification expects.

    Reused rather than reimplemented on purpose. That code pins each adapter to a
    manifest digest and cross-checks it against the vector batch the index was built
    from, and a second implementation of a security check is a second chance to get it
    subtly weaker. The adaptation is a shim; the verification stays in one place.
    """

    return argparse.Namespace(
        embedding_backend=settings.serving_embedding_backend,
        dense_model_root=settings.serving_dense_model_root,
        dense_model_manifest=settings.serving_dense_model_manifest,
        dense_artifact_sha256=settings.serving_dense_artifact_sha256,
        sparse_model_root=settings.serving_sparse_model_root,
        sparse_model_manifest=settings.serving_sparse_model_manifest,
        sparse_artifact_sha256=settings.serving_sparse_artifact_sha256,
        device=None,
        embedding_timeout=120.0,
        embedding_max_attempts=2,
        embedding_retry_delay=0.25,
        embedding_max_retry_delay=2.0,
    )


def build_serving_runtime(
    settings: Settings,
    *,
    evidence_details_provider: EvidenceDetailProvider,
) -> ServingRuntime | None:
    """Build the research serving path, or return None when it is switched off.

    None is the ordinary state and not an error: the API runs without a serving path and
    abstains with RETRIEVAL_PIPELINE_NOT_CONFIGURED, which is an accurate description of
    that deployment rather than a failure of it.
    """

    if not settings.serving_enabled:
        return None

    # Imported here so that an API running without serving does not pay for torch, the
    # adapter registry, or the Qdrant client at import time.
    from app.corpus_steward.cli import _build_embedding_backend
    from app.corpus_steward.index_schemas import IndexVectorBatch
    from app.corpus_steward.qdrant_index import QdrantRESTClient
    from app.reasoning.presentation import PassagePresenter

    bundle_path = Path(
        str(_require(settings.serving_release_bundle_path, "serving_release_bundle_path"))
    )
    vectors_path = Path(str(_require(settings.serving_vectors_path, "serving_vectors_path")))
    collection = str(_require(settings.serving_qdrant_collection, "serving_qdrant_collection"))

    bundle = CorpusReleaseBundle.model_validate_json(bundle_path.read_text(encoding="utf-8"))
    vectors = IndexVectorBatch.model_validate_json(vectors_path.read_text(encoding="utf-8"))

    manifest = bundle.manifest.content
    if vectors.content.corpus_release_id != manifest.corpus_release_id:
        raise ServingConfigurationError(
            f"vector batch is for release {vectors.content.corpus_release_id} but the "
            f"bundle is release {manifest.corpus_release_id}. Serving a release against "
            "another release's vectors would attach one release's identity to another's "
            "evidence."
        )

    evidence = {record.evidence_id: record for record in bundle.evidence}
    backend = _build_embedding_backend(
        _embedding_namespace(settings),
        baseline_dense_dimension=vectors.content.dense.dimension,
        baseline_sparse_dimension=vectors.content.sparse.dimension,
        # One question at a time: this encodes a query, not a corpus.
        maximum_batch_size=1,
        expected_dense=vectors.content.dense,
        expected_sparse=vectors.content.sparse,
    )
    qdrant = QdrantRESTClient(
        settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        timeout_seconds=settings.qdrant_timeout_seconds,
    )
    # Built once here rather than lazily on the first question: the presenter scans the
    # release for the decision-table header rows it takes column labels from, which is
    # release-load work and does not belong inside a question's latency.
    presenter = PassagePresenter.for_release(evidence.values())
    retrieval = ServingRetrievalService(
        qdrant,
        backend,
        presenter=presenter,
        candidate_limit=settings.serving_candidate_limit,
        top_k=settings.serving_top_k,
        rrf_k=settings.serving_rrf_k,
    )
    composer = GroundedAnswerComposer(_generation_backend(settings))
    pipeline = ReleaseServingPipeline(
        corpus_release_id=manifest.corpus_release_id,
        qdrant_collection=collection,
        dense_vector_name=vectors.content.dense.name,
        sparse_vector_name=vectors.content.sparse.name,
        evidence=evidence,
        retrieval=retrieval,
        composer=composer,
        evidence_details_provider=evidence_details_provider,
    )
    release = ActiveCorpusRelease(
        corpus_release_id=manifest.corpus_release_id,
        manifest_sha256=bundle.manifest.manifest_sha256,
        # The collection actually served, not the one the manifest names. A release
        # indexed under several vector profiles has one collection per profile, and the
        # payload must describe where the answer's evidence was really found.
        qdrant_collection=collection,
        serving_mode=ReleaseServingMode.RESEARCH_UNACTIVATED,
    )
    return ServingRuntime(pipeline=pipeline, release=release)


def _vertex_project() -> tuple[str, str]:
    """The Vertex binding, read from the environment exactly as `scripts/ask.py` reads it.

    Kept identical on purpose: an operator who has the CLI working should not discover
    that the API wants the same credentials under different names. There is no default
    project, because guessing one would silently bill the wrong account.
    """

    project = os.environ.get("MEDRAG_VERTEX_PROJECT_ID")
    if not project:
        raise ServingConfigurationError(
            "SERVING_ENABLED is set but MEDRAG_VERTEX_PROJECT_ID is not. The serving "
            "path composes grounded answers and needs the Vertex binding plus "
            "application-default credentials."
        )
    return project, os.environ.get("MEDRAG_VERTEX_REGION", "global")


def _generation_backend(settings: Settings):
    """Construct the generation adapter named by settings.

    Fails on an unknown name rather than defaulting: silently falling back to the other
    provider would put an unintended model behind a clinical-shaped answer.
    """

    provider = settings.serving_generation_provider.strip().lower()
    project, region = _vertex_project()
    if provider == "gemini":
        from app.reasoning.gemini_adapters import (
            GeminiAdapterParameters,
            GeminiGenerationAdapter,
        )

        return GeminiGenerationAdapter(
            GeminiAdapterParameters(
                model_id=os.environ.get("MEDRAG_GEMINI_MODEL_ID", "gemini-flash-latest"),
                gcp_project_id=project,
                gcp_region=region,
                max_output_tokens=int(os.environ.get("MEDRAG_GENERATION_MAX_TOKENS", "8192")),
            )
        )
    if provider == "claude":
        from app.reasoning.generation_adapters import AnthropicGenerationAdapter
        from app.reasoning.generation_schemas import (
            GenerationAdapterParameters,
            GenerationEffort,
            GenerationProvider,
        )

        return AnthropicGenerationAdapter(
            GenerationAdapterParameters(
                provider=GenerationProvider.GCP_VERTEX,
                model_id=os.environ.get("MEDRAG_GENERATION_MODEL_ID", "claude-opus-5"),
                max_tokens=int(os.environ.get("MEDRAG_GENERATION_MAX_TOKENS", "16000")),
                effort=GenerationEffort(os.environ.get("MEDRAG_GENERATION_EFFORT", "high")),
                gcp_project_id=project,
                gcp_region=region,
            )
        )
    raise ServingConfigurationError(
        f"unknown SERVING_GENERATION_PROVIDER {settings.serving_generation_provider!r}; "
        "expected 'claude' or 'gemini'"
    )


__all__ = [
    "ServingConfigurationError",
    "ServingRuntime",
    "build_serving_runtime",
]
