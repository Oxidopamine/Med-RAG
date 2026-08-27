import json
import sys
import types
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import pytest

from app.corpus_steward.benchmark_schemas import (
    BENCHMARK_CONTRACT_VERSION,
    BenchmarkSuite,
    BenchmarkSuitePartition,
)
from app.corpus_steward.cli import reranker_runtime_matrix_schema_documents
from app.corpus_steward.model_artifacts import (
    ModelArtifactKind,
    build_model_artifact_manifest,
    verify_model_artifact,
)
from app.corpus_steward.reranker_runtime_matrix import (
    _BGE_RERANKER_IDENTITY,
    _MEDCPT_RERANKER_IDENTITY,
    _QWEN3_RERANKER_IDENTITY,
    RERANKER_RUNTIME_MODEL_IDS,
    RerankerRuntimeMatrixRequest,
    RerankerRuntimeMeasurements,
    RerankerRuntimeTarget,
    _sealed_instruction,
    _truncated_pair_count,
    execute_reranker_runtime_matrix,
)
from app.corpus_steward.reranking import (
    _OFFICIAL_PREFIX,
    _OFFICIAL_SUFFIX,
    BGE_RERANKER_V2_M3_MODEL_IDS,
    MEDCPT_CROSS_ENCODER_ADAPTER_ID,
    MEDCPT_CROSS_ENCODER_ADAPTER_REVISION,
    MEDCPT_CROSS_ENCODER_MODEL_IDS,
    QWEN3_RERANKER_MODEL_IDS,
    BGERerankerV2M3Parameters,
    MedCPTCrossEncoderAdapter,
    MedCPTCrossEncoderParameters,
    Qwen3RerankerParameters,
)
from app.schemas.corpus import CorpusReleaseBundle

ROOT = Path(__file__).parents[4]
FIXTURE_PATH = ROOT / "data" / "fixtures" / "corpus-release-v1.json"
SUITE_PATH = ROOT / "benchmarks" / "suites" / "synthetic-v1.json"
SCHEMA_ROOT = ROOT / "packages" / "schemas"

MEDCPT_PARAMETERS = {
    "max_length": 512,
    "batch_size": 2,
    "padding_side": "right",
    "truncation": "longest_first",
    "score_mode": "relevance_logit",
    "device": "cpu",
    "dtype": "float32",
}


def _requires_regenerated_artifact(path: Path) -> None:
    """Skip when a checked-in sealed artifact predates the current contract.

    These artifacts are outputs of the sealing pipeline, not fixtures: regenerating
    them needs the release database, signing keys, and Qdrant. Skipping keyed on the
    contract version means the check re-arms by itself once the artifact is rebuilt,
    instead of being silently deleted or hand-edited back into agreement.
    """

    version = json.loads(path.read_text(encoding="utf-8"))["content"]["schema_version"]
    if version != BENCHMARK_CONTRACT_VERSION:
        pytest.skip(
            f"{path.name} is sealed at contract {version}; "
            f"regenerate at {BENCHMARK_CONTRACT_VERSION} "
            "(corpus-steward benchmark-generate-source-derived / benchmark-seal-suite)"
        )


def fixture_inputs() -> tuple[CorpusReleaseBundle, BenchmarkSuite]:
    bundle = CorpusReleaseBundle.model_validate_json(
        FIXTURE_PATH.read_text(encoding="utf-8")
    )
    synthetic = BenchmarkSuite.model_validate_json(
        SUITE_PATH.read_text(encoding="utf-8")
    )
    development_content = synthetic.content.model_copy(
        update={"suite_partition": BenchmarkSuitePartition.DEVELOPMENT}
    )
    development = BenchmarkSuite.model_construct(
        content=development_content,
        suite_sha256=synthetic.suite_sha256,
    )
    return bundle, development


def request(
    bundle: CorpusReleaseBundle, *targets: RerankerRuntimeTarget
) -> RerankerRuntimeMatrixRequest:
    return RerankerRuntimeMatrixRequest(
        matrix_id="fixture-reranker-runtime",
        corpus_release_id=bundle.manifest.content.corpus_release_id,
        manifest_sha256=bundle.manifest.manifest_sha256,
        query_sample_count=1,
        pool_size=1,
        warmup_runs=0,
        measured_runs=1,
        targets=targets or (missing_target(),),
    )


def missing_target() -> RerankerRuntimeTarget:
    return RerankerRuntimeTarget(
        target_id="qwen3-reranker-0.6b-cpu-float32",
        model_id="Qwen/Qwen3-Reranker-0.6B",
        model_revision="e" * 40,
        artifact_root="models/local/missing-reranker",
        artifact_manifest_path="data/local/missing-reranker-manifest.json",
        expected_artifact_sha256=None,
        device="cpu",
        dtype="float32",
        max_length=2048,
        batch_size=2,
    )


def _medcpt_artifact_root(workspace_root: Path) -> Path:
    root = workspace_root / "models" / "local" / "medcpt-cross-encoder"
    root.mkdir(parents=True)
    (root / "config.json").write_text("{}\n", encoding="utf-8")
    return root


def _medcpt_manifest(root: Path, parameters: dict[str, object]):
    return build_model_artifact_manifest(
        root,
        artifact_kind=ModelArtifactKind.RERANKER,
        model_id="ncbi/MedCPT-Cross-Encoder",
        revision="c" * 40,
        dimension=1,
        adapter_id=MEDCPT_CROSS_ENCODER_ADAPTER_ID,
        adapter_revision=MEDCPT_CROSS_ENCODER_ADAPTER_REVISION,
        adapter_parameters=parameters,
    )


def sealed_medcpt_target(
    workspace_root: Path, *, target_batch_size: int = 2
) -> RerankerRuntimeTarget:
    """A MedCPT target whose artifact is genuinely present and digest-verified."""

    root = _medcpt_artifact_root(workspace_root)
    manifest = _medcpt_manifest(root, MEDCPT_PARAMETERS)
    manifest_path = workspace_root / "data" / "local" / "medcpt-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    return RerankerRuntimeTarget(
        target_id="medcpt-cross-encoder-cpu-float32",
        model_id="ncbi/MedCPT-Cross-Encoder",
        model_revision="c" * 40,
        artifact_root="models/local/medcpt-cross-encoder",
        artifact_manifest_path="data/local/medcpt-manifest.json",
        expected_artifact_sha256=manifest.artifact_sha256,
        device="cpu",
        dtype="float32",
        max_length=512,
        batch_size=target_batch_size,
    )


async def test_reranker_matrix_seals_all_missing_artifact_blockers(
    tmp_path: Path,
) -> None:
    _requires_regenerated_artifact(SUITE_PATH)
    bundle, suite = fixture_inputs()

    report = await execute_reranker_runtime_matrix(
        request(bundle),
        bundle,
        suite,
        workspace_root=tmp_path.resolve(),
    )

    result = report.content.results[0]
    assert report.content.outcome == "BLOCKED"
    assert result.status == "BLOCKED"
    assert result.blockers == (
        "ARTIFACT_SHA256_PIN_MISSING",
        "LOCAL_MODEL_ROOT_MISSING",
        "MODEL_ARTIFACT_MANIFEST_MISSING",
    )
    assert result.measurements is None


async def test_reranker_matrix_rejects_non_development_suite(tmp_path: Path) -> None:
    _requires_regenerated_artifact(SUITE_PATH)
    bundle = CorpusReleaseBundle.model_validate_json(
        FIXTURE_PATH.read_text(encoding="utf-8")
    )
    suite = BenchmarkSuite.model_validate_json(SUITE_PATH.read_text(encoding="utf-8"))

    with pytest.raises(ValueError, match="development suite"):
        await execute_reranker_runtime_matrix(
            request(bundle),
            bundle,
            suite,
            workspace_root=tmp_path.resolve(),
        )


def test_checked_in_reranker_runtime_schemas_match_contract() -> None:
    for filename, expected in reranker_runtime_matrix_schema_documents().items():
        actual = json.loads((SCHEMA_ROOT / filename).read_text(encoding="utf-8"))
        assert actual == expected


def test_every_registered_reranker_family_is_an_allowlisted_target() -> None:
    """All three lanes are targetable, and the allowlist is one frozenset union."""

    assert RERANKER_RUNTIME_MODEL_IDS == (
        QWEN3_RERANKER_MODEL_IDS
        | BGE_RERANKER_V2_M3_MODEL_IDS
        | MEDCPT_CROSS_ENCODER_MODEL_IDS
    )
    assert isinstance(RERANKER_RUNTIME_MODEL_IDS, frozenset)
    assert {
        "Qwen/Qwen3-Reranker-0.6B",
        "BAAI/bge-reranker-v2-m3",
        "ncbi/MedCPT-Cross-Encoder",
    } <= RERANKER_RUNTIME_MODEL_IDS


@pytest.mark.parametrize(
    ("model_id", "max_length"),
    [
        ("Qwen/Qwen3-Reranker-0.6B", 2048),
        ("BAAI/bge-reranker-v2-m3", 8192),
        ("ncbi/MedCPT-Cross-Encoder", 512),
    ],
)
def test_each_reranker_family_validates_as_a_target(
    model_id: str, max_length: int
) -> None:
    target = RerankerRuntimeTarget(
        target_id="target",
        model_id=model_id,
        model_revision="a" * 40,
        artifact_root="models/local/reranker",
        artifact_manifest_path="data/local/manifest.json",
        device="cpu",
        dtype="float32",
        max_length=max_length,
        batch_size=2,
    )

    assert target.model_id == model_id


def test_unlisted_reranker_model_is_rejected_as_a_target() -> None:
    with pytest.raises(ValueError, match="not allowlisted"):
        RerankerRuntimeTarget(
            target_id="target",
            model_id="some-org/unreviewed-reranker",
            model_revision="a" * 40,
            artifact_root="models/local/reranker",
            artifact_manifest_path="data/local/manifest.json",
            device="cpu",
            dtype="float32",
            max_length=512,
            batch_size=2,
        )


def measurements(minimum: float, maximum: float) -> RerankerRuntimeMeasurements:
    return RerankerRuntimeMeasurements(
        query_count=1,
        pool_size=3,
        pair_count=3,
        query_mean_latency_ms=1.0,
        query_p95_latency_ms=1.0,
        run_mean_latency_ms=1.0,
        pairs_per_second=3.0,
        minimum_score=minimum,
        maximum_score=maximum,
        score_reproducibility_delta=0.0,
        truncated_pair_count=0,
    )


def test_medcpt_logits_survive_the_measurement_contract() -> None:
    """The published MedCPT example output is [6.9363, -8.2063, -15.8475].

    A 0-1 probability guard would fail validation on every MedCPT run and seal the
    target BLOCKED forever, never MEASURED, so the score domain is deliberately a
    per-adapter property rather than a contract property.
    """

    measured = measurements(-15.8475, 6.9363)

    assert measured.minimum_score == -15.8475
    assert measured.maximum_score == 6.9363


def test_sigmoid_bounded_lanes_still_validate() -> None:
    measured = measurements(0.0, 1.0)

    assert (measured.minimum_score, measured.maximum_score) == (0.0, 1.0)


def test_measurements_still_reject_an_inverted_score_range() -> None:
    with pytest.raises(ValueError, match="score range is inverted"):
        measurements(6.9363, -15.8475)


def test_measurements_still_reject_non_finite_scores() -> None:
    with pytest.raises(ValueError):
        measurements(float("-inf"), float("nan"))


def test_sealed_instruction_reads_only_the_qwen3_parameters() -> None:
    """Neither specialist has an instruction field, so neither can be read for one."""

    qwen = Qwen3RerankerParameters(
        instruction="Retrieve clinical guideline passages that answer the query.",
        max_length=2048,
        batch_size=2,
        device="cpu",
        dtype="float32",
    )
    bge = BGERerankerV2M3Parameters(
        max_length=8192, batch_size=2, device="cpu", dtype="float32"
    )
    medcpt = MedCPTCrossEncoderParameters(batch_size=2, device="cpu", dtype="float32")

    assert not hasattr(bge, "instruction")
    assert not hasattr(medcpt, "instruction")
    assert _sealed_instruction(_QWEN3_RERANKER_IDENTITY, qwen) == qwen.instruction
    assert _sealed_instruction(_BGE_RERANKER_IDENTITY, bge) == ""
    assert _sealed_instruction(_MEDCPT_RERANKER_IDENTITY, medcpt) == ""


class _TokenizerCall(NamedTuple):
    segments: tuple[str, ...]
    add_special_tokens: bool


class _WhitespaceTokenizer:
    """Just enough of a fast tokenizer to observe how each family builds its input."""

    def __init__(self) -> None:
        self.calls: list[_TokenizerCall] = []

    def __call__(
        self, *segments: str, truncation: bool = False, add_special_tokens: bool = True
    ) -> dict[str, list[int]]:
        self.calls.append(_TokenizerCall(segments, add_special_tokens))
        tokens = [token for segment in segments for token in segment.split()]
        # A real fast tokenizer inserts its own separators around each segment of a
        # sequence pair; a single pre-formatted prompt string gets none of them.
        separators = 2 * len(segments) if add_special_tokens else 0
        return {"input_ids": list(range(len(tokens) + separators))}


def install_tokenizer(monkeypatch: pytest.MonkeyPatch) -> _WhitespaceTokenizer:
    tokenizer = _WhitespaceTokenizer()
    module = types.ModuleType("transformers")
    module.AutoTokenizer = types.SimpleNamespace(
        from_pretrained=lambda *args, **kwargs: tokenizer
    )
    monkeypatch.setitem(sys.modules, "transformers", module)
    return tokenizer


@pytest.mark.parametrize(
    "adapter_identity", [_BGE_RERANKER_IDENTITY, _MEDCPT_RERANKER_IDENTITY]
)
def test_both_specialists_are_counted_as_genuine_sequence_pairs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, adapter_identity: tuple[str, str]
) -> None:
    """MedCPT and bge-reranker-v2-m3 both encode ``tokenizer(question, document)``."""

    tokenizer = install_tokenizer(monkeypatch)

    truncated = _truncated_pair_count(
        tmp_path,
        ("alpha beta",),
        ("gamma", "delta epsilon zeta"),
        adapter_identity=adapter_identity,
        instruction="",
        max_length=8,
    )

    assert [len(call.segments) for call in tokenizer.calls] == [2, 2]
    assert tokenizer.calls[0].segments == ("alpha beta", "gamma")
    assert all(_OFFICIAL_PREFIX not in call.segments[0] for call in tokenizer.calls)
    # 2 + 1 query/document tokens plus 4 pair separators fits 8; 2 + 3 plus 4 does not.
    assert truncated == 1


def test_qwen3_is_counted_as_one_formatted_prompt_with_chat_overhead(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tokenizer = install_tokenizer(monkeypatch)

    truncated = _truncated_pair_count(
        tmp_path,
        ("alpha beta",),
        ("gamma", "delta epsilon zeta"),
        adapter_identity=_QWEN3_RERANKER_IDENTITY,
        instruction="Retrieve guideline passages.",
        max_length=8,
    )

    assert tokenizer.calls[0] == _TokenizerCall((_OFFICIAL_PREFIX,), False)
    assert tokenizer.calls[1] == _TokenizerCall((_OFFICIAL_SUFFIX,), False)
    assert [len(call.segments) for call in tokenizer.calls[2:]] == [1, 1]
    assert "<Instruct>: Retrieve guideline passages." in tokenizer.calls[2].segments[0]
    # The fixed chat prefix and suffix alone exceed an 8-token budget, so every pair
    # is truncated -- overhead the sequence-pair count must not borrow.
    assert truncated == 2


class _CountingRuntime:
    def __init__(self, scores: tuple[float, ...]) -> None:
        self._scores = scores
        self.calls: list[tuple[tuple[str, str], ...]] = []

    def score(
        self,
        pairs: Sequence[tuple[str, str]],
        *,
        instruction: str,
        max_length: int,
    ) -> Sequence[float]:
        converted = tuple(pairs)
        self.calls.append(converted)
        return self._scores[: len(converted)]


async def test_clearing_the_memory_cache_makes_a_repeat_run_measure_again(
    tmp_path: Path,
) -> None:
    """The trap this harness exists to avoid: runs 2..n replayed from run 1.

    The shared reranker base caches scores in memory even with no on-disk cache path,
    so an uncleared repeat-run loop reports a throughput no deployment would ever see.
    This pins the contract the matrix depends on, from the harness's side of it.
    """

    root = _medcpt_artifact_root(tmp_path)
    manifest = _medcpt_manifest(root, MEDCPT_PARAMETERS)
    artifact = verify_model_artifact(
        root,
        manifest,
        expected_artifact_sha256=manifest.artifact_sha256,
        expected_kind=ModelArtifactKind.RERANKER,
    )
    runtime = _CountingRuntime((6.9363, -15.8475))
    adapter = MedCPTCrossEncoderAdapter(artifact, runtime=runtime)

    first = await adapter.score("query", ("one", "two"))
    await adapter.score("query", ("one", "two"))
    assert len(runtime.calls) == 1

    adapter.clear_memory_score_cache()
    repeated = await adapter.score("query", ("one", "two"))

    assert len(runtime.calls) == 2
    assert repeated == first


async def test_medcpt_target_is_measured_through_the_shared_registry(
    tmp_path: Path,
) -> None:
    """A verified MedCPT artifact must reach adapter construction, not a contract wall.

    Before the reranker matrix knew this lane, a MedCPT target could not be expressed
    at all. It now clears the allowlist, its own parameter schema, and the target/
    artifact parameter cross-check, and only stops where a fixture must: there are no
    real weights under the artifact root to load.
    """

    _requires_regenerated_artifact(SUITE_PATH)
    bundle, suite = fixture_inputs()
    workspace_root = tmp_path.resolve()

    report = await execute_reranker_runtime_matrix(
        request(bundle, sealed_medcpt_target(workspace_root)),
        bundle,
        suite,
        workspace_root=workspace_root,
    )

    result = report.content.results[0]
    assert result.status == "BLOCKED"
    blocker = result.blockers[0]
    assert "allowlisted reranker adapter" not in blocker
    assert "do not match matrix target" not in blocker
    assert blocker.startswith("AdapterConfigurationError:")


async def test_reranker_target_must_match_its_sealed_artifact_parameters(
    tmp_path: Path,
) -> None:
    """The MedCPT parameter schema is really validated, not skipped as unknown."""

    _requires_regenerated_artifact(SUITE_PATH)
    bundle, suite = fixture_inputs()
    workspace_root = tmp_path.resolve()

    report = await execute_reranker_runtime_matrix(
        request(bundle, sealed_medcpt_target(workspace_root, target_batch_size=8)),
        bundle,
        suite,
        workspace_root=workspace_root,
    )

    result = report.content.results[0]
    assert result.status == "BLOCKED"
    assert result.blockers == (
        "ValueError:reranker artifact runtime parameters do not match matrix target",
    )
