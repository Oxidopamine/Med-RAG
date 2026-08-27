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
    BGE_RERANKER_V2_M3_ADAPTER_ID,
    BGE_RERANKER_V2_M3_ADAPTER_REVISION,
    MEDCPT_CROSS_ENCODER_ADAPTER_ID,
    MEDCPT_CROSS_ENCODER_ADAPTER_REVISION,
    QWEN3_RERANKER_ADAPTER_ID,
    QWEN3_RERANKER_ADAPTER_REVISION,
    BGERerankerV2M3Adapter,
    MedCPTCrossEncoderAdapter,
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


async def test_clearing_the_memory_cache_spares_the_on_disk_cache(tmp_path) -> None:
    """Clearing drops in-memory scores only; a configured disk cache survives it.

    A timing harness must clear between measured runs or run 2 is served from run 1. The
    on-disk cache is keyed to artifact, instruction, and budget, so it is a
    correctness-preserving optimization rather than a measurement artifact, and clearing
    memory must not invalidate it. Both halves of that claim are pinned here.
    """

    artifact, _ = _artifact(tmp_path)
    cache = tmp_path / "scores.json"
    runtime = _Runtime((), {"first": 0.3, "second": 0.6})
    adapter = Qwen3RerankerAdapter(artifact, runtime=runtime, score_cache=cache)

    expected = await adapter.score("query", ("first", "second"))
    assert len(runtime.calls) == 1

    adapter.clear_memory_score_cache()

    assert await adapter.score("query", ("first", "second")) == expected
    assert len(runtime.calls) == 2, "cleared scores must be measured again, not replayed"

    replayed_runtime = _Runtime((0.9, 0.9))
    replayed = Qwen3RerankerAdapter(
        artifact,
        runtime=replayed_runtime,
        score_cache=cache,
    )

    assert await replayed.score("query", ("first", "second")) == expected
    assert replayed_runtime.calls == [], "the on-disk cache must survive a memory clear"


def _specialist_artifact(tmp_path, *, name, model_id, adapter_id, adapter_revision, parameters):
    root = tmp_path / name
    root.mkdir()
    (root / "config.json").write_text("{}\n", encoding="utf-8")
    manifest = build_model_artifact_manifest(
        root,
        artifact_kind=ModelArtifactKind.RERANKER,
        model_id=model_id,
        revision="a" * 40,
        dimension=1,
        adapter_id=adapter_id,
        adapter_revision=adapter_revision,
        adapter_parameters=parameters,
    )
    return verify_model_artifact(
        root,
        manifest,
        expected_artifact_sha256=manifest.artifact_sha256,
        expected_kind=ModelArtifactKind.RERANKER,
    )


def _bge_artifact(tmp_path, *, max_length=512):
    return _specialist_artifact(
        tmp_path,
        name="bge-reranker",
        model_id="BAAI/bge-reranker-v2-m3",
        adapter_id=BGE_RERANKER_V2_M3_ADAPTER_ID,
        adapter_revision=BGE_RERANKER_V2_M3_ADAPTER_REVISION,
        parameters={
            "max_length": max_length,
            "batch_size": 2,
            "padding_side": "right",
            "truncation": "longest_first",
            "score_mode": "sigmoid_probability",
            "device": "cpu",
            "dtype": "float32",
        },
    )


def _medcpt_artifact(tmp_path):
    return _specialist_artifact(
        tmp_path,
        name="medcpt-cross-encoder",
        model_id="ncbi/MedCPT-Cross-Encoder",
        adapter_id=MEDCPT_CROSS_ENCODER_ADAPTER_ID,
        adapter_revision=MEDCPT_CROSS_ENCODER_ADAPTER_REVISION,
        parameters={
            "max_length": 512,
            "batch_size": 2,
            "padding_side": "right",
            "truncation": "longest_first",
            "score_mode": "relevance_logit",
            "device": "cpu",
            "dtype": "float32",
        },
    )


async def test_bge_reranker_is_manifest_bound_and_applies_no_instruction(tmp_path) -> None:
    artifact = _bge_artifact(tmp_path)
    runtime = _Runtime((0.2, 0.9), {"one": 0.2, "two": 0.9, "six": 0.2})
    adapter = BGERerankerV2M3Adapter(artifact, runtime=runtime)

    scores = await adapter.score("Which treatment?", ("one", "two", "six"))

    assert scores == (0.2, 0.9, 0.2)
    assert [len(call[0]) for call in runtime.calls] == [2, 1]
    # BGE's published contract carries no instruction segment; the empty-string digest is
    # what pins "this backend applies no instruction" into a candidate identity.
    assert all(call[1] == "" for call in runtime.calls)
    assert adapter.instruction_sha256 == hashlib.sha256(b"").hexdigest()


async def test_bge_reranker_rejects_scores_outside_its_sigmoid_domain(tmp_path) -> None:
    artifact = _bge_artifact(tmp_path)
    adapter = BGERerankerV2M3Adapter(artifact, runtime=_Runtime((1.4,)))

    with pytest.raises(RuntimeError, match="invalid probability"):
        await adapter.score("query", ("document",))


async def test_medcpt_accepts_the_negative_logits_a_probability_lane_would_reject(
    tmp_path,
) -> None:
    """MedCPT's own example output is negative for low-relevance pairs.

    A shared 0-1 guard would reject every genuine non-match, so the unbounded domain is
    the behaviour under test, not an oversight.
    """

    artifact = _medcpt_artifact(tmp_path)
    runtime = _Runtime((), {"match": 6.9363, "unrelated": -15.8475})
    adapter = MedCPTCrossEncoderAdapter(artifact, runtime=runtime)

    scores = await adapter.score("query", ("match", "unrelated"))

    assert scores == (6.9363, -15.8475)


async def test_medcpt_still_rejects_non_finite_scores(tmp_path) -> None:
    artifact = _medcpt_artifact(tmp_path)
    adapter = MedCPTCrossEncoderAdapter(artifact, runtime=_Runtime((float("nan"),)))

    with pytest.raises(RuntimeError, match="non-finite relevance score"):
        await adapter.score("query", ("document",))


async def test_reranker_lanes_do_not_share_a_score_cache(tmp_path) -> None:
    """A BGE probability must never be served back as a MedCPT logit, or vice versa."""

    cache = tmp_path / "scores.json"
    bge = BGERerankerV2M3Adapter(
        _bge_artifact(tmp_path),
        runtime=_Runtime((0.5,)),
        score_cache=cache,
    )
    await bge.score("query", ("document",))

    with pytest.raises(AdapterConfigurationError, match="score cache is inconsistent"):
        MedCPTCrossEncoderAdapter(
            _medcpt_artifact(tmp_path),
            runtime=_Runtime((1.0,)),
            score_cache=cache,
        )


def test_bge_reranker_rejects_a_budget_beyond_its_position_ceiling(tmp_path) -> None:
    # The manifest builder stores adapter parameters verbatim, so the 8192 ceiling is
    # enforced where it is sealed against the adapter, not when the bytes are inventoried.
    artifact = _bge_artifact(tmp_path, max_length=9000)

    with pytest.raises(AdapterConfigurationError, match="invalid parameters"):
        BGERerankerV2M3Adapter(artifact, runtime=_Runtime((0.1,)))


def test_specialist_adapters_reject_each_others_artifacts(tmp_path) -> None:
    with pytest.raises(AdapterConfigurationError, match="allowlisted"):
        MedCPTCrossEncoderAdapter(_bge_artifact(tmp_path), runtime=_Runtime((0.1,)))
    with pytest.raises(AdapterConfigurationError, match="allowlisted"):
        BGERerankerV2M3Adapter(_medcpt_artifact(tmp_path), runtime=_Runtime((0.1,)))
