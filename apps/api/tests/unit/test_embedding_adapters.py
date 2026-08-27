import json
import sys
from collections.abc import Sequence
from contextlib import nullcontext
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
    MEDCPT_ADAPTER_ID,
    MEDCPT_ADAPTER_REVISION,
    MEDCPT_ARTICLE_MODEL_ID,
    MEDCPT_DIMENSION,
    MEDCPT_PAIR_MODEL_ID,
    MEDCPT_PASSAGE_FORMAT,
    MEDCPT_QUERY_MODEL_ID,
    QWEN3_ADAPTER_ID,
    QWEN3_ADAPTER_REVISION,
    QWEN3_MODEL_DIMENSIONS,
    QWEN3_OPENVINO_ADAPTER_ID,
    QWEN3_OPENVINO_ADAPTER_REVISION,
    AdapterConfigurationError,
    BGEM3DenseAdapter,
    MedCPTDualEncoderAdapter,
    QdrantBM25SparseAdapter,
    Qwen3DenseAdapter,
    Qwen3OpenVINODenseAdapter,
    _TorchTransformerRuntime,
    unicode_medical_tokens,
)
from app.corpus_steward.model_artifacts import (
    ModelArtifactKind,
    build_model_artifact_manifest,
    build_model_artifact_pair_manifest,
    verify_model_artifact,
    verify_model_artifact_pair,
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


def _medcpt_parameters(*, batch_size: int = 2, device: str = "cpu") -> dict[str, object]:
    return {
        "pooling": "cls",
        "normalize": False,
        "query_max_length": 64,
        "document_max_length": 512,
        "passage_format": MEDCPT_PASSAGE_FORMAT,
        "batch_size": batch_size,
        "device": device,
        "dtype": "float32",
    }


def _medcpt_member(
    tmp_path: Path,
    name: str,
    *,
    model_id: str,
    revision: str,
    adapter_id: str = MEDCPT_ADAPTER_ID,
    dimension: int = MEDCPT_DIMENSION,
):
    root = tmp_path / name
    root.mkdir(parents=True)
    # Distinct bytes per encoder, as two real checkpoints always have.
    (root / "config.json").write_text(
        json.dumps({"_name_or_path": model_id}, sort_keys=True), encoding="utf-8"
    )
    manifest = build_model_artifact_manifest(
        root,
        artifact_kind=ModelArtifactKind.DENSE,
        model_id=model_id,
        revision=revision,
        dimension=dimension,
        adapter_id=adapter_id,
        adapter_revision=MEDCPT_ADAPTER_REVISION,
    )
    return root, manifest


def _medcpt_pair(
    tmp_path: Path,
    *,
    parameters: dict[str, object] | None = None,
    model_id: str = MEDCPT_PAIR_MODEL_ID,
    query_model_id: str = MEDCPT_QUERY_MODEL_ID,
    document_model_id: str = MEDCPT_ARTICLE_MODEL_ID,
    query_revision: str = "cd6b4b1f0a7f2fa26e1a3e2ae4e5cbd3ba4a04b1",
    adapter_id: str = MEDCPT_ADAPTER_ID,
    dimension: int = MEDCPT_DIMENSION,
):
    query_root, query = _medcpt_member(
        tmp_path,
        "medcpt-query",
        model_id=query_model_id,
        revision=query_revision,
        adapter_id=adapter_id,
        dimension=dimension,
    )
    document_root, document = _medcpt_member(
        tmp_path,
        "medcpt-article",
        model_id=document_model_id,
        revision="8f0a2b1c9d3e4f5a6b7c8d9e0f1a2b3c4d5e6f70",
        adapter_id=adapter_id,
        dimension=dimension,
    )
    manifest = build_model_artifact_pair_manifest(
        query=query,
        document=document,
        model_id=model_id,
        adapter_parameters=parameters if parameters is not None else _medcpt_parameters(),
    )
    return verify_model_artifact_pair(
        query_root=query_root,
        document_root=document_root,
        manifest=manifest,
        expected_artifact_sha256=manifest.artifact_sha256,
        expected_kind=ModelArtifactKind.DENSE,
    )


class _DualEncoderRuntime:
    """Records exactly what each of the two encoders was asked to encode."""

    def __init__(self, marker: float) -> None:
        self.marker = marker
        self.calls: list[dict[str, object]] = []

    def encode(
        self,
        texts: Sequence[str],
        *,
        max_length: int,
        pooling: str,
        normalize: bool,
        text_pairs: Sequence[str] | None = None,
    ) -> Sequence[Sequence[float]]:
        self.calls.append(
            {
                "texts": tuple(texts),
                "text_pairs": tuple(text_pairs) if text_pairs is not None else None,
                "max_length": max_length,
                "pooling": pooling,
                "normalize": normalize,
            }
        )
        return tuple(
            (self.marker, float(len(text))) + (0.0,) * (MEDCPT_DIMENSION - 2)
            for text in texts
        )


async def test_medcpt_routes_each_role_to_its_own_encoder_with_official_truncation(
    tmp_path: Path,
) -> None:
    pair = _medcpt_pair(tmp_path)
    query_runtime = _DualEncoderRuntime(1.0)
    document_runtime = _DualEncoderRuntime(2.0)
    adapter = MedCPTDualEncoderAdapter(
        pair, query_runtime=query_runtime, document_runtime=document_runtime
    )

    queries = await adapter.embed_queries(("first-line ART regimen?",))
    passages = await adapter.embed_passages(
        (("Initial therapy", "Start an integrase inhibitor."),)
    )

    assert len(queries[0]) == MEDCPT_DIMENSION
    assert len(passages[0]) == MEDCPT_DIMENSION
    assert queries[0][0] == 1.0
    assert passages[0][0] == 2.0
    # The query encoder never sees a sequence pair and truncates at the query length.
    assert query_runtime.calls == [
        {
            "texts": ("first-line ART regimen?",),
            "text_pairs": None,
            "max_length": 64,
            "pooling": "cls",
            "normalize": False,
        }
    ]
    # The article encoder receives the title/section as the first sequence.
    assert document_runtime.calls == [
        {
            "texts": ("Initial therapy",),
            "text_pairs": ("Start an integrase inhibitor.",),
            "max_length": 512,
            "pooling": "cls",
            "normalize": False,
        }
    ]


async def test_medcpt_passage_encoding_does_not_depend_on_batch_composition(
    tmp_path: Path,
) -> None:
    pair = _medcpt_pair(tmp_path, parameters=_medcpt_parameters(batch_size=4))
    document_runtime = _DualEncoderRuntime(2.0)
    adapter = MedCPTDualEncoderAdapter(
        pair, query_runtime=_DualEncoderRuntime(1.0), document_runtime=document_runtime
    )

    vectors = await adapter.embed_passages(
        (
            ("Monitoring", "Check viral load at week 4."),
            ("", "A chunk with no section heading."),
            ("Dosing", "Give 50 mg once daily."),
        )
    )

    assert len(vectors) == 3
    # Titled and untitled rows are encoded in separate calls, so a chunk's token
    # sequence never changes with the company it keeps in a micro-batch.
    assert document_runtime.calls[0]["texts"] == ("Monitoring", "Dosing")
    assert document_runtime.calls[0]["text_pairs"] == (
        "Check viral load at week 4.",
        "Give 50 mg once daily.",
    )
    assert document_runtime.calls[1]["texts"] == ("A chunk with no section heading.",)
    assert document_runtime.calls[1]["text_pairs"] is None
    # Output order still follows the caller's order, not the encoding order.
    assert vectors[0][1] == float(len("Monitoring"))
    assert vectors[1][1] == float(len("A chunk with no section heading."))
    assert vectors[2][1] == float(len("Dosing"))


async def test_medcpt_release_documents_are_encoded_chunk_only(tmp_path: Path) -> None:
    """Production passages carry no title, and the adapter must not invent one.

    The sealed release record has no title or section field, so the producer hands over
    chunk text alone. An earlier version split on a ``"\\n\\n"`` separator that
    ``content_search`` can never contain — all whitespace runs are collapsed to single
    spaces before the record is sealed — so titles appeared supported while every passage
    silently encoded untitled. This pins the honest behaviour: one untitled call, no
    sequence pair, whatever the text looks like.
    """

    pair = _medcpt_pair(tmp_path, parameters=_medcpt_parameters(batch_size=4))
    document_runtime = _DualEncoderRuntime(2.0)
    adapter = MedCPTDualEncoderAdapter(
        pair, query_runtime=_DualEncoderRuntime(1.0), document_runtime=document_runtime
    )

    vectors = await adapter.embed_documents(
        ("Monitoring Check viral load at week 4.", "Give 50 mg once daily.")
    )

    assert len(vectors) == 2
    assert len(document_runtime.calls) == 1, "untitled passages take a single branch"
    assert document_runtime.calls[0]["texts"] == (
        "Monitoring Check viral load at week 4.",
        "Give 50 mg once daily.",
    )
    assert document_runtime.calls[0]["text_pairs"] is None


async def test_medcpt_pair_is_dispatched_only_through_the_paired_allowlist(
    tmp_path: Path,
) -> None:
    pair = _medcpt_pair(tmp_path)
    registry = adapter_registry.default_adapter_registry()

    built = registry.create_dense_pair(
        pair,
        query_runtime=_DualEncoderRuntime(1.0),
        document_runtime=_DualEncoderRuntime(2.0),
    )

    assert isinstance(built, MedCPTDualEncoderAdapter)
    # Half a dual encoder is not a symmetric dense model, and cannot be built as one.
    with pytest.raises(AdapterConfigurationError, match="not in the local allowlist"):
        registry.create_dense(pair.query)
    with pytest.raises(AdapterConfigurationError, match="not in the local allowlist"):
        registry.create_dense(pair.document)
    # And a single-artifact adapter is not reachable through the paired table.
    unpaired = _medcpt_pair(tmp_path / "unpaired", adapter_id=BGE_M3_ADAPTER_ID)
    with pytest.raises(AdapterConfigurationError, match="not in the local allowlist"):
        registry.create_dense_pair(unpaired)


def test_medcpt_rejects_mismatched_members_dimension_and_device(tmp_path: Path) -> None:
    runtimes = {
        "query_runtime": _DualEncoderRuntime(1.0),
        "document_runtime": _DualEncoderRuntime(2.0),
    }

    with pytest.raises(AdapterConfigurationError, match="does not match"):
        MedCPTDualEncoderAdapter(_medcpt_pair(tmp_path / "device"), device="cuda", **runtimes)

    swapped = _medcpt_pair(
        tmp_path / "swapped",
        query_model_id=MEDCPT_ARTICLE_MODEL_ID,
        document_model_id=MEDCPT_QUERY_MODEL_ID,
    )
    with pytest.raises(AdapterConfigurationError, match="query member must be"):
        MedCPTDualEncoderAdapter(swapped, **runtimes)

    with pytest.raises(AdapterConfigurationError, match="paired model_id"):
        MedCPTDualEncoderAdapter(
            _medcpt_pair(tmp_path / "model", model_id="ncbi/MedCPT-Cross-Encoder"),
            **runtimes,
        )

    with pytest.raises(AdapterConfigurationError, match="immutable lowercase commit"):
        MedCPTDualEncoderAdapter(
            _medcpt_pair(tmp_path / "revision", query_revision="main"), **runtimes
        )

    with pytest.raises(AdapterConfigurationError, match="must declare dimension 768"):
        MedCPTDualEncoderAdapter(
            _medcpt_pair(tmp_path / "dimension", dimension=1024), **runtimes
        )


def test_medcpt_parameters_are_sealed_within_the_official_encoder_limits(
    tmp_path: Path,
) -> None:
    runtimes = {
        "query_runtime": _DualEncoderRuntime(1.0),
        "document_runtime": _DualEncoderRuntime(2.0),
    }
    rejected = (
        {"document_max_length": 1024},
        {"query_max_length": 0},
        {"pooling": "mean"},
        {"passage_format": "chunk-only-v1"},
        {"dtype": "int8"},
    )
    for index, override in enumerate(rejected):
        with pytest.raises(AdapterConfigurationError, match="invalid parameters"):
            MedCPTDualEncoderAdapter(
                _medcpt_pair(
                    tmp_path / f"rejected-{index}",
                    parameters=_medcpt_parameters() | override,
                ),
                **runtimes,
            )


async def test_medcpt_pair_pins_one_identity_in_the_production_backend(
    tmp_path: Path,
) -> None:
    pair = _medcpt_pair(tmp_path)
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
    adapter = MedCPTDualEncoderAdapter(
        pair,
        query_runtime=_DualEncoderRuntime(1.0),
        document_runtime=_DualEncoderRuntime(2.0),
    )

    backend = ProductionEmbeddingBackend(
        adapter, QdrantBM25SparseAdapter(sparse_artifact)
    )
    embedded = await backend.embed_documents(("Give 50 mg once daily.",))

    # The collection is pinned to the pair, not to either checkpoint on its own.
    assert backend.dense_definition.dimension == MEDCPT_DIMENSION
    assert backend.dense_definition.model == pair.reference
    assert backend.dense_definition.model != pair.query.reference
    assert backend.dense_definition.model != pair.document.reference
    assert len(embedded[0].dense) == MEDCPT_DIMENSION


def test_transformer_runtime_passes_sequence_pairs_to_the_local_tokenizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}
    fake_torch = ModuleType("torch")
    fake_torch.float32 = object()
    fake_torch.float16 = object()
    fake_torch.bfloat16 = object()
    fake_torch.cuda = SimpleNamespace(is_available=lambda: False)
    fake_torch.backends = SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False))

    class FakeTokenizer:
        @classmethod
        def from_pretrained(cls, root: str, **kwargs):
            return cls()

        def __call__(self, *args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs
            raise _StopEncoding

    class FakeModel:
        config = SimpleNamespace(hidden_size=MEDCPT_DIMENSION)

        @classmethod
        def from_pretrained(cls, root: str, **kwargs):
            seen["model"] = kwargs
            return cls()

        def to(self, device: str) -> None:
            return None

        def eval(self) -> None:
            return None

    class _StopEncoding(Exception):
        pass

    fake_transformers = ModuleType("transformers")
    fake_transformers.AutoTokenizer = FakeTokenizer
    fake_transformers.AutoModel = FakeModel
    fake_torch.inference_mode = lambda: nullcontext()
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)

    runtime = _TorchTransformerRuntime(
        tmp_path, device="cpu", dtype="float32", expected_dimension=MEDCPT_DIMENSION
    )
    with pytest.raises(_StopEncoding):
        runtime.encode(
            ("Monitoring",),
            max_length=512,
            pooling="cls",
            normalize=False,
            text_pairs=("Check viral load.",),
        )

    # The title/section and the chunk arrive as two aligned sequence lists.
    assert seen["args"] == (["Monitoring"], ["Check viral load."])
    assert seen["kwargs"]["truncation"] is True
    assert seen["kwargs"]["max_length"] == 512
    assert seen["model"]["local_files_only"] is True
    assert seen["model"]["trust_remote_code"] is False

    with pytest.raises(ValueError, match="must align"):
        runtime.encode(
            ("Monitoring", "Dosing"),
            max_length=512,
            pooling="cls",
            normalize=False,
            text_pairs=("Check viral load.",),
        )
