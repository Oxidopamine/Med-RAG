import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.corpus_steward.cli import model_artifact_schema_document
from app.corpus_steward.index_schemas import SparseVector
from app.corpus_steward.model_artifacts import (
    ModelArtifactError,
    ModelArtifactKind,
    ModelArtifactManifest,
    ModelArtifactPairManifest,
    ModelArtifactPairManifestContent,
    ModelArtifactRole,
    ModelArtifactVerificationLimits,
    build_model_artifact_manifest,
    build_model_artifact_pair_manifest,
    verify_model_artifact,
    verify_model_artifact_pair,
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


def _pair_member(
    tmp_path: Path,
    name: str,
    *,
    model_id: str,
    revision: str,
    adapter_id: str = "med-rag/dual-adapter",
    adapter_revision: str = "1.0.0",
    dimension: int = 768,
    adapter_parameters: dict | None = None,
):
    root = tmp_path / name
    root.mkdir(parents=True)
    (root / "config.json").write_text(
        json.dumps({"architecture": name}, sort_keys=True), encoding="utf-8"
    )
    manifest = build_model_artifact_manifest(
        root,
        artifact_kind=ModelArtifactKind.DENSE,
        model_id=model_id,
        revision=revision,
        dimension=dimension,
        adapter_id=adapter_id,
        adapter_revision=adapter_revision,
        adapter_parameters=adapter_parameters,
    )
    return root, manifest


def _pair(tmp_path: Path, **overrides):
    query_root, query = _pair_member(
        tmp_path,
        "query-encoder",
        model_id="example/query-encoder",
        revision="1111111111111111111111111111111111111111",
        **overrides.pop("query", {}),
    )
    document_root, document = _pair_member(
        tmp_path,
        "article-encoder",
        model_id="example/article-encoder",
        revision="2222222222222222222222222222222222222222",
        **overrides.pop("document", {}),
    )
    manifest = build_model_artifact_pair_manifest(
        query=query,
        document=document,
        model_id=overrides.pop("model_id", "example/dual-encoder"),
        adapter_parameters=overrides.pop("adapter_parameters", {"pooling": "cls"}),
    )
    assert not overrides
    return query_root, document_root, manifest


def test_artifact_pair_binds_two_roots_as_one_verified_candidate_identity(
    tmp_path: Path,
) -> None:
    query_root, document_root, manifest = _pair(tmp_path)

    verified = verify_model_artifact_pair(
        query_root=query_root,
        document_root=document_root,
        manifest=manifest,
        expected_artifact_sha256=manifest.artifact_sha256,
        expected_kind=ModelArtifactKind.DENSE,
    )

    assert verified.query.root == query_root.resolve()
    assert verified.document.root == document_root.resolve()
    assert verified.member(ModelArtifactRole.QUERY) is verified.query
    assert verified.member(ModelArtifactRole.DOCUMENT) is verified.document
    # One identity for the pair, whose digest transitively covers both roots' bytes.
    assert verified.reference == manifest.model_reference()
    assert verified.reference.artifact_sha256 == manifest.artifact_sha256
    assert verified.reference.revision == (
        "document=2222222222222222222222222222222222222222;"
        "query=1111111111111111111111111111111111111111"
    )
    assert manifest.content.dimension == 768
    assert manifest.content.artifact_kind is ModelArtifactKind.DENSE


def test_artifact_pair_digest_covers_every_byte_of_both_halves(tmp_path: Path) -> None:
    query_root, document_root, manifest = _pair(tmp_path)
    (document_root / "config.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ModelArtifactError, match="size|digest"):
        verify_model_artifact_pair(
            query_root=query_root,
            document_root=document_root,
            manifest=manifest,
            expected_artifact_sha256=manifest.artifact_sha256,
        )


def test_artifact_pair_rejects_swapped_roles_and_a_shared_root(tmp_path: Path) -> None:
    query_root, document_root, manifest = _pair(tmp_path)

    with pytest.raises(ModelArtifactError, match="size|digest|inventory mismatch"):
        verify_model_artifact_pair(
            query_root=document_root,
            document_root=query_root,
            manifest=manifest,
            expected_artifact_sha256=manifest.artifact_sha256,
        )

    with pytest.raises(
        ModelArtifactError, match="size|digest|inventory mismatch|same artifact root"
    ):
        verify_model_artifact_pair(
            query_root=query_root,
            document_root=query_root,
            manifest=manifest,
            expected_artifact_sha256=manifest.artifact_sha256,
        )


def test_artifact_pair_requires_an_independent_digest_pin(tmp_path: Path) -> None:
    query_root, document_root, manifest = _pair(tmp_path)

    with pytest.raises(ModelArtifactError, match="independently pinned"):
        verify_model_artifact_pair(
            query_root=query_root,
            document_root=document_root,
            manifest=manifest,
            expected_artifact_sha256="0" * 64,
        )


def test_artifact_pair_members_cannot_disagree_or_carry_their_own_parameters(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError, match="paired dimension"):
        _pair(tmp_path / "dimension", query={"dimension": 1024})

    with pytest.raises(ValidationError, match="paired adapter"):
        _pair(tmp_path / "adapter", query={"adapter_revision": "2.0.0"})

    with pytest.raises(ValidationError, match="cannot carry parameters"):
        _pair(tmp_path / "parameters", query={"adapter_parameters": {"pooling": "cls"}})


def test_artifact_pair_revision_and_membership_cannot_be_forged(tmp_path: Path) -> None:
    _, _, manifest = _pair(tmp_path)
    content = manifest.content.model_dump(mode="json")
    document, query = (member for member in content["members"])

    with pytest.raises(ValidationError, match="derived composite"):
        ModelArtifactPairManifestContent.model_validate({**content, "revision": "v1"})

    with pytest.raises(ValidationError, match="same artifact twice"):
        ModelArtifactPairManifestContent.model_validate(
            {**content, "members": [document, {**document, "role": "QUERY"}]}
        )

    with pytest.raises(ValidationError, match="one document and one query"):
        ModelArtifactPairManifestContent.model_validate(
            {**content, "members": [query, document]}
        )


def test_artifact_pair_manifest_digest_must_match_its_content(tmp_path: Path) -> None:
    _, _, manifest = _pair(tmp_path)

    with pytest.raises(ValidationError, match="digest does not match"):
        ModelArtifactPairManifest(content=manifest.content, artifact_sha256="0" * 64)

    resealed = ModelArtifactPairManifest.seal(
        manifest.content.model_copy(update={"model_id": "example/other"})
    )
    assert resealed.artifact_sha256 != manifest.artifact_sha256


def test_artifact_pair_enforces_one_resource_ceiling_across_both_roots(
    tmp_path: Path,
) -> None:
    """A pair must not silently consume twice the single-artifact budget.

    _enforce_limits runs inside each per-member verification, so a limit checked only
    there passes for each half while the pair as a whole exceeds it.
    """

    query_root, document_root, manifest = _pair(tmp_path)
    per_member_files = max(
        len(member.manifest.content.files) for member in manifest.content.members
    )
    # Generous enough for either half alone, too small for both together.
    limits = ModelArtifactVerificationLimits(max_file_count=per_member_files)

    with pytest.raises(ModelArtifactError, match="file-count limit"):
        verify_model_artifact_pair(
            query_root=query_root,
            document_root=document_root,
            manifest=manifest,
            expected_artifact_sha256=manifest.artifact_sha256,
            limits=limits,
        )

    # Each half on its own still verifies under that same ceiling, which is the point.
    for member, root in zip(
        manifest.content.members, (document_root, query_root), strict=True
    ):
        verify_model_artifact(
            root,
            member.manifest,
            expected_artifact_sha256=member.manifest.artifact_sha256,
            limits=limits,
        )


def test_artifact_pair_rejects_a_member_revision_that_forges_the_composite(
    tmp_path: Path,
) -> None:
    """Separators are structural, so a revision carrying one is ambiguous."""

    for index, forged in enumerate(("1111;role=2222", "1111=2222")):
        _, query = _pair_member(
            tmp_path / f"forged-{index}",
            "query-encoder",
            model_id="example/query-encoder",
            revision=forged,
        )
        _, document = _pair_member(
            tmp_path / f"forged-{index}",
            "article-encoder",
            model_id="example/article-encoder",
            revision="2" * 40,
        )
        with pytest.raises(ValueError, match="composite separators"):
            build_model_artifact_pair_manifest(
                query=query, document=document, model_id="example/dual-encoder"
            )


def test_artifact_pair_requires_two_different_models(tmp_path: Path) -> None:
    """A dual encoder whose halves are the same model is a symmetric model."""

    _, query = _pair_member(
        tmp_path, "query-encoder", model_id="example/same-encoder", revision="1" * 40
    )
    _, document = _pair_member(
        tmp_path, "article-encoder", model_id="example/same-encoder", revision="2" * 40
    )

    with pytest.raises(ValueError, match="two different models"):
        build_model_artifact_pair_manifest(
            query=query, document=document, model_id="example/dual-encoder"
        )
