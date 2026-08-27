"""Allowlisted adapter dispatch driven only by verified artifact manifests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.corpus_steward.embedding_adapters import (
    BGE_M3_ADAPTER_ID,
    BGE_M3_ADAPTER_REVISION,
    BM25_ADAPTER_ID,
    BM25_ADAPTER_REVISION,
    MEDCPT_ADAPTER_ID,
    MEDCPT_ADAPTER_REVISION,
    QWEN3_ADAPTER_ID,
    QWEN3_ADAPTER_REVISION,
    QWEN3_OPENVINO_ADAPTER_ID,
    QWEN3_OPENVINO_ADAPTER_REVISION,
    AdapterConfigurationError,
    BGEM3DenseAdapter,
    MedCPTDualEncoderAdapter,
    QdrantBM25SparseAdapter,
    Qwen3DenseAdapter,
    Qwen3OpenVINODenseAdapter,
)
from app.corpus_steward.model_artifacts import (
    ModelArtifactKind,
    VerifiedModelArtifact,
    VerifiedModelArtifactPair,
)
from app.corpus_steward.reranking import (
    BGE_RERANKER_V2_M3_ADAPTER_ID,
    BGE_RERANKER_V2_M3_ADAPTER_REVISION,
    MEDCPT_CROSS_ENCODER_ADAPTER_ID,
    MEDCPT_CROSS_ENCODER_ADAPTER_REVISION,
    QWEN3_RERANKER_ADAPTER_ID,
    QWEN3_RERANKER_ADAPTER_REVISION,
    BGERerankerV2M3Adapter,
    MedCPTCrossEncoderAdapter,
    Qwen3RerankerAdapter,
    RerankerBackend,
)
from app.corpus_steward.vector_producer import DenseEmbeddingAdapter, SparseEmbeddingAdapter

DenseFactory = Callable[..., DenseEmbeddingAdapter]
DensePairFactory = Callable[..., DenseEmbeddingAdapter]
SparseFactory = Callable[..., SparseEmbeddingAdapter]
RerankerFactory = Callable[..., RerankerBackend]


class ManifestAdapterRegistry:
    """Fail-closed registry keyed by sealed kind, adapter ID, and adapter revision."""

    def __init__(self) -> None:
        self._dense: dict[tuple[str, str], DenseFactory] = {}
        self._dense_pair: dict[tuple[str, str], DensePairFactory] = {}
        self._sparse: dict[tuple[str, str], SparseFactory] = {}
        self._reranker: dict[tuple[str, str], RerankerFactory] = {}

    def register_dense(
        self, adapter_id: str, adapter_revision: str, factory: DenseFactory
    ) -> None:
        self._register(self._dense, adapter_id, adapter_revision, factory)

    def register_dense_pair(
        self, adapter_id: str, adapter_revision: str, factory: DensePairFactory
    ) -> None:
        """Allowlist an adapter that is only ever built from two bound artifact roots."""

        self._register(self._dense_pair, adapter_id, adapter_revision, factory)

    def register_sparse(
        self, adapter_id: str, adapter_revision: str, factory: SparseFactory
    ) -> None:
        self._register(self._sparse, adapter_id, adapter_revision, factory)

    def register_reranker(
        self, adapter_id: str, adapter_revision: str, factory: RerankerFactory
    ) -> None:
        self._register(self._reranker, adapter_id, adapter_revision, factory)

    @staticmethod
    def _register(
        registrations: dict[tuple[str, str], Callable[..., Any]],
        adapter_id: str,
        adapter_revision: str,
        factory: Callable[..., Any],
    ) -> None:
        key = (adapter_id, adapter_revision)
        if not adapter_id or not adapter_revision:
            raise ValueError("adapter registry keys cannot be empty")
        if key in registrations:
            raise ValueError(f"adapter is already registered: {adapter_id}@{adapter_revision}")
        registrations[key] = factory

    def create_dense(
        self, artifact: VerifiedModelArtifact, **runtime_options: Any
    ) -> DenseEmbeddingAdapter:
        return self._create(
            artifact,
            expected_kind=ModelArtifactKind.DENSE,
            registrations=self._dense,
            runtime_options=runtime_options,
        )

    def create_dense_pair(
        self, artifact: VerifiedModelArtifactPair, **runtime_options: Any
    ) -> DenseEmbeddingAdapter:
        """Build a paired dense adapter from one verified two-root candidate identity.

        Paired and single-artifact adapters are allowlisted in separate tables, so half
        of a dual encoder can never be dispatched as a symmetric dense model, and a
        symmetric model can never be dispatched as one half of a pair.
        """

        return self._create(
            artifact,
            expected_kind=ModelArtifactKind.DENSE,
            registrations=self._dense_pair,
            runtime_options=runtime_options,
        )

    def create_sparse(
        self, artifact: VerifiedModelArtifact, **runtime_options: Any
    ) -> SparseEmbeddingAdapter:
        return self._create(
            artifact,
            expected_kind=ModelArtifactKind.SPARSE,
            registrations=self._sparse,
            runtime_options=runtime_options,
        )

    def create_reranker(
        self, artifact: VerifiedModelArtifact, **runtime_options: Any
    ) -> RerankerBackend:
        return self._create(
            artifact,
            expected_kind=ModelArtifactKind.RERANKER,
            registrations=self._reranker,
            runtime_options=runtime_options,
        )

    @staticmethod
    def _create(
        artifact: VerifiedModelArtifact | VerifiedModelArtifactPair,
        *,
        expected_kind: ModelArtifactKind,
        registrations: dict[tuple[str, str], Callable[..., Any]],
        runtime_options: dict[str, Any],
    ):
        content = artifact.manifest.content
        if content.artifact_kind is not expected_kind:
            raise AdapterConfigurationError(
                f"cannot create {expected_kind.value.lower()} adapter from "
                f"{content.artifact_kind.value.lower()} artifact"
            )
        key = (content.adapter_id, content.adapter_revision)
        factory = registrations.get(key)
        if factory is None:
            raise AdapterConfigurationError(
                "artifact declares an adapter that is not in the local allowlist: "
                f"{content.adapter_id}@{content.adapter_revision}"
            )
        try:
            return factory(artifact, **runtime_options)
        except TypeError as error:
            raise AdapterConfigurationError(
                f"unsupported runtime option for {content.adapter_id}"
            ) from error


def default_adapter_registry() -> ManifestAdapterRegistry:
    registry = ManifestAdapterRegistry()
    registry.register_dense(
        QWEN3_ADAPTER_ID,
        QWEN3_ADAPTER_REVISION,
        Qwen3DenseAdapter,
    )
    registry.register_dense(
        QWEN3_OPENVINO_ADAPTER_ID,
        QWEN3_OPENVINO_ADAPTER_REVISION,
        Qwen3OpenVINODenseAdapter,
    )
    registry.register_dense(
        BGE_M3_ADAPTER_ID,
        BGE_M3_ADAPTER_REVISION,
        BGEM3DenseAdapter,
    )
    registry.register_dense_pair(
        MEDCPT_ADAPTER_ID,
        MEDCPT_ADAPTER_REVISION,
        MedCPTDualEncoderAdapter,
    )
    registry.register_sparse(
        BM25_ADAPTER_ID,
        BM25_ADAPTER_REVISION,
        QdrantBM25SparseAdapter,
    )
    registry.register_reranker(
        QWEN3_RERANKER_ADAPTER_ID,
        QWEN3_RERANKER_ADAPTER_REVISION,
        Qwen3RerankerAdapter,
    )
    registry.register_reranker(
        BGE_RERANKER_V2_M3_ADAPTER_ID,
        BGE_RERANKER_V2_M3_ADAPTER_REVISION,
        BGERerankerV2M3Adapter,
    )
    registry.register_reranker(
        MEDCPT_CROSS_ENCODER_ADAPTER_ID,
        MEDCPT_CROSS_ENCODER_ADAPTER_REVISION,
        MedCPTCrossEncoderAdapter,
    )
    return registry
