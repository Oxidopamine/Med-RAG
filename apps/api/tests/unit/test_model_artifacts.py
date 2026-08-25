import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from app.corpus_steward.cli import model_artifact_schema_document
from app.corpus_steward.index_schemas import SparseVector
from app.corpus_steward.model_artifacts import (
    ModelArtifactError,
    ModelArtifactKind,
    ModelArtifactManifest,
    build_model_artifact_manifest,
    verify_model_artifact,
)
from app.corpus_steward.vector_producer import (
    EmbeddingExecutionPolicy,
    ProductionEmbeddingBackend,
    RetryableEmbeddingError,
    VectorBatchProducer,
    VectorProductionError,
)

SCHEMA_PATH = (
    Path(__file__).parents[4]
    / "packages"
    / "schemas"
    / "model-artifact-manifest-1.0.0.schema.json"
)


def _artifact(
    tmp_path: Path,
    name: str,
    kind: ModelArtifactKind,
    dimension: int,
):
    root = tmp_path / name
    root.mkdir()
    (root / "config.json").write_text(
        json.dumps({"architecture": name}, sort_keys=True), encoding="utf-8"
    )
    weights = root / "weights"
    weights.mkdir()
    (weights / "model.bin").write_bytes(f"weights:{name}".encode())
    manifest = build_model_artifact_manifest(
        root,
        artifact_kind=kind,
        model_id=f"example/{name}",
        revision="0123456789abcdef0123456789abcdef01234567",
        dimension=dimension,
        adapter_id=f"med-rag/{name}-adapter",
        adapter_revision="1.0.0",
        adapter_parameters={"normalize": True, "prompt": "search_document"},
    )
    return root, manifest, verify_model_artifact(
        root, manifest, expected_artifact_sha256=manifest.artifact_sha256
    )


def test_model_artifact_manifest_binds_bytes_identity_and_adapter_contract(
    tmp_path: Path,
) -> None:
    root, manifest, verified = _artifact(
        tmp_path, "dense", ModelArtifactKind.DENSE, 3
    )

    assert verified.root == root.resolve()
    assert verified.reference == manifest.model_reference()
    assert [item.path for item in manifest.content.files] == [
        "config.json",
        "weights/model.bin",
    ]
    changed_contract = ModelArtifactManifest.seal(
        manifest.content.model_copy(update={"adapter_revision": "1.0.1"})
    )
    assert changed_contract.artifact_sha256 != manifest.artifact_sha256


def test_model_artifact_verification_rejects_content_and_inventory_drift(
    tmp_path: Path,
) -> None:
    root, manifest, _ = _artifact(tmp_path, "dense", ModelArtifactKind.DENSE, 3)
    (root / "weights" / "model.bin").write_bytes(b"altered")

    with pytest.raises(ModelArtifactError, match="size|digest"):
        verify_model_artifact(
            root, manifest, expected_artifact_sha256=manifest.artifact_sha256
        )

    (root / "weights" / "model.bin").write_bytes(b"weights:dense")
    (root / "unexpected.txt").write_text("drift", encoding="utf-8")
    with pytest.raises(ModelArtifactError, match="inventory mismatch"):
        verify_model_artifact(
            root, manifest, expected_artifact_sha256=manifest.artifact_sha256
        )


def test_model_artifact_verification_requires_independent_digest_pin(
    tmp_path: Path,
) -> None:
    root, manifest, _ = _artifact(tmp_path, "dense", ModelArtifactKind.DENSE, 3)

    with pytest.raises(ModelArtifactError, match="independently pinned"):
        verify_model_artifact(
            root, manifest, expected_artifact_sha256="0" * 64
        )


class _DenseAdapter:
    def __init__(self, artifact, *, transient_failures: int = 0) -> None:
        self._artifact = artifact
        self.transient_failures = transient_failures
        self.document_calls = 0
        self.query_calls = 0

    @property
    def artifact(self):
        return self._artifact

    async def embed_documents(
        self, texts: Sequence[str]
    ) -> Sequence[Sequence[float]]:
        self.document_calls += 1
        if self.transient_failures:
            self.transient_failures -= 1
            raise RetryableEmbeddingError("capacity")
        return tuple((1.0, 0.0, 0.0) for _ in texts)

    async def embed_queries(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        self.query_calls += 1
        return tuple((0.0, 1.0, 0.0) for _ in texts)


class _SparseAdapter:
    def __init__(self, artifact) -> None:
        self._artifact = artifact
        self.document_calls = 0
        self.query_calls = 0

    @property
    def artifact(self):
        return self._artifact

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[SparseVector]:
        self.document_calls += 1
        return tuple(SparseVector(indices=(2,), values=(2.0,)) for _ in texts)

    async def embed_queries(self, texts: Sequence[str]) -> Sequence[SparseVector]:
        self.query_calls += 1
        return tuple(SparseVector(indices=(2,), values=(1.0,)) for _ in texts)


async def test_production_backend_uses_verified_asymmetric_adapters_and_bounded_retry(
    tmp_path: Path,
) -> None:
    _, _, dense_artifact = _artifact(tmp_path, "dense", ModelArtifactKind.DENSE, 3)
    _, _, sparse_artifact = _artifact(tmp_path, "sparse", ModelArtifactKind.SPARSE, 8)
    dense = _DenseAdapter(dense_artifact, transient_failures=1)
    sparse = _SparseAdapter(sparse_artifact)
    backend = ProductionEmbeddingBackend(
        dense,
        sparse,
        policy=EmbeddingExecutionPolicy(
            max_batch_size=2,
            timeout_seconds=1,
            max_attempts=2,
            retry_delay_seconds=0,
        ),
    )

    documents = await backend.embed_documents(("first", "second"))
    queries = await backend.embed_queries(("question",))

    assert dense.document_calls == 2
    assert dense.query_calls == 1
    assert sparse.document_calls == 1
    assert sparse.query_calls == 1
    assert documents[0].dense == (1.0, 0.0, 0.0)
    assert documents[0].sparse.values == (2.0,)
    assert queries[0].dense == (0.0, 1.0, 0.0)
    assert queries[0].sparse.values == (1.0,)
    assert backend.dense_definition.model == dense_artifact.reference
    assert backend.sparse_definition.model == sparse_artifact.reference

    with pytest.raises(VectorProductionError, match="batch-size limit"):
        await backend.embed_documents(("one", "two", "three"))
    with pytest.raises(ValueError, match="producer batch size"):
        VectorBatchProducer(backend, batch_size=3)


async def test_production_backend_never_retries_unclassified_adapter_failures(
    tmp_path: Path,
) -> None:
    _, _, dense_artifact = _artifact(tmp_path, "dense", ModelArtifactKind.DENSE, 3)
    _, _, sparse_artifact = _artifact(tmp_path, "sparse", ModelArtifactKind.SPARSE, 8)

    class BrokenDense(_DenseAdapter):
        async def embed_documents(
            self, texts: Sequence[str]
        ) -> Sequence[Sequence[float]]:
            self.document_calls += 1
            raise RuntimeError("invalid model state")

    dense = BrokenDense(dense_artifact)
    backend = ProductionEmbeddingBackend(
        dense,
        _SparseAdapter(sparse_artifact),
        policy=EmbeddingExecutionPolicy(max_attempts=5, retry_delay_seconds=0),
    )

    with pytest.raises(VectorProductionError, match="non-retryable"):
        await backend.embed_documents(("text",))
    assert dense.document_calls == 1


def test_checked_in_model_artifact_schema_matches_versioned_contract() -> None:
    assert json.loads(SCHEMA_PATH.read_text(encoding="utf-8")) == (
        model_artifact_schema_document()
    )
