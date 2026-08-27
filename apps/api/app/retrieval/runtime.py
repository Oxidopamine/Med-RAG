"""Build the serving retrieval stack from configuration, or refuse to build one.

The steward CLI already knows how to turn a sealed candidate plus verified local model
artifacts into an embedding backend. Serving needs exactly the same construction with
one difference in posture: the CLI is run by an engineer who can read an error, while
the API starts unattended. So every failure here is a configuration error raised at
startup, and a deployment that cannot build the stack starts with no engine at all and
abstains on every question rather than answering from an unpinned model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.corpus_steward.adapter_registry import default_adapter_registry
from app.corpus_steward.candidate_schemas import RetrievalCandidateManifest
from app.corpus_steward.model_artifacts import (
    ModelArtifactKind,
    ModelArtifactManifest,
    VerifiedModelArtifact,
    verify_model_artifact,
)
from app.corpus_steward.qdrant_index import QdrantRESTClient
from app.corpus_steward.reranking import Qwen3RerankerAdapter, RerankerBackend
from app.corpus_steward.vector_producer import (
    EmbeddingExecutionPolicy,
    ProductionEmbeddingBackend,
)
from app.reasoning.generation_adapters import (
    GENERATION_ADAPTER_ID,
    GENERATION_ADAPTER_REVISION,
    GenerationBackend,
    default_generation_registry,
)
from app.reasoning.generation_schemas import GenerationAdapterParameters
from app.retrieval.serving import (
    ReleaseReader,
    ServingConfigurationError,
    ServingRetrievalEngine,
)

# One question at a time. Serving latency is dominated by a single query encode, and a
# larger ceiling would only let a future caller batch unrelated questions together.
SERVING_EMBEDDING_BATCH_SIZE = 1


@dataclass(frozen=True)
class ServingStack:
    """Everything a question needs, or the reason there is nothing to answer with."""

    engine: ServingRetrievalEngine | None = None
    generation: GenerationBackend | None = None
    qdrant: QdrantRESTClient | None = None
    unavailable_reason: str | None = None

    async def close(self) -> None:
        if self.qdrant is not None:
            await self.qdrant.close()


def build_serving_stack(settings: Settings, releases: ReleaseReader) -> ServingStack:
    """Assemble the serving stack, reporting rather than raising when unconfigured.

    An unconfigured deployment is a normal state - the corpus pipeline is usable long
    before a candidate is chosen - so it produces a stack with no engine and a stated
    reason. A *misconfigured* one is not: partially specified artifacts, a candidate
    that does not match its adapters, or an unreadable manifest raise.
    """

    if settings.serving_candidate_path is None:
        return ServingStack(
            unavailable_reason="SERVING_CANDIDATE_PATH is not configured",
        )
    try:
        candidate = RetrievalCandidateManifest.model_validate_json(
            settings.serving_candidate_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise ServingConfigurationError(
            f"sealed retrieval candidate could not be loaded: {error}"
        ) from error

    dense = _verified_artifact(
        settings.serving_dense_model_root,
        settings.serving_dense_model_manifest,
        settings.serving_dense_artifact_sha256,
        kind=ModelArtifactKind.DENSE,
        label="dense",
    )
    sparse = _verified_artifact(
        settings.serving_sparse_model_root,
        settings.serving_sparse_model_manifest,
        settings.serving_sparse_artifact_sha256,
        kind=ModelArtifactKind.SPARSE,
        label="sparse",
    )
    registry = default_adapter_registry()
    backend = ProductionEmbeddingBackend(
        registry.create_dense(dense, device=settings.serving_embedding_device),
        registry.create_sparse(sparse),
        policy=EmbeddingExecutionPolicy(
            max_batch_size=SERVING_EMBEDDING_BATCH_SIZE,
            timeout_seconds=settings.serving_embedding_timeout_seconds,
            max_attempts=settings.serving_embedding_max_attempts,
            retry_delay_seconds=settings.serving_embedding_retry_delay_seconds,
            max_retry_delay_seconds=settings.serving_embedding_max_retry_delay_seconds,
        ),
    )
    reranker = _build_reranker(settings, candidate)

    qdrant = QdrantRESTClient(
        settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        timeout_seconds=settings.qdrant_timeout_seconds,
    )
    engine = ServingRetrievalEngine(
        qdrant, backend, candidate, releases, reranker=reranker
    )
    return ServingStack(
        engine=engine,
        generation=_build_generation(settings),
        qdrant=qdrant,
    )


def _build_reranker(
    settings: Settings, candidate: RetrievalCandidateManifest
) -> RerankerBackend | None:
    configured = candidate.content.reranker
    if configured is None:
        return None
    artifact = _verified_artifact(
        settings.serving_reranker_model_root,
        settings.serving_reranker_model_manifest,
        settings.serving_reranker_artifact_sha256,
        kind=ModelArtifactKind.RERANKER,
        label="reranker",
    )
    if artifact.reference != configured.model:
        raise ServingConfigurationError(
            "reranker artifact does not match the sealed candidate pin"
        )
    return Qwen3RerankerAdapter(artifact, device=settings.serving_reranker_device)


def _build_generation(settings: Settings) -> GenerationBackend | None:
    """Load the sealed generation parameters, if this deployment has any.

    A missing generation lane is survivable: retrieval still runs and the question
    abstains with a stated reason. A malformed one is not, because the alternative is
    a process that looks configured and silently never answers.
    """

    if settings.serving_generation_parameters_path is None:
        return None
    try:
        parameters = GenerationAdapterParameters.model_validate_json(
            settings.serving_generation_parameters_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise ServingConfigurationError(
            f"sealed generation parameters could not be loaded: {error}"
        ) from error
    return default_generation_registry().build(
        GENERATION_ADAPTER_ID, GENERATION_ADAPTER_REVISION, parameters
    )


def _verified_artifact(
    root: Path | None,
    manifest_path: Path | None,
    expected_sha256: str | None,
    *,
    kind: ModelArtifactKind,
    label: str,
) -> VerifiedModelArtifact:
    missing = [
        name
        for name, value in (
            ("root", root),
            ("manifest", manifest_path),
            ("artifact digest", expected_sha256),
        )
        if value is None
    ]
    if missing:
        raise ServingConfigurationError(
            f"{label} model artifact is missing its {', '.join(missing)}"
        )
    assert root is not None and manifest_path is not None and expected_sha256 is not None
    try:
        manifest = ModelArtifactManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise ServingConfigurationError(
            f"{label} model artifact manifest could not be loaded: {error}"
        ) from error
    return verify_model_artifact(
        root,
        manifest,
        expected_artifact_sha256=expected_sha256,
        expected_kind=kind,
    )
