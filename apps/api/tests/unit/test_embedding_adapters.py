import json
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from app.corpus_steward import adapter_registry, cli
from app.corpus_steward.adapter_registry import ManifestAdapterRegistry
from app.corpus_steward.embedding_adapters import (
    BGE_M3_ADAPTER_ID,
    BGE_M3_ADAPTER_REVISION,
    BGE_M3_DIMENSION,
    BGE_M3_MODEL_ID,
    BGE_M3_MODEL_REVISION,
    BM25_ADAPTER_ID,
    BM25_ADAPTER_REVISION,
    BM25_MODEL_ID,
    BM25_MODEL_REVISION,
    QWEN3_ADAPTER_ID,
    QWEN3_ADAPTER_REVISION,
    QWEN3_MODEL_DIMENSIONS,
    QWEN3_OPENVINO_ADAPTER_ID,
    QWEN3_OPENVINO_ADAPTER_REVISION,
    AdapterConfigurationError,
    BGEM3DenseAdapter,
    QdrantBM25SparseAdapter,
    Qwen3DenseAdapter,
    Qwen3OpenVINODenseAdapter,
    _TorchTransformerRuntime,
    unicode_medical_tokens,
)
from app.corpus_steward.model_artifacts import (
    ModelArtifactKind,
    build_model_artifact_manifest,
    verify_model_artifact,
)
from app.corpus_steward.vector_producer import ProductionEmbeddingBackend

FIXTURE_PATH = Path(__file__).parents[4] / "data" / "fixtures" / "corpus-release-v1.json"


def _dense_parameters(*, batch_size: int = 2, device: str = "cpu") -> dict[str, object]:
    return {
        "pooling": "cls",
        "normalize": True,
        "query_prefix": "query:",
        "document_prefix": "passage:",
        "max_length": 512,
        "batch_size": batch_size,
        "device": device,
        "dtype": "float32",
    }


def _sparse_parameters() -> dict[str, object]:
    return {
        "tokenizer": "unicode-medical-v1",
        "k1": 1.2,
        "b": 0.75,
        "average_document_length": 10.0,
        "query_term_frequency": "binary",
        "hash_algorithm": "sha256-uint32-le",
        "hash_seed": "med-rag-bm25-v1",
        "query_prefix": "",
        "document_prefix": "",
        "stopwords_path": "stopwords.txt",
    }


def _qwen_parameters(*, batch_size: int = 2) -> dict[str, object]:
    return {
        "pooling": "last_token",
        "normalize": True,
        "query_instruction": (
            "Given a clinical question, retrieve authoritative guideline evidence "
            "needed for a complete and safe answer"
        ),
        "document_prefix": "",
        "padding_side": "left",
        "max_length": 8192,
        "batch_size": batch_size,
        "device": "cpu",
        "dtype": "float32",
    }


def _qwen_openvino_parameters(*, batch_size: int = 2) -> dict[str, object]:
    parameters = _qwen_parameters(batch_size=batch_size)
    parameters.update(
        {
            "device": "CPU",
            "dtype": "int8",
            "weight_format": "int8",
            "openvino_version": "2025.3.0",
            "optimum_intel_version": "1.25.2",
            "nncf_version": "2.18.0",
        }
    )
    return parameters


def _verified_artifact(
    tmp_path: Path,
    *,
    name: str,
    kind: ModelArtifactKind,
    model_id: str,
    dimension: int,
    adapter_id: str,
    adapter_revision: str,
    parameters: dict[str, object],
    revision: str | None = None,
):
    root = tmp_path / name
    root.mkdir()
    (root / "config.json").write_text("{}\n", encoding="utf-8")
    if kind is ModelArtifactKind.SPARSE:
        (root / "stopwords.txt").write_text("# sealed stopwords\nthe\n", encoding="utf-8")
    manifest = build_model_artifact_manifest(
        root,
        artifact_kind=kind,
        model_id=model_id,
        revision=revision
        or (BGE_M3_MODEL_REVISION if model_id == BGE_M3_MODEL_ID else BM25_MODEL_REVISION),
        dimension=dimension,
        adapter_id=adapter_id,
        adapter_revision=adapter_revision,
        adapter_parameters=parameters,
    )
    return verify_model_artifact(
        root,
        manifest,
        expected_artifact_sha256=manifest.artifact_sha256,
        expected_kind=kind,
    )


class _DenseRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], int, str, bool]] = []

    def encode(
        self,
        texts: Sequence[str],
        *,
        max_length: int,
        pooling: str,
        normalize: bool,
    ) -> Sequence[Sequence[float]]:
        self.calls.append((tuple(texts), max_length, pooling, normalize))
        return tuple(
            (float(len(text)),) + (0.0,) * (BGE_M3_DIMENSION - 1)
            for text in texts
        )


class _QwenRuntime:
    def __init__(self, dimension: int) -> None:
        self.dimension = dimension
        self.calls: list[tuple[tuple[str, ...], int, str, bool]] = []

    def encode(
        self,
        texts: Sequence[str],
        *,
        max_length: int,
        pooling: str,
        normalize: bool,
    ) -> Sequence[Sequence[float]]:
        self.calls.append((tuple(texts), max_length, pooling, normalize))
        return tuple(tuple(float(index + 1) for index in range(self.dimension)) for _ in texts)


async def test_bge_m3_adapter_applies_roles_and_sealed_micro_batching(
    tmp_path: Path,
) -> None:
    artifact = _verified_artifact(
        tmp_path,
        name="dense",
        kind=ModelArtifactKind.DENSE,
        model_id=BGE_M3_MODEL_ID,
        dimension=BGE_M3_DIMENSION,
        adapter_id=BGE_M3_ADAPTER_ID,
        adapter_revision=BGE_M3_ADAPTER_REVISION,
        parameters=_dense_parameters(batch_size=2),
    )
    runtime = _DenseRuntime()
    adapter = BGEM3DenseAdapter(artifact, runtime=runtime)

    documents = await adapter.embed_documents(("one", "two", "three"))
    queries = await adapter.embed_queries(("dose",))

    assert len(documents) == 3
    assert len(documents[0]) == BGE_M3_DIMENSION
    assert len(runtime.calls) == 3
    assert runtime.calls[0] == (
        ("passage: one", "passage: two"),
        512,
        "cls",
        True,
    )
    assert runtime.calls[1][0] == ("passage: three",)
    assert runtime.calls[2][0] == ("query: dose",)
    assert len(queries) == 1


def test_bge_m3_adapter_rejects_unsealed_device_or_wrong_model(tmp_path: Path) -> None:
    artifact = _verified_artifact(
        tmp_path,
        name="dense",
        kind=ModelArtifactKind.DENSE,
        model_id=BGE_M3_MODEL_ID,
        dimension=BGE_M3_DIMENSION,
        adapter_id=BGE_M3_ADAPTER_ID,
        adapter_revision=BGE_M3_ADAPTER_REVISION,
        parameters=_dense_parameters(),
    )
    with pytest.raises(AdapterConfigurationError, match="does not match"):
        BGEM3DenseAdapter(artifact, device="cuda", runtime=_DenseRuntime())

    wrong_artifact = _verified_artifact(
        tmp_path,
        name="wrong-dense",
        kind=ModelArtifactKind.DENSE,
        model_id="example/not-bge-m3",
        dimension=BGE_M3_DIMENSION,
        adapter_id=BGE_M3_ADAPTER_ID,
        adapter_revision=BGE_M3_ADAPTER_REVISION,
        parameters=_dense_parameters(),
    )
    with pytest.raises(AdapterConfigurationError, match="requires model_id"):
        BGEM3DenseAdapter(wrong_artifact, runtime=_DenseRuntime())


async def test_qwen3_family_applies_instruction_last_token_and_mrl_dimension(
    tmp_path: Path,
) -> None:
    model_id = "Qwen/Qwen3-Embedding-0.6B"
    artifact = _verified_artifact(
        tmp_path,
        name="qwen-dense",
        kind=ModelArtifactKind.DENSE,
        model_id=model_id,
        revision="72bb2d1e482afe83dcebe9496edc693ad1967a0f",
        dimension=256,
        adapter_id=QWEN3_ADAPTER_ID,
        adapter_revision=QWEN3_ADAPTER_REVISION,
        parameters=_qwen_parameters(),
    )
    runtime = _QwenRuntime(QWEN3_MODEL_DIMENSIONS[model_id])
    adapter = Qwen3DenseAdapter(artifact, runtime=runtime)

    query = (await adapter.embed_queries(("What monitoring is required?",)))[0]
    document = (await adapter.embed_documents(("Monitor renal function.",)))[0]

    assert len(query) == 256
    assert sum(value * value for value in query) == pytest.approx(1.0)
    assert len(document) == 256
    assert runtime.calls[0][0][0].startswith("Instruct: Given a clinical question")
    assert runtime.calls[0][0][0].endswith("\nQuery:What monitoring is required?")
    assert runtime.calls[0][1:] == (8192, "last_token", False)
    assert runtime.calls[1][0] == ("Monitor renal function.",)


def test_manifest_registry_is_allowlisted_and_qwen_first(tmp_path: Path) -> None:
    model_id = "Qwen/Qwen3-Embedding-0.6B"
    artifact = _verified_artifact(
        tmp_path,
        name="qwen-registry",
        kind=ModelArtifactKind.DENSE,
        model_id=model_id,
        revision="72bb2d1e482afe83dcebe9496edc693ad1967a0f",
        dimension=1024,
        adapter_id=QWEN3_ADAPTER_ID,
        adapter_revision=QWEN3_ADAPTER_REVISION,
        parameters=_qwen_parameters(),
    )
    runtime = _QwenRuntime(QWEN3_MODEL_DIMENSIONS[model_id])

    built = adapter_registry.default_adapter_registry().create_dense(
        artifact, runtime=runtime
    )

    assert isinstance(built, Qwen3DenseAdapter)
    empty = ManifestAdapterRegistry()
    with pytest.raises(AdapterConfigurationError, match="not in the local allowlist"):
        empty.create_dense(artifact)


async def test_qwen3_openvino_adapter_preserves_qwen_embedding_semantics(
    tmp_path: Path,
) -> None:
    model_id = "Qwen/Qwen3-Embedding-0.6B"
    artifact = _verified_artifact(
        tmp_path,
        name="qwen-openvino",
        kind=ModelArtifactKind.DENSE,
        model_id=model_id,
        revision="72bb2d1e482afe83dcebe9496edc693ad1967a0f",
        dimension=256,
        adapter_id=QWEN3_OPENVINO_ADAPTER_ID,
        adapter_revision=QWEN3_OPENVINO_ADAPTER_REVISION,
        parameters=_qwen_openvino_parameters(),
    )
    runtime = _QwenRuntime(QWEN3_MODEL_DIMENSIONS[model_id])

    built = adapter_registry.default_adapter_registry().create_dense(
        artifact, runtime=runtime, device="CPU"
    )
    vector = (await built.embed_queries(("What monitoring is required?",)))[0]

    assert isinstance(built, Qwen3OpenVINODenseAdapter)
    assert len(vector) == 256
    assert sum(value * value for value in vector) == pytest.approx(1.0)
    assert runtime.calls[0][0][0].startswith("Instruct:")


def test_unicode_medical_tokenizer_is_normalized_and_multilingual() -> None:
    assert unicode_medical_tokens("Dose ５.０ mg/kg مَرِيض 高血圧") == (
        "dose",
        "5.0",
        "mg",
        "kg",
        "مَرِيض",
        "مريض",
        "高",
        "血",
        "圧",
        "高血",
        "血圧",
    )


def test_transformer_runtime_loads_only_local_trusted_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, tuple[str, dict[str, object]]] = {}
    fake_torch = ModuleType("torch")
    fake_torch.float32 = object()
    fake_torch.float16 = object()
    fake_torch.bfloat16 = object()
    fake_torch.cuda = SimpleNamespace(is_available=lambda: False)
    fake_torch.backends = SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False))

    class FakeTokenizer:
        @classmethod
        def from_pretrained(cls, root: str, **kwargs):
            calls["tokenizer"] = (root, kwargs)
            return cls()

    class FakeModel:
        config = SimpleNamespace(hidden_size=BGE_M3_DIMENSION)

        @classmethod
        def from_pretrained(cls, root: str, **kwargs):
            calls["model"] = (root, kwargs)
            return cls()

        def to(self, device: str) -> None:
            calls["device"] = (device, {})

        def eval(self) -> None:
            calls["eval"] = ("called", {})

    fake_transformers = ModuleType("transformers")
    fake_transformers.AutoTokenizer = FakeTokenizer
    fake_transformers.AutoModel = FakeModel
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)

    _TorchTransformerRuntime(
        tmp_path,
        device="cpu",
        dtype="float32",
        expected_dimension=BGE_M3_DIMENSION,
    )

    assert calls["tokenizer"][1] == {
        "local_files_only": True,
        "trust_remote_code": False,
        "use_fast": True,
    }
    assert calls["model"][1]["local_files_only"] is True
    assert calls["model"][1]["trust_remote_code"] is False
    assert calls["device"][0] == "cpu"


async def test_bm25_adapter_has_asymmetric_document_and_query_weights(
    tmp_path: Path,
) -> None:
    artifact = _verified_artifact(
        tmp_path,
        name="sparse",
        kind=ModelArtifactKind.SPARSE,
        model_id=BM25_MODEL_ID,
        dimension=2**18,
        adapter_id=BM25_ADAPTER_ID,
        adapter_revision=BM25_ADAPTER_REVISION,
        parameters=_sparse_parameters(),
    )
    adapter = QdrantBM25SparseAdapter(artifact)

    document = (await adapter.embed_documents(("the aspirin aspirin dose",)))[0]
    query = (await adapter.embed_queries(("aspirin aspirin",)))[0]

    assert query.indices[0] in document.indices
    assert query.values == (1.0,)
    aspirin_position = document.indices.index(query.indices[0])
    assert document.values[aspirin_position] > 1.0
    assert document.indices[0] < 2**18


class _FakeDenseAdapter:
    def __init__(self, artifact, *, device=None) -> None:
        self.artifact = artifact
        self.device = device

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return tuple((1.0,) + (0.0,) * (BGE_M3_DIMENSION - 1) for _ in texts)

    async def embed_queries(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return tuple((1.0,) + (0.0,) * (BGE_M3_DIMENSION - 1) for _ in texts)


def _production_cli_arguments(
    tmp_path: Path,
    dense_artifact,
    sparse_artifact,
) -> list[str]:
    dense_manifest = tmp_path / "dense-manifest.json"
    sparse_manifest = tmp_path / "sparse-manifest.json"
    dense_manifest.write_text(
        dense_artifact.manifest.model_dump_json(), encoding="utf-8"
    )
    sparse_manifest.write_text(
        sparse_artifact.manifest.model_dump_json(), encoding="utf-8"
    )
    return [
        "--embedding-backend",
        "production",
        "--dense-model-root",
        str(dense_artifact.root),
        "--dense-model-manifest",
        str(dense_manifest),
        "--dense-artifact-sha256",
        dense_artifact.manifest.artifact_sha256,
        "--sparse-model-root",
        str(sparse_artifact.root),
        "--sparse-model-manifest",
        str(sparse_manifest),
        "--sparse-artifact-sha256",
        sparse_artifact.manifest.artifact_sha256,
        "--device",
        "cpu",
    ]


async def test_index_produce_vectors_selects_verified_production_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dense_artifact = _verified_artifact(
        tmp_path,
        name="dense",
        kind=ModelArtifactKind.DENSE,
        model_id=BGE_M3_MODEL_ID,
        dimension=BGE_M3_DIMENSION,
        adapter_id=BGE_M3_ADAPTER_ID,
        adapter_revision=BGE_M3_ADAPTER_REVISION,
        parameters=_dense_parameters(),
    )
    sparse_artifact = _verified_artifact(
        tmp_path,
        name="sparse",
        kind=ModelArtifactKind.SPARSE,
        model_id=BM25_MODEL_ID,
        dimension=2**18,
        adapter_id=BM25_ADAPTER_ID,
        adapter_revision=BM25_ADAPTER_REVISION,
        parameters=_sparse_parameters(),
    )
    monkeypatch.setattr(adapter_registry, "BGEM3DenseAdapter", _FakeDenseAdapter)
    output = tmp_path / "vectors.json"
    arguments = cli.build_parser().parse_args(
        [
            "index-produce-vectors",
            str(FIXTURE_PATH),
            "--output",
            str(output),
            "--batch-size",
            "2",
            *_production_cli_arguments(tmp_path, dense_artifact, sparse_artifact),
        ]
    )

    assert await cli.produce_index_vectors(arguments) == 0
    produced = json.loads(output.read_text(encoding="utf-8"))
    assert produced["content"]["dense"]["model"] == dense_artifact.reference.model_dump(
        mode="json"
    )
    assert produced["content"]["sparse"]["model"] == sparse_artifact.reference.model_dump(
        mode="json"
    )


def test_benchmark_parser_and_factory_use_vector_pinned_production_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dense_artifact = _verified_artifact(
        tmp_path,
        name="dense",
        kind=ModelArtifactKind.DENSE,
        model_id=BGE_M3_MODEL_ID,
        dimension=BGE_M3_DIMENSION,
        adapter_id=BGE_M3_ADAPTER_ID,
        adapter_revision=BGE_M3_ADAPTER_REVISION,
        parameters=_dense_parameters(),
    )
    sparse_artifact = _verified_artifact(
        tmp_path,
        name="sparse",
        kind=ModelArtifactKind.SPARSE,
        model_id=BM25_MODEL_ID,
        dimension=2**18,
        adapter_id=BM25_ADAPTER_ID,
        adapter_revision=BM25_ADAPTER_REVISION,
        parameters=_sparse_parameters(),
    )
    monkeypatch.setattr(adapter_registry, "BGEM3DenseAdapter", _FakeDenseAdapter)
    arguments = cli.build_parser().parse_args(
        [
            "benchmark-run",
            str(FIXTURE_PATH),
            "--vectors",
            "vectors.json",
            "--suite",
            "suite.json",
            "--candidate",
            "candidate.json",
            "--output",
            "report.json",
            *_production_cli_arguments(tmp_path, dense_artifact, sparse_artifact),
        ]
    )
    expected_dense = cli.DenseVectorDefinition(
        dimension=BGE_M3_DIMENSION, model=dense_artifact.reference
    )
    expected_sparse = cli.SparseVectorDefinition(
        dimension=2**18, model=sparse_artifact.reference
    )

    backend = cli._build_embedding_backend(
        arguments,
        baseline_dense_dimension=1,
        baseline_sparse_dimension=1,
        maximum_batch_size=1,
        expected_dense=expected_dense,
        expected_sparse=expected_sparse,
    )

    assert isinstance(backend, ProductionEmbeddingBackend)
    assert backend.dense_definition.model == dense_artifact.reference
    assert backend.sparse_definition.model == sparse_artifact.reference
