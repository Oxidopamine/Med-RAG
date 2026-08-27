"""Pinned Qwen3 runtime-matrix execution against verified local artifacts."""

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
from app.corpus_steward.embedding_adapters import (
    BGE_M3_ADAPTER_ID,
    BGE_M3_ADAPTER_REVISION,
    BGE_M3_DIMENSION,
    BGE_M3_MODEL_ID,
    QWEN3_ADAPTER_ID,
    QWEN3_ADAPTER_REVISION,
    QWEN3_MODEL_DIMENSIONS,
    QWEN3_OPENVINO_ADAPTER_ID,
    QWEN3_OPENVINO_ADAPTER_REVISION,
    BGEM3AdapterParameters,
    Qwen3EmbeddingAdapterParameters,
    Qwen3OpenVINOAdapterParameters,
)
from app.corpus_steward.model_artifacts import (
    ModelArtifactKind,
    ModelArtifactManifest,
    verify_model_artifact,
)
from app.schemas.corpus import SHA256_PATTERN, CorpusReleaseBundle, canonical_sha256
from app.schemas.domain import CanonicalModel, utc_now

QWEN_RUNTIME_MATRIX_CONTRACT_VERSION = "1.0.0"

# This module measures every allowlisted dense adapter, not only Qwen3 — the name
# predates BGE-M3 joining the matrix. Each entry pins the parameter-schema class used
# to cross-check a target's declared runtime settings against its sealed artifact.
_DENSE_MODEL_DIMENSIONS: dict[str, int] = {
    **QWEN3_MODEL_DIMENSIONS,
    BGE_M3_MODEL_ID: BGE_M3_DIMENSION,
}
_DENSE_ADAPTER_PARAMETER_MODELS: dict[tuple[str, str], type] = {
    (QWEN3_ADAPTER_ID, QWEN3_ADAPTER_REVISION): Qwen3EmbeddingAdapterParameters,
    (QWEN3_OPENVINO_ADAPTER_ID, QWEN3_OPENVINO_ADAPTER_REVISION): Qwen3OpenVINOAdapterParameters,
    (BGE_M3_ADAPTER_ID, BGE_M3_ADAPTER_REVISION): BGEM3AdapterParameters,
}


class QwenRuntimeTarget(CanonicalModel):
    target_id: str = Field(min_length=1, max_length=100)
    model_id: str = Field(min_length=1, max_length=200)
    model_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    dimension: int = Field(gt=0)
    artifact_root: str = Field(min_length=1, max_length=500)
    artifact_manifest_path: str = Field(min_length=1, max_length=500)
    expected_artifact_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    device: str = Field(min_length=1, max_length=50)
    dtype: Literal["float32", "float16", "bfloat16", "int8"]
    max_length: int = Field(gt=0, le=32_768)
    batch_size: int = Field(gt=0, le=1_024)

    @model_validator(mode="after")
    def validate_supported_model(self) -> QwenRuntimeTarget:
        if _DENSE_MODEL_DIMENSIONS.get(self.model_id) != self.dimension:
            raise ValueError("dense runtime target model/dimension is not allowlisted")
        return self


class QwenRuntimeMatrixRequest(CanonicalModel):
    schema_version: Literal[QWEN_RUNTIME_MATRIX_CONTRACT_VERSION] = (
        QWEN_RUNTIME_MATRIX_CONTRACT_VERSION
    )
    matrix_id: str = Field(min_length=1, max_length=100)
    corpus_release_id: str = Field(min_length=1, max_length=64)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    query_sample_count: int = Field(default=32, gt=0, le=10_000)
    document_sample_count: int = Field(default=32, gt=0, le=10_000)
    warmup_runs: int = Field(default=1, ge=0, le=20)
    measured_runs: int = Field(default=3, gt=0, le=100)
    targets: tuple[QwenRuntimeTarget, ...] = Field(min_length=1)

    @field_validator("targets")
    @classmethod
    def sort_unique_targets(
        cls, value: tuple[QwenRuntimeTarget, ...]
    ) -> tuple[QwenRuntimeTarget, ...]:
        ids = [item.target_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("Qwen runtime target IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.target_id))


class QwenRuntimeMeasurements(CanonicalModel):
    query_count: int = Field(gt=0)
    document_count: int = Field(gt=0)
    initialization_latency_ms: float | None = Field(
        default=None, ge=0, exclude_if=lambda value: value is None
    )
    cold_query_latency_ms: float | None = Field(
        default=None, ge=0, exclude_if=lambda value: value is None
    )
    query_mean_latency_ms: float = Field(ge=0)
    query_p95_latency_ms: float = Field(ge=0)
    document_mean_latency_ms: float = Field(ge=0)
    document_p95_latency_ms: float = Field(ge=0)
    query_items_per_second: float = Field(gt=0)
    document_items_per_second: float = Field(gt=0)
    maximum_norm_deviation: float = Field(ge=0)
    truncated_query_count: int = Field(ge=0)
    truncated_document_count: int = Field(ge=0)
    peak_process_rss_bytes: int | None = Field(default=None, ge=0)
    peak_cuda_memory_bytes: int | None = Field(default=None, ge=0)


class QwenRuntimeTargetResult(CanonicalModel):
    target: QwenRuntimeTarget
    status: Literal["MEASURED", "BLOCKED"]
    artifact_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    blockers: tuple[str, ...] = ()
    measurements: QwenRuntimeMeasurements | None = None

    @model_validator(mode="after")
    def validate_status(self) -> QwenRuntimeTargetResult:
        if self.status == "MEASURED":
            if self.blockers or self.measurements is None or self.artifact_sha256 is None:
                raise ValueError("measured Qwen targets require an artifact and measurements")
        elif not self.blockers or self.measurements is not None:
            raise ValueError("blocked Qwen targets require blockers and no measurements")
        return self


class QwenRuntimeEnvironment(CanonicalModel):
    operating_system: str = Field(min_length=1, max_length=300)
    processor: str = Field(min_length=1, max_length=300)
    python_version: str = Field(min_length=1, max_length=100)
    torch_version: str | None = Field(default=None, max_length=100)
    transformers_version: str | None = Field(default=None, max_length=100)
    cuda_available: bool
    cuda_device_names: tuple[str, ...] = ()


class QwenRuntimeMatrixReportContent(CanonicalModel):
    schema_version: Literal[QWEN_RUNTIME_MATRIX_CONTRACT_VERSION] = (
        QWEN_RUNTIME_MATRIX_CONTRACT_VERSION
    )
    matrix_id: str = Field(min_length=1, max_length=100)
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    corpus_release_id: str = Field(min_length=1, max_length=64)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    development_suite_sha256: str = Field(pattern=SHA256_PATTERN)
    environment: QwenRuntimeEnvironment
    executed_at: datetime
    results: tuple[QwenRuntimeTargetResult, ...] = Field(min_length=1)
    outcome: Literal["COMPLETE", "BLOCKED"]

    @model_validator(mode="after")
    def validate_outcome(self) -> QwenRuntimeMatrixReportContent:
        blocked = any(item.status == "BLOCKED" for item in self.results)
        if (self.outcome == "BLOCKED") != blocked:
            raise ValueError("Qwen runtime matrix outcome is inconsistent")
        return self


class QwenRuntimeMatrixReport(CanonicalModel):
    content: QwenRuntimeMatrixReportContent
    report_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> QwenRuntimeMatrixReport:
        if self.report_sha256 != canonical_sha256(self.content):
            raise ValueError("Qwen runtime matrix report digest is inconsistent")
        return self

    @classmethod
    def seal(cls, content: QwenRuntimeMatrixReportContent) -> QwenRuntimeMatrixReport:
        return cls(content=content, report_sha256=canonical_sha256(content))


async def execute_qwen_runtime_matrix(
    request: QwenRuntimeMatrixRequest,
    bundle: CorpusReleaseBundle,
    suite: BenchmarkSuite,
    *,
    workspace_root: Path,
) -> QwenRuntimeMatrixReport:
    if request.corpus_release_id != bundle.manifest.content.corpus_release_id:
        raise ValueError("Qwen matrix release does not match the bundle")
    if request.manifest_sha256 != bundle.manifest.manifest_sha256:
        raise ValueError("Qwen matrix manifest does not match the bundle")
    if suite.content.suite_partition is not BenchmarkSuitePartition.DEVELOPMENT:
        raise ValueError("Qwen runtime measurements require a development suite")
    if (
        suite.content.corpus_release_id != request.corpus_release_id
        or suite.content.manifest_sha256 != request.manifest_sha256
    ):
        raise ValueError("Qwen matrix development suite does not match the release")
    questions = tuple(
        item.question
        for item in sorted(suite.content.cases, key=lambda case: case.case_id)[
            : request.query_sample_count
        ]
    )
    documents = tuple(
        item.content_search
        for item in sorted(bundle.evidence, key=lambda evidence: evidence.evidence_id)[
            : request.document_sample_count
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
    return QwenRuntimeMatrixReport.seal(
        QwenRuntimeMatrixReportContent(
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
    target: QwenRuntimeTarget,
    questions: tuple[str, ...],
    documents: tuple[str, ...],
    *,
    request: QwenRuntimeMatrixRequest,
    workspace_root: Path,
) -> QwenRuntimeTargetResult:
    root = (workspace_root / Path(target.artifact_root)).resolve()
    manifest_path = (workspace_root / Path(target.artifact_manifest_path)).resolve()
    if not root.is_relative_to(workspace_root) or not manifest_path.is_relative_to(
        workspace_root
    ):
        return QwenRuntimeTargetResult(
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
        return QwenRuntimeTargetResult(
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
        )
        content = manifest.content
        adapter_identity = (content.adapter_id, content.adapter_revision)
        parameter_model = _DENSE_ADAPTER_PARAMETER_MODELS.get(adapter_identity)
        if parameter_model is None:
            raise ValueError("verified artifact does not use an allowlisted dense adapter")
        parameters = parameter_model.model_validate(content.adapter_parameters)
        if (
            content.artifact_kind is not ModelArtifactKind.DENSE
            or content.model_id != target.model_id
            or content.revision != target.model_revision
            or content.dimension != target.dimension
        ):
            raise ValueError("verified artifact does not match the pinned dense target")
        if (
            parameters.device != target.device
            or parameters.dtype != target.dtype
            or parameters.max_length != target.max_length
            or parameters.batch_size != target.batch_size
        ):
            raise ValueError("dense artifact runtime parameters do not match matrix target")
        initialization_started = perf_counter()
        adapter = default_adapter_registry().create_dense(
            verified, device=target.device
        )
        initialization_latency_ms = (perf_counter() - initialization_started) * 1_000
        cold_started = perf_counter()
        await adapter.embed_queries(questions[:1])
        cold_query_latency_ms = (perf_counter() - cold_started) * 1_000
        for _ in range(request.warmup_runs):
            await adapter.embed_queries(questions[:1])
            await adapter.embed_documents(documents[:1])
        query_latencies = []
        document_latencies = []
        query_vectors = ()
        document_vectors = ()
        for _ in range(request.measured_runs):
            started = perf_counter()
            query_vectors = tuple(await adapter.embed_queries(questions))
            query_latencies.append((perf_counter() - started) * 1_000)
            started = perf_counter()
            document_vectors = tuple(await adapter.embed_documents(documents))
            document_latencies.append((perf_counter() - started) * 1_000)
        deviations = [
            abs(math.sqrt(sum(value * value for value in vector)) - 1.0)
            for vector in (*query_vectors, *document_vectors)
        ]
        truncated_queries, truncated_documents = _truncation_counts(
            root,
            questions,
            documents,
            adapter_identity=adapter_identity,
            parameters=parameters,
            max_length=target.max_length,
        )
        peak_cuda = _peak_cuda_memory(target.device)
        return QwenRuntimeTargetResult(
            target=target,
            status="MEASURED",
            artifact_sha256=manifest.artifact_sha256,
            measurements=QwenRuntimeMeasurements(
                query_count=len(questions),
                document_count=len(documents),
                initialization_latency_ms=initialization_latency_ms,
                cold_query_latency_ms=cold_query_latency_ms,
                query_mean_latency_ms=fmean(query_latencies),
                query_p95_latency_ms=_p95(query_latencies),
                document_mean_latency_ms=fmean(document_latencies),
                document_p95_latency_ms=_p95(document_latencies),
                query_items_per_second=(
                    len(questions) / (fmean(query_latencies) / 1_000)
                ),
                document_items_per_second=(
                    len(documents) / (fmean(document_latencies) / 1_000)
                ),
                maximum_norm_deviation=max(deviations),
                truncated_query_count=truncated_queries,
                truncated_document_count=truncated_documents,
                peak_process_rss_bytes=_peak_process_rss_bytes(),
                peak_cuda_memory_bytes=peak_cuda,
            ),
        )
    except Exception as error:
        return QwenRuntimeTargetResult(
            target=target,
            status="BLOCKED",
            blockers=(f"{type(error).__name__}:{error}",),
        )


def _formatted_query_document_texts(
    adapter_identity: tuple[str, str],
    parameters,
    questions: tuple[str, ...],
    documents: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Reproduce each adapter's own text formatting for an honest truncation count."""

    if adapter_identity == (BGE_M3_ADAPTER_ID, BGE_M3_ADAPTER_REVISION):
        return (
            tuple(
                f"{parameters.query_prefix} {question}" if parameters.query_prefix else question
                for question in questions
            ),
            tuple(
                f"{parameters.document_prefix} {document}"
                if parameters.document_prefix
                else document
                for document in documents
            ),
        )
    formatted_queries = tuple(
        f"Instruct: {parameters.query_instruction}\nQuery:{question}"
        for question in questions
    )
    return formatted_queries, documents


def _truncation_counts(
    root: Path,
    questions: tuple[str, ...],
    documents: tuple[str, ...],
    *,
    adapter_identity: tuple[str, str],
    parameters,
    max_length: int,
) -> tuple[int, int]:
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        str(root),
        local_files_only=True,
        trust_remote_code=False,
        use_fast=True,
    )
    formatted_queries, formatted_documents = _formatted_query_document_texts(
        adapter_identity, parameters, questions, documents
    )
    return (
        sum(
            len(tokenizer(text, truncation=False)["input_ids"]) > max_length
            for text in formatted_queries
        ),
        sum(
            len(tokenizer(text, truncation=False)["input_ids"]) > max_length
            for text in formatted_documents
        ),
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


def _runtime_environment() -> QwenRuntimeEnvironment:
    try:
        torch_version = metadata.version("torch")
    except metadata.PackageNotFoundError:
        torch_version = None
    try:
        import torch

        cuda_available = bool(torch.cuda.is_available())
        cuda_names = tuple(
            torch.cuda.get_device_name(index)
            for index in range(torch.cuda.device_count())
        )
    except (ImportError, OSError):
        cuda_available = False
        cuda_names = ()
    try:
        transformers_version = metadata.version("transformers")
    except metadata.PackageNotFoundError:
        transformers_version = None
    return QwenRuntimeEnvironment(
        operating_system=platform.platform(),
        processor=platform.processor() or platform.machine() or "unknown",
        python_version=sys.version.split()[0],
        torch_version=torch_version,
        transformers_version=transformers_version,
        cuda_available=cuda_available,
        cuda_device_names=cuda_names,
    )
