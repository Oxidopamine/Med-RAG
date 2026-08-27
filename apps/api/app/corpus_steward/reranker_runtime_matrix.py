"""Pinned reranker runtime-matrix execution against verified local artifacts.

The dense sibling of this module is `qwen_runtime_matrix`. The two are deliberately
parallel: same request/report contract shape, same sealed digest, same "measure it or
seal exactly why it could not be measured" outcome. They stay separate because a
reranker measures a different quantity -- pair throughput and score stability over a
candidate pool, not per-item embedding latency and unit-norm deviation -- and folding
both into one contract would make every field optional for one of them.
"""

from __future__ import annotations

import math
import platform
import sys
from datetime import datetime
from importlib import metadata
from pathlib import Path
from statistics import fmean
from time import perf_counter
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.corpus_steward.adapter_registry import default_adapter_registry
from app.corpus_steward.benchmark_schemas import BenchmarkSuite, BenchmarkSuitePartition
from app.corpus_steward.model_artifacts import (
    ModelArtifactKind,
    ModelArtifactManifest,
    verify_model_artifact,
)
from app.corpus_steward.reranking import (
    _OFFICIAL_PREFIX,
    _OFFICIAL_SUFFIX,
    BGE_RERANKER_V2_M3_ADAPTER_ID,
    BGE_RERANKER_V2_M3_ADAPTER_REVISION,
    BGE_RERANKER_V2_M3_INSTRUCTION,
    BGE_RERANKER_V2_M3_MODEL_IDS,
    MEDCPT_CROSS_ENCODER_ADAPTER_ID,
    MEDCPT_CROSS_ENCODER_ADAPTER_REVISION,
    MEDCPT_CROSS_ENCODER_INSTRUCTION,
    MEDCPT_CROSS_ENCODER_MODEL_IDS,
    QWEN3_RERANKER_ADAPTER_ID,
    QWEN3_RERANKER_ADAPTER_REVISION,
    QWEN3_RERANKER_MODEL_IDS,
    BGERerankerV2M3Parameters,
    MedCPTCrossEncoderParameters,
    Qwen3RerankerParameters,
    _CachedRerankerAdapter,
)
from app.schemas.corpus import SHA256_PATTERN, CorpusReleaseBundle, canonical_sha256
from app.schemas.domain import CanonicalModel, utc_now

RERANKER_RUNTIME_MATRIX_CONTRACT_VERSION = "1.0.0"

RERANKER_RUNTIME_MODEL_IDS = (
    QWEN3_RERANKER_MODEL_IDS | BGE_RERANKER_V2_M3_MODEL_IDS | MEDCPT_CROSS_ENCODER_MODEL_IDS
)
_QWEN3_RERANKER_IDENTITY = (QWEN3_RERANKER_ADAPTER_ID, QWEN3_RERANKER_ADAPTER_REVISION)
_BGE_RERANKER_IDENTITY = (
    BGE_RERANKER_V2_M3_ADAPTER_ID,
    BGE_RERANKER_V2_M3_ADAPTER_REVISION,
)
_MEDCPT_RERANKER_IDENTITY = (
    MEDCPT_CROSS_ENCODER_ADAPTER_ID,
    MEDCPT_CROSS_ENCODER_ADAPTER_REVISION,
)

_RerankerParameters = (
    Qwen3RerankerParameters | BGERerankerV2M3Parameters | MedCPTCrossEncoderParameters
)
# Each allowlisted reranker adapter pins the parameter schema its sealed artifact must
# validate against, so a target can never be measured under another lane's contract.
_RERANKER_PARAMETER_MODELS: dict[tuple[str, str], type[_RerankerParameters]] = {
    _QWEN3_RERANKER_IDENTITY: Qwen3RerankerParameters,
    _BGE_RERANKER_IDENTITY: BGERerankerV2M3Parameters,
    _MEDCPT_RERANKER_IDENTITY: MedCPTCrossEncoderParameters,
}
# Neither specialist has an instruction field in its sealed parameters, by design:
# their published contracts have no instruction segment at all. The module constant
# empty string is the value they apply, and its digest is what binds "this backend
# applies no instruction" into the pin.
_SPECIALIST_INSTRUCTIONS: dict[tuple[str, str], str] = {
    _BGE_RERANKER_IDENTITY: BGE_RERANKER_V2_M3_INSTRUCTION,
    _MEDCPT_RERANKER_IDENTITY: MEDCPT_CROSS_ENCODER_INSTRUCTION,
}
# Both specialists are cross-encoders that encode a genuine two-segment sequence pair,
# unlike Qwen3, which flattens the pair into one formatted prompt string.
_SEQUENCE_PAIR_IDENTITIES = frozenset(
    {_BGE_RERANKER_IDENTITY, _MEDCPT_RERANKER_IDENTITY}
)
# Bounds the worst case a single sealed request can ask a GPU box to score.
_MAXIMUM_MEASURED_PAIRS = 100_000


def _sealed_instruction(
    adapter_identity: tuple[str, str], parameters: _RerankerParameters
) -> str:
    """Resolve the instruction a backend actually applies to every scored pair."""

    if adapter_identity == _QWEN3_RERANKER_IDENTITY:
        return parameters.instruction
    return _SPECIALIST_INSTRUCTIONS[adapter_identity]


class RerankerRuntimeTarget(CanonicalModel):
    target_id: str = Field(min_length=1, max_length=100)
    model_id: str = Field(min_length=1, max_length=200)
    model_revision: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    artifact_root: str = Field(min_length=1, max_length=500)
    artifact_manifest_path: str = Field(min_length=1, max_length=500)
    expected_artifact_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    device: str = Field(min_length=1, max_length=50)
    dtype: Literal["float32", "float16", "bfloat16", "int8_dynamic"]
    max_length: int = Field(gt=0, le=32_768)
    batch_size: int = Field(gt=0, le=1_024)

    @model_validator(mode="after")
    def validate_supported_model(self) -> RerankerRuntimeTarget:
        if self.model_id not in RERANKER_RUNTIME_MODEL_IDS:
            raise ValueError("reranker runtime target model is not allowlisted")
        return self


class RerankerRuntimeMatrixRequest(CanonicalModel):
    schema_version: Literal[RERANKER_RUNTIME_MATRIX_CONTRACT_VERSION] = (
        RERANKER_RUNTIME_MATRIX_CONTRACT_VERSION
    )
    matrix_id: str = Field(min_length=1, max_length=100)
    corpus_release_id: str = Field(min_length=1, max_length=64)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    query_sample_count: int = Field(default=8, gt=0, le=10_000)
    # Documents are the candidate pool re-scored against every sampled query, so the
    # measured work is the product of the two counts, not their sum.
    pool_size: int = Field(default=32, gt=0, le=1_000)
    warmup_runs: int = Field(default=1, ge=0, le=20)
    measured_runs: int = Field(default=3, gt=0, le=100)
    targets: tuple[RerankerRuntimeTarget, ...] = Field(min_length=1)

    @field_validator("targets")
    @classmethod
    def sort_unique_targets(
        cls, value: tuple[RerankerRuntimeTarget, ...]
    ) -> tuple[RerankerRuntimeTarget, ...]:
        ids = [item.target_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("reranker runtime target IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.target_id))

    @model_validator(mode="after")
    def validate_pair_budget(self) -> RerankerRuntimeMatrixRequest:
        if self.query_sample_count * self.pool_size > _MAXIMUM_MEASURED_PAIRS:
            raise ValueError("reranker runtime matrix pair budget is exceeded")
        return self


class RerankerRuntimeMeasurements(CanonicalModel):
    query_count: int = Field(gt=0)
    pool_size: int = Field(gt=0)
    pair_count: int = Field(gt=0)
    initialization_latency_ms: float | None = Field(
        default=None, ge=0, exclude_if=lambda value: value is None
    )
    cold_query_latency_ms: float | None = Field(
        default=None, ge=0, exclude_if=lambda value: value is None
    )
    query_mean_latency_ms: float = Field(ge=0)
    query_p95_latency_ms: float = Field(ge=0)
    run_mean_latency_ms: float = Field(ge=0)
    pairs_per_second: float = Field(gt=0)
    # Deliberately unbounded beyond finiteness. A bounded score domain is a per-adapter
    # property, not a contract property: Qwen3 and bge-reranker-v2-m3 seal sigmoid
    # 0-1 scores, while ncbi/MedCPT-Cross-Encoder returns raw relevance logits whose
    # own published example output is [6.9363, -8.2063, -15.8475]. A 0-1 guard here
    # would fail validation on every MedCPT run and seal the target BLOCKED forever,
    # never MEASURED -- reporting a contract violation where the real result is a
    # working specialist on its own scale. See `_score_bounds` in `reranking`.
    minimum_score: float = Field(allow_inf_nan=False)
    maximum_score: float = Field(allow_inf_nan=False)
    # How far the same (query, document) pair drifted between measured runs. A
    # non-trivial value means the runtime is not deterministic at this dtype, which
    # invalidates any later quality comparison drawn from a single pass.
    score_reproducibility_delta: float = Field(ge=0)
    truncated_pair_count: int = Field(ge=0)
    peak_process_rss_bytes: int | None = Field(default=None, ge=0)
    peak_cuda_memory_bytes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_score_range(self) -> RerankerRuntimeMeasurements:
        if self.minimum_score > self.maximum_score:
            raise ValueError("reranker score range is inverted")
        return self


class RerankerRuntimeTargetResult(CanonicalModel):
    target: RerankerRuntimeTarget
    status: Literal["MEASURED", "BLOCKED"]
    artifact_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    adapter_id: str | None = Field(default=None, min_length=1, max_length=300)
    adapter_revision: str | None = Field(default=None, min_length=1, max_length=300)
    blockers: tuple[str, ...] = ()
    measurements: RerankerRuntimeMeasurements | None = None

    @model_validator(mode="after")
    def validate_status(self) -> RerankerRuntimeTargetResult:
        if self.status == "MEASURED":
            if (
                self.blockers
                or self.measurements is None
                or self.artifact_sha256 is None
                or self.adapter_id is None
                or self.adapter_revision is None
            ):
                raise ValueError(
                    "measured reranker targets require an artifact, adapter identity, "
                    "and measurements"
                )
        elif not self.blockers or self.measurements is not None:
            raise ValueError("blocked reranker targets require blockers and no measurements")
        return self


class RerankerRuntimeEnvironment(CanonicalModel):
    operating_system: str = Field(min_length=1, max_length=300)
    processor: str = Field(min_length=1, max_length=300)
    python_version: str = Field(min_length=1, max_length=100)
    torch_version: str | None = Field(default=None, max_length=100)
    transformers_version: str | None = Field(default=None, max_length=100)
    cuda_available: bool
    cuda_device_names: tuple[str, ...] = ()


class RerankerRuntimeMatrixReportContent(CanonicalModel):
    schema_version: Literal[RERANKER_RUNTIME_MATRIX_CONTRACT_VERSION] = (
        RERANKER_RUNTIME_MATRIX_CONTRACT_VERSION
    )
    matrix_id: str = Field(min_length=1, max_length=100)
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    corpus_release_id: str = Field(min_length=1, max_length=64)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    development_suite_sha256: str = Field(pattern=SHA256_PATTERN)
    environment: RerankerRuntimeEnvironment
    executed_at: datetime
    results: tuple[RerankerRuntimeTargetResult, ...] = Field(min_length=1)
    outcome: Literal["COMPLETE", "BLOCKED"]

    @model_validator(mode="after")
    def validate_outcome(self) -> RerankerRuntimeMatrixReportContent:
        blocked = any(item.status == "BLOCKED" for item in self.results)
        if (self.outcome == "BLOCKED") != blocked:
            raise ValueError("reranker runtime matrix outcome is inconsistent")
        return self


class RerankerRuntimeMatrixReport(CanonicalModel):
    content: RerankerRuntimeMatrixReportContent
    report_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> RerankerRuntimeMatrixReport:
        if self.report_sha256 != canonical_sha256(self.content):
            raise ValueError("reranker runtime matrix report digest is inconsistent")
        return self

    @classmethod
    def seal(
        cls, content: RerankerRuntimeMatrixReportContent
    ) -> RerankerRuntimeMatrixReport:
        return cls(content=content, report_sha256=canonical_sha256(content))


async def execute_reranker_runtime_matrix(
    request: RerankerRuntimeMatrixRequest,
    bundle: CorpusReleaseBundle,
    suite: BenchmarkSuite,
    *,
    workspace_root: Path,
) -> RerankerRuntimeMatrixReport:
    if request.corpus_release_id != bundle.manifest.content.corpus_release_id:
        raise ValueError("reranker matrix release does not match the bundle")
    if request.manifest_sha256 != bundle.manifest.manifest_sha256:
        raise ValueError("reranker matrix manifest does not match the bundle")
    if suite.content.suite_partition is not BenchmarkSuitePartition.DEVELOPMENT:
        raise ValueError("reranker runtime measurements require a development suite")
    if (
        suite.content.corpus_release_id != request.corpus_release_id
        or suite.content.manifest_sha256 != request.manifest_sha256
    ):
        raise ValueError("reranker matrix development suite does not match the release")
    questions = tuple(
        item.question
        for item in sorted(suite.content.cases, key=lambda case: case.case_id)[
            : request.query_sample_count
        ]
    )
    documents = tuple(
        item.content_search
        for item in sorted(bundle.evidence, key=lambda evidence: evidence.evidence_id)[
            : request.pool_size
        ]
    )
    results = tuple(
        [
            await _measure_target(
                target,
                questions,
                documents,
                request=request,
                workspace_root=workspace_root,
            )
            for target in request.targets
        ]
    )
    return RerankerRuntimeMatrixReport.seal(
        RerankerRuntimeMatrixReportContent(
            matrix_id=request.matrix_id,
            request_sha256=canonical_sha256(request),
            corpus_release_id=request.corpus_release_id,
            manifest_sha256=request.manifest_sha256,
            development_suite_sha256=suite.suite_sha256,
            environment=_runtime_environment(),
            executed_at=utc_now(),
            results=results,
            outcome=(
                "BLOCKED" if any(item.status == "BLOCKED" for item in results) else "COMPLETE"
            ),
        )
    )


async def _measure_target(
    target: RerankerRuntimeTarget,
    questions: tuple[str, ...],
    documents: tuple[str, ...],
    *,
    request: RerankerRuntimeMatrixRequest,
    workspace_root: Path,
) -> RerankerRuntimeTargetResult:
    root = (workspace_root / Path(target.artifact_root)).resolve()
    manifest_path = (workspace_root / Path(target.artifact_manifest_path)).resolve()
    if not root.is_relative_to(workspace_root) or not manifest_path.is_relative_to(
        workspace_root
    ):
        return RerankerRuntimeTargetResult(
            target=target,
            status="BLOCKED",
            blockers=("ARTIFACT_PATH_OUTSIDE_WORKSPACE",),
        )
    blockers = []
    if target.expected_artifact_sha256 is None:
        blockers.append("ARTIFACT_SHA256_PIN_MISSING")
    if not root.is_dir():
        blockers.append("LOCAL_MODEL_ROOT_MISSING")
    if not manifest_path.is_file():
        blockers.append("MODEL_ARTIFACT_MANIFEST_MISSING")
    if blockers:
        return RerankerRuntimeTargetResult(
            target=target,
            status="BLOCKED",
            blockers=tuple(blockers),
        )
    try:
        manifest = ModelArtifactManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        verified = verify_model_artifact(
            root,
            manifest,
            expected_artifact_sha256=target.expected_artifact_sha256,
            expected_kind=ModelArtifactKind.RERANKER,
        )
        content = manifest.content
        adapter_identity = (content.adapter_id, content.adapter_revision)
        parameter_model = _RERANKER_PARAMETER_MODELS.get(adapter_identity)
        if parameter_model is None:
            raise ValueError(
                "verified artifact does not use an allowlisted reranker adapter"
            )
        parameters = parameter_model.model_validate(content.adapter_parameters)
        instruction = _sealed_instruction(adapter_identity, parameters)
        if (
            content.model_id != target.model_id
            or content.revision != target.model_revision
            or content.dimension != 1
        ):
            raise ValueError("verified artifact does not match the pinned reranker target")
        if (
            parameters.device != target.device
            or parameters.dtype != target.dtype
            or parameters.max_length != target.max_length
            or parameters.batch_size != target.batch_size
        ):
            raise ValueError(
                "reranker artifact runtime parameters do not match matrix target"
            )
        initialization_started = perf_counter()
        adapter = default_adapter_registry().create_reranker(verified, device=target.device)
        initialization_latency_ms = (perf_counter() - initialization_started) * 1_000
        if not isinstance(adapter, _CachedRerankerAdapter):
            raise ValueError("reranker runtime measurement requires an artifact-bound adapter")
        cold_started = perf_counter()
        await adapter.score(questions[0], documents)
        cold_query_latency_ms = (perf_counter() - cold_started) * 1_000
        for _ in range(request.warmup_runs):
            adapter.clear_memory_score_cache()
            await adapter.score(questions[0], documents)
        query_latencies: list[float] = []
        run_latencies: list[float] = []
        run_scores: list[tuple[float, ...]] = []
        for _ in range(request.measured_runs):
            # Without this the second run is served entirely from the adapter's own
            # in-memory score cache and would report a throughput no deployment sees.
            adapter.clear_memory_score_cache()
            run_started = perf_counter()
            scores: list[float] = []
            for question in questions:
                started = perf_counter()
                scores.extend(await adapter.score(question, documents))
                query_latencies.append((perf_counter() - started) * 1_000)
            run_latencies.append((perf_counter() - run_started) * 1_000)
            run_scores.append(tuple(scores))
        baseline = run_scores[0]
        reproducibility_delta = max(
            (
                abs(value - reference)
                for run in run_scores[1:]
                for value, reference in zip(run, baseline, strict=True)
            ),
            default=0.0,
        )
        pair_count = len(questions) * len(documents)
        return RerankerRuntimeTargetResult(
            target=target,
            status="MEASURED",
            artifact_sha256=manifest.artifact_sha256,
            adapter_id=content.adapter_id,
            adapter_revision=content.adapter_revision,
            measurements=RerankerRuntimeMeasurements(
                query_count=len(questions),
                pool_size=len(documents),
                pair_count=pair_count,
                initialization_latency_ms=initialization_latency_ms,
                cold_query_latency_ms=cold_query_latency_ms,
                query_mean_latency_ms=fmean(query_latencies),
                query_p95_latency_ms=_p95(query_latencies),
                run_mean_latency_ms=fmean(run_latencies),
                pairs_per_second=pair_count / (fmean(run_latencies) / 1_000),
                minimum_score=min(baseline),
                maximum_score=max(baseline),
                score_reproducibility_delta=reproducibility_delta,
                truncated_pair_count=_truncated_pair_count(
                    root,
                    questions,
                    documents,
                    adapter_identity=adapter_identity,
                    instruction=instruction,
                    max_length=target.max_length,
                ),
                peak_process_rss_bytes=_peak_process_rss_bytes(),
                peak_cuda_memory_bytes=_peak_cuda_memory(target.device),
            ),
        )
    except Exception as error:
        return RerankerRuntimeTargetResult(
            target=target,
            status="BLOCKED",
            blockers=(f"{type(error).__name__}:{error}",),
        )


def _truncated_pair_count(
    root: Path,
    questions: tuple[str, ...],
    documents: tuple[str, ...],
    *,
    adapter_identity: tuple[str, str],
    instruction: str,
    max_length: int,
) -> int:
    """Count pairs the sealed maximum length would have to cut.

    Truncation is counted the way each family actually builds its input: Qwen3 wraps a
    single formatted string in a fixed chat prefix and suffix, while the two specialist
    cross-encoders -- bge-reranker-v2-m3 and MedCPT -- encode the query and document as
    a genuine two-segment sequence pair and let the tokenizer add its own separators.
    Counting either family the other's way misstates the budget by the separator tokens,
    in the direction that hides truncation.
    """

    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        str(root),
        local_files_only=True,
        trust_remote_code=False,
        use_fast=True,
    )
    if adapter_identity in _SEQUENCE_PAIR_IDENTITIES:
        return sum(
            len(tokenizer(question, document, truncation=False)["input_ids"]) > max_length
            for question in questions
            for document in documents
        )
    overhead = len(
        tokenizer(_OFFICIAL_PREFIX, add_special_tokens=False)["input_ids"]
    ) + len(tokenizer(_OFFICIAL_SUFFIX, add_special_tokens=False)["input_ids"])
    formatted = (
        f"<Instruct>: {instruction}\n<Query>: {question}\n<Document>: {document}"
        for question in questions
        for document in documents
    )
    return sum(
        len(tokenizer(text, truncation=False, add_special_tokens=False)["input_ids"])
        + overhead
        > max_length
        for text in formatted
    )


def _peak_cuda_memory(device: str) -> int | None:
    if not device.startswith("cuda"):
        return None
    import torch

    return int(torch.cuda.max_memory_allocated(device))


def _peak_process_rss_bytes() -> int | None:
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
        except (IndexError, OSError, ValueError):
            return None
    return None


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def _runtime_environment() -> RerankerRuntimeEnvironment:
    try:
        torch_version = metadata.version("torch")
    except metadata.PackageNotFoundError:
        torch_version = None
    try:
        import torch

        cuda_available = bool(torch.cuda.is_available())
        cuda_names = tuple(
            torch.cuda.get_device_name(index) for index in range(torch.cuda.device_count())
        )
    except (ImportError, OSError):
        cuda_available = False
        cuda_names = ()
    try:
        transformers_version = metadata.version("transformers")
    except metadata.PackageNotFoundError:
        transformers_version = None
    return RerankerRuntimeEnvironment(
        operating_system=platform.platform(),
        processor=platform.processor() or platform.machine() or "unknown",
        python_version=sys.version.split()[0],
        torch_version=torch_version,
        transformers_version=transformers_version,
        cuda_available=cuda_available,
        cuda_device_names=cuda_names,
    )
