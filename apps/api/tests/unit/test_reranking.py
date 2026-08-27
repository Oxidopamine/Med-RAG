import hashlib
import json
from collections.abc import Sequence

import pytest

from app.corpus_steward.embedding_adapters import AdapterConfigurationError
from app.corpus_steward.model_artifacts import (
    ModelArtifactKind,
    build_model_artifact_manifest,
    verify_model_artifact,
)
from app.corpus_steward.reranking import (
    QWEN3_RERANKER_ADAPTER_ID,
    QWEN3_RERANKER_ADAPTER_REVISION,
    Qwen3RerankerAdapter,
)


class _Runtime:
    def __init__(
        self,
        scores: tuple[float, ...],
        document_scores: dict[str, float] | None = None,
    ) -> None:
        self.scores = scores
        self.document_scores = document_scores
        self.calls: list[tuple[tuple[tuple[str, str], ...], str, int]] = []

    def score(
        self,
        pairs: Sequence[tuple[str, str]],
        *,
        instruction: str,
        max_length: int,
    ) -> Sequence[float]:
        converted = tuple(pairs)
        self.calls.append((converted, instruction, max_length))
        if self.document_scores is not None:
            return tuple(self.document_scores[pair[1]] for pair in converted)
        return self.scores[: len(converted)]


def _artifact(tmp_path):
    root = tmp_path / "qwen3-reranker"
    root.mkdir()
    (root / "config.json").write_text("{}\n", encoding="utf-8")
    parameters = {
        "instruction": "Retrieve clinical guideline passages that answer the query.",
        "max_length": 2048,
        "batch_size": 2,
        "padding_side": "left",
        "truncation": "longest_first",
        "score_mode": "yes_probability",
        "device": "cpu",
        "dtype": "float32",
    }
    manifest = build_model_artifact_manifest(
        root,
        artifact_kind=ModelArtifactKind.RERANKER,
        model_id="Qwen/Qwen3-Reranker-0.6B",
        revision="e" * 40,
        dimension=1,
        adapter_id=QWEN3_RERANKER_ADAPTER_ID,
        adapter_revision=QWEN3_RERANKER_ADAPTER_REVISION,
        adapter_parameters=parameters,
    )
    return verify_model_artifact(
        root,
        manifest,
        expected_artifact_sha256=manifest.artifact_sha256,
        expected_kind=ModelArtifactKind.RERANKER,
    ), parameters


async def test_qwen3_reranker_is_manifest_bound_and_batched(tmp_path) -> None:
    artifact, parameters = _artifact(tmp_path)
    runtime = _Runtime((0.1, 0.8), {"one": 0.1, "two": 0.8, "six": 0.1})
    adapter = Qwen3RerankerAdapter(artifact, runtime=runtime)

    scores = await adapter.score("Which treatment?", ("one", "two", "six"))

    assert scores == (0.1, 0.8, 0.1)
    assert [len(call[0]) for call in runtime.calls] == [2, 1]
    assert all(call[1] == parameters["instruction"] for call in runtime.calls)
    assert adapter.instruction_sha256 == hashlib.sha256(
        parameters["instruction"].encode("utf-8")
    ).hexdigest()
    assert adapter.model_reference == artifact.reference


async def test_qwen3_reranker_rejects_malformed_probabilities(tmp_path) -> None:
    artifact, _ = _artifact(tmp_path)
    adapter = Qwen3RerankerAdapter(artifact, runtime=_Runtime((1.5,)))

    with pytest.raises(RuntimeError, match="invalid probability"):
        await adapter.score("query", ("document",))


async def test_qwen3_reranker_reuses_a_digest_checked_score_cache(tmp_path) -> None:
    artifact, _ = _artifact(tmp_path)
    cache = tmp_path / "scores.json"
    first_runtime = _Runtime((0.2, 0.7))
    first = Qwen3RerankerAdapter(
        artifact,
        runtime=first_runtime,
        score_cache=cache,
    )

    expected = await first.score("query", ("first", "second"))
    second_runtime = _Runtime((0.9, 0.9))
    second = Qwen3RerankerAdapter(
        artifact,
        runtime=second_runtime,
        score_cache=cache,
    )

    assert await second.score("query", ("first", "second")) == expected
    assert second_runtime.calls == []

    tampered = json.loads(cache.read_text(encoding="utf-8"))
    tampered["scores"][next(iter(tampered["scores"]))] = 0.99
    cache.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(AdapterConfigurationError, match="score cache is inconsistent"):
        Qwen3RerankerAdapter(artifact, runtime=_Runtime((0.1,)), score_cache=cache)
