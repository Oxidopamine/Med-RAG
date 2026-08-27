from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets
from collections.abc import Sequence
from datetime import timezone
from pathlib import Path

from pydantic import ValidationError

from app.core.config import get_settings
from app.corpus.releases import SQLCorpusReleaseRepository
from app.corpus_steward.adapter_registry import default_adapter_registry
from app.corpus_steward.adjudication_repository import (
    SQLBenchmarkAdjudicationRepository,
)
from app.corpus_steward.adjudication_schemas import (
    ADJUDICATION_CONTRACT_VERSION,
    AdjudicationProcessPolicy,
    AdjudicationProcessPolicyContent,
    AdjudicationSealRequest,
    BenchmarkAccessPolicy,
    BenchmarkAccessPolicyContent,
    BenchmarkAdjudicationRecord,
    BenchmarkSuiteBuildRequest,
    BenchmarkThresholdPolicy,
    BenchmarkThresholdPolicyContent,
    ClinicalReviewDecision,
    ClinicalReviewDecisionContent,
    ClinicalReviewImport,
    DisagreementResolution,
    DisagreementResolutionContent,
    DisagreementResolutionImport,
)
from app.corpus_steward.adjudication_service import ClinicalAdjudicationService
from app.corpus_steward.benchmark import (
    RetrievalBenchmarkRunner,
    derive_development_suite_for_candidate,
)
from app.corpus_steward.benchmark_schemas import (
    BENCHMARK_CONTRACT_VERSION,
    BenchmarkAcceptanceAttestationContent,
    BenchmarkProvenanceMode,
    BenchmarkReport,
    BenchmarkSuite,
    BenchmarkSuiteContent,
    SignedBenchmarkAcceptance,
)
from app.corpus_steward.bm25_statistics import (
    BM25_STATISTICS_CONTRACT_VERSION,
    BM25ReleaseStatistics,
    derive_bm25_release_statistics,
)
from app.corpus_steward.candidate_schemas import (
    CANDIDATE_CONTRACT_VERSION,
    RetrievalCandidateContent,
    RetrievalCandidateManifest,
)
from app.corpus_steward.connectors import HTTPConnectorTransport
from app.corpus_steward.crypto import Ed25519Signer, generate_ed25519_key_pair
from app.corpus_steward.embedding_adapters import BM25AdapterParameters
from app.corpus_steward.evidence_extractor import DAKSourceExtractor
from app.corpus_steward.fhir_package import FHIRPackageLimits, FHIRPackageParser
from app.corpus_steward.index_repository import SQLIndexBasisRepository
from app.corpus_steward.index_schemas import (
    INDEX_CONTRACT_VERSION,
    DenseVectorDefinition,
    IndexAttestationContent,
    IndexVectorBatch,
    IndexVectorBatchContent,
    SignedIndexAttestation,
    SparseVectorDefinition,
)
from app.corpus_steward.ledger import SQLReconciliationLedger
from app.corpus_steward.materialization_repository import SQLMaterializationRepository
from app.corpus_steward.materialization_schemas import (
    MATERIALIZER_VERSION,
    MaterializationResult,
    MaterializationState,
)
from app.corpus_steward.materialization_service import MaterializationService
from app.corpus_steward.model_artifacts import (
    MODEL_ARTIFACT_CONTRACT_VERSION,
    ModelArtifactKind,
    ModelArtifactManifest,
    build_model_artifact_manifest,
    verify_model_artifact,
)
from app.corpus_steward.qa_repository import SQLQARepository
from app.corpus_steward.qa_schemas import ReleasePolicy
from app.corpus_steward.qa_service import QAService
from app.corpus_steward.qdrant_index import (
    QDRANT_PINNED_VERSION,
    QdrantIndexService,
    QdrantRESTClient,
)
from app.corpus_steward.qwen_runtime_matrix import (
    QWEN_RUNTIME_MATRIX_CONTRACT_VERSION,
    QwenRuntimeMatrixReport,
    QwenRuntimeMatrixRequest,
    execute_qwen_runtime_matrix,
)
from app.corpus_steward.registry import (
    SQLAttestationRepository,
    SQLTrustRootRegistry,
)
from app.corpus_steward.reranker_runtime_matrix import (
    RERANKER_RUNTIME_MATRIX_CONTRACT_VERSION,
    RerankerRuntimeMatrixReport,
    RerankerRuntimeMatrixRequest,
    execute_reranker_runtime_matrix,
)
from app.corpus_steward.reranking import Qwen3RerankerAdapter, RerankerBackend
from app.corpus_steward.schemas import (
    AttestationPurpose,
    BenchmarkAttestationPurpose,
    CoverageExceptionContent,
    JobState,
    TrustRootDefinition,
)
from app.corpus_steward.service import ReconciliationService
from app.corpus_steward.source_derived_benchmark import (
    SOURCE_DERIVED_CONTRACT_VERSION,
    AutomatedBenchmarkGenerationPolicy,
    AutomatedBenchmarkGenerationPolicyContent,
    AutomatedBenchmarkGenerationRecord,
    AutomatedBenchmarkGenerationRequest,
    SourceDerivedBenchmarkGenerator,
)
from app.corpus_steward.source_derived_repository import (
    SQLSourceDerivedBenchmarkRepository,
)
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.corpus_steward.structured_input_repository import SQLStructuredInputRepository
from app.corpus_steward.structured_input_schemas import StructuredInputState
from app.corpus_steward.structured_input_service import StructuredInputClosureService
from app.corpus_steward.structured_repository import SQLStructuredPackageRepository
from app.corpus_steward.structured_schemas import StructuredRunState
from app.corpus_steward.structured_service import StructuredPackageService
from app.corpus_steward.vector_producer import (
    DeterministicHashingBackend,
    EmbeddingBackend,
    EmbeddingExecutionPolicy,
    ProductionEmbeddingBackend,
    VectorBatchProducer,
    VectorProductionCheckpoint,
)
from app.persistence.database import Database
from app.schemas.corpus import (
    ACTIVATION_CONTRACT_VERSION,
    CORPUS_CONTRACT_VERSION,
    CorpusReleaseBundle,
    SignedActivationDecision,
    canonical_json_bytes,
    canonical_sha256,
)
from app.schemas.domain import utc_now


def corpus_schema_document() -> dict[str, object]:
    schema = CorpusReleaseBundle.model_json_schema()
    schema["$id"] = (
        "https://med-rag.local/contracts/"
        f"corpus-release-bundle-{CORPUS_CONTRACT_VERSION}.schema.json"
    )
    schema["x-contract-version"] = CORPUS_CONTRACT_VERSION
    return schema


def activation_schema_document() -> dict[str, object]:
    schema = SignedActivationDecision.model_json_schema()
    schema["$id"] = (
        "https://med-rag.local/contracts/"
        f"signed-activation-decision-{ACTIVATION_CONTRACT_VERSION}.schema.json"
    )
    schema["x-contract-version"] = ACTIVATION_CONTRACT_VERSION
    return schema


def materialization_schema_document() -> dict[str, object]:
    schema = MaterializationResult.model_json_schema()
    schema["$id"] = (
        "https://med-rag.local/contracts/"
        f"corpus-materialization-result-{MATERIALIZER_VERSION}.schema.json"
    )
    schema["x-contract-version"] = MATERIALIZER_VERSION
    return schema


def index_vector_schema_document() -> dict[str, object]:
    schema = IndexVectorBatch.model_json_schema()
    schema["$id"] = (
        "https://med-rag.local/contracts/"
        f"qdrant-vector-batch-{INDEX_CONTRACT_VERSION}.schema.json"
    )
    schema["x-contract-version"] = INDEX_CONTRACT_VERSION
    return schema


def index_attestation_schema_document() -> dict[str, object]:
    schema = SignedIndexAttestation.model_json_schema()
    schema["$id"] = (
        "https://med-rag.local/contracts/"
        f"qdrant-index-attestation-{INDEX_CONTRACT_VERSION}.schema.json"
    )
    schema["x-contract-version"] = INDEX_CONTRACT_VERSION
    return schema


def benchmark_suite_schema_document() -> dict[str, object]:
    schema = BenchmarkSuite.model_json_schema()
    schema["$id"] = (
        "https://med-rag.local/contracts/"
        f"retrieval-benchmark-suite-{BENCHMARK_CONTRACT_VERSION}.schema.json"
    )
    schema["x-contract-version"] = BENCHMARK_CONTRACT_VERSION
    return schema


def benchmark_report_schema_document() -> dict[str, object]:
    schema = BenchmarkReport.model_json_schema()
    schema["$id"] = (
        "https://med-rag.local/contracts/"
        f"retrieval-benchmark-report-{BENCHMARK_CONTRACT_VERSION}.schema.json"
    )
    schema["x-contract-version"] = BENCHMARK_CONTRACT_VERSION
    return schema


def candidate_schema_document() -> dict[str, object]:
    schema = RetrievalCandidateManifest.model_json_schema()
    schema["$id"] = (
        "https://med-rag.local/contracts/"
        f"retrieval-candidate-{CANDIDATE_CONTRACT_VERSION}.schema.json"
    )
    schema["x-contract-version"] = CANDIDATE_CONTRACT_VERSION
    return schema


def benchmark_acceptance_schema_document() -> dict[str, object]:
    schema = SignedBenchmarkAcceptance.model_json_schema()
    schema["$id"] = (
        "https://med-rag.local/contracts/"
        f"signed-benchmark-acceptance-{BENCHMARK_CONTRACT_VERSION}.schema.json"
    )
    schema["x-contract-version"] = BENCHMARK_CONTRACT_VERSION
    return schema


def bm25_statistics_schema_document() -> dict[str, object]:
    schema = BM25ReleaseStatistics.model_json_schema()
    schema["$id"] = (
        "https://med-rag.local/contracts/"
        f"bm25-release-statistics-{BM25_STATISTICS_CONTRACT_VERSION}.schema.json"
    )
    schema["x-contract-version"] = BM25_STATISTICS_CONTRACT_VERSION
    return schema


def qwen_runtime_matrix_schema_documents() -> dict[str, dict[str, object]]:
    models = {
        "qwen-runtime-matrix-request": QwenRuntimeMatrixRequest,
        "qwen-runtime-matrix-report": QwenRuntimeMatrixReport,
    }
    documents: dict[str, dict[str, object]] = {}
    for name, model in models.items():
        filename = f"{name}-{QWEN_RUNTIME_MATRIX_CONTRACT_VERSION}.schema.json"
        schema = model.model_json_schema()
        schema["$id"] = f"https://med-rag.local/contracts/{filename}"
        schema["x-contract-version"] = QWEN_RUNTIME_MATRIX_CONTRACT_VERSION
        documents[filename] = schema
    return documents


def export_qwen_runtime_matrix_schemas(directory: Path) -> int:
    for filename, document in qwen_runtime_matrix_schema_documents().items():
        export_schema(directory / filename, document)
    return 0


def reranker_runtime_matrix_schema_documents() -> dict[str, dict[str, object]]:
    """Contracts for the reranker matrix, versioned separately from the dense one.

    The two matrices measure different quantities -- pair throughput and score
    stability here, per-item embedding latency and unit-norm deviation there -- so they
    seal under their own contract version and can move independently.
    """

    models = {
        "reranker-runtime-matrix-request": RerankerRuntimeMatrixRequest,
        "reranker-runtime-matrix-report": RerankerRuntimeMatrixReport,
    }
    documents: dict[str, dict[str, object]] = {}
    for name, model in models.items():
        filename = f"{name}-{RERANKER_RUNTIME_MATRIX_CONTRACT_VERSION}.schema.json"
        schema = model.model_json_schema()
        schema["$id"] = f"https://med-rag.local/contracts/{filename}"
        schema["x-contract-version"] = RERANKER_RUNTIME_MATRIX_CONTRACT_VERSION
        documents[filename] = schema
    return documents


def export_reranker_runtime_matrix_schemas(directory: Path) -> int:
    for filename, document in reranker_runtime_matrix_schema_documents().items():
        export_schema(directory / filename, document)
    return 0


def benchmark_adjudication_schema_documents() -> dict[str, dict[str, object]]:
    models = {
        "benchmark-access-policy": BenchmarkAccessPolicy,
        "adjudication-process-policy": AdjudicationProcessPolicy,
        "benchmark-threshold-policy": BenchmarkThresholdPolicy,
        "clinical-review-decision": ClinicalReviewDecision,
        "disagreement-resolution": DisagreementResolution,
        "benchmark-adjudication-record": BenchmarkAdjudicationRecord,
        "benchmark-suite-build-request": BenchmarkSuiteBuildRequest,
    }
    documents: dict[str, dict[str, object]] = {}
    for name, model in models.items():
        filename = f"{name}-{ADJUDICATION_CONTRACT_VERSION}.schema.json"
        schema = model.model_json_schema()
        schema["$id"] = f"https://med-rag.local/contracts/{filename}"
        schema["x-contract-version"] = ADJUDICATION_CONTRACT_VERSION
        documents[filename] = schema
    return documents


def export_benchmark_adjudication_schemas(directory: Path) -> int:
    for filename, document in benchmark_adjudication_schema_documents().items():
        export_schema(directory / filename, document)
    return 0


def benchmark_source_derived_schema_documents() -> dict[str, dict[str, object]]:
    models = {
        "automated-benchmark-generation-policy": AutomatedBenchmarkGenerationPolicy,
        "automated-benchmark-generation-request": AutomatedBenchmarkGenerationRequest,
        "automated-benchmark-generation-record": AutomatedBenchmarkGenerationRecord,
    }
    documents: dict[str, dict[str, object]] = {}
    for name, model in models.items():
        filename = f"{name}-{SOURCE_DERIVED_CONTRACT_VERSION}.schema.json"
        schema = model.model_json_schema()
        schema["$id"] = f"https://med-rag.local/contracts/{filename}"
        schema["x-contract-version"] = SOURCE_DERIVED_CONTRACT_VERSION
        documents[filename] = schema
    return documents


def export_benchmark_source_derived_schemas(directory: Path) -> int:
    for filename, document in benchmark_source_derived_schema_documents().items():
        export_schema(directory / filename, document)
    return 0


def model_artifact_schema_document() -> dict[str, object]:
    schema = ModelArtifactManifest.model_json_schema()
    schema["$id"] = (
        "https://med-rag.local/contracts/"
        f"model-artifact-manifest-{MODEL_ARTIFACT_CONTRACT_VERSION}.schema.json"
    )
    schema["x-contract-version"] = MODEL_ARTIFACT_CONTRACT_VERSION
    return schema


def export_schema(path: Path, document: dict[str, object]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "contract_version": document.get(
                    "x-contract-version", CORPUS_CONTRACT_VERSION
                ),
                "output": str(path),
            },
            sort_keys=True,
        )
    )
    return 0


def create_model_artifact_manifest(arguments: argparse.Namespace) -> int:
    root = arguments.root.resolve(strict=True)
    output = arguments.output.absolute()
    if output == root or output.is_relative_to(root):
        raise ValueError("model artifact manifest output must be outside the artifact root")
    parameters: dict[str, object] = {}
    if arguments.adapter_parameters is not None:
        parsed = json.loads(arguments.adapter_parameters.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("adapter parameters must be a JSON object")
        parameters = parsed
    manifest = build_model_artifact_manifest(
        root,
        artifact_kind=ModelArtifactKind(arguments.kind),
        model_id=arguments.model_id,
        revision=arguments.revision,
        dimension=arguments.dimension,
        adapter_id=arguments.adapter_id,
        adapter_revision=arguments.adapter_revision,
        adapter_parameters=parameters,
    )
    _write_json_output(arguments.output, manifest)
    print(
        json.dumps(
            {
                "valid": True,
                "artifact_kind": manifest.content.artifact_kind.value,
                "model_id": manifest.content.model_id,
                "revision": manifest.content.revision,
                "dimension": manifest.content.dimension,
                "file_count": len(manifest.content.files),
                "total_bytes": sum(item.byte_size for item in manifest.content.files),
                "artifact_sha256": manifest.artifact_sha256,
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


def derive_bm25_statistics_command(arguments: argparse.Namespace) -> int:
    bundle = CorpusReleaseBundle.model_validate_json(
        arguments.bundle.read_text(encoding="utf-8")
    )
    parameters = BM25AdapterParameters.model_validate_json(
        arguments.adapter_parameters.read_text(encoding="utf-8")
    )
    statistics = derive_bm25_release_statistics(bundle, parameters)
    _write_json_output(arguments.output, statistics)
    print(
        json.dumps(
            {
                "valid": True,
                "corpus_release_id": statistics.content.corpus_release_id,
                "manifest_sha256": statistics.content.manifest_sha256,
                "document_count": statistics.content.document_count,
                "average_document_length": (
                    statistics.content.average_document_length
                ),
                "p95_document_length": statistics.content.p95_document_length,
                "statistics_sha256": statistics.statistics_sha256,
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


async def qwen_runtime_matrix_command(arguments: argparse.Namespace) -> int:
    request = QwenRuntimeMatrixRequest.model_validate_json(
        arguments.path.read_text(encoding="utf-8")
    )
    bundle = CorpusReleaseBundle.model_validate_json(
        arguments.bundle.read_text(encoding="utf-8")
    )
    suite = BenchmarkSuite.model_validate_json(
        arguments.suite.read_text(encoding="utf-8")
    )
    report = await execute_qwen_runtime_matrix(
        request,
        bundle,
        suite,
        workspace_root=arguments.workspace_root.resolve(strict=True),
    )
    _write_json_output(arguments.output, report)
    print(
        json.dumps(
            {
                "matrix_id": report.content.matrix_id,
                "outcome": report.content.outcome,
                "measured_target_count": sum(
                    item.status == "MEASURED" for item in report.content.results
                ),
                "blocked_target_count": sum(
                    item.status == "BLOCKED" for item in report.content.results
                ),
                "results": [
                    {
                        "target_id": item.target.target_id,
                        "status": item.status,
                        "blockers": list(item.blockers),
                    }
                    for item in report.content.results
                ],
                "report_sha256": report.report_sha256,
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0 if report.content.outcome == "COMPLETE" else 9


async def reranker_runtime_matrix_command(arguments: argparse.Namespace) -> int:
    request = RerankerRuntimeMatrixRequest.model_validate_json(
        arguments.path.read_text(encoding="utf-8")
    )
    bundle = CorpusReleaseBundle.model_validate_json(
        arguments.bundle.read_text(encoding="utf-8")
    )
    suite = BenchmarkSuite.model_validate_json(
        arguments.suite.read_text(encoding="utf-8")
    )
    report = await execute_reranker_runtime_matrix(
        request,
        bundle,
        suite,
        workspace_root=arguments.workspace_root.resolve(strict=True),
    )
    _write_json_output(arguments.output, report)
    print(
        json.dumps(
            {
                "matrix_id": report.content.matrix_id,
                "outcome": report.content.outcome,
                "measured_target_count": sum(
                    item.status == "MEASURED" for item in report.content.results
                ),
                "blocked_target_count": sum(
                    item.status == "BLOCKED" for item in report.content.results
                ),
                "results": [
                    {
                        "target_id": item.target.target_id,
                        "status": item.status,
                        # Three reranker lanes can share one matrix, and their scores are
                        # not on one scale, so the summary names the adapter that
                        # produced each row rather than leaving it to the report file.
                        "adapter_id": item.adapter_id,
                        "adapter_revision": item.adapter_revision,
                        "blockers": list(item.blockers),
                    }
                    for item in report.content.results
                ],
                "report_sha256": report.report_sha256,
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0 if report.content.outcome == "COMPLETE" else 9


def verify_model_artifact_command(arguments: argparse.Namespace) -> int:
    manifest = ModelArtifactManifest.model_validate_json(
        arguments.manifest.read_text(encoding="utf-8")
    )
    verified = verify_model_artifact(
        arguments.root,
        manifest,
        expected_artifact_sha256=arguments.expected_artifact_sha256,
    )
    print(
        json.dumps(
            {
                "valid": True,
                "artifact_root": str(verified.root),
                "artifact_kind": manifest.content.artifact_kind.value,
                "model_id": manifest.content.model_id,
                "revision": manifest.content.revision,
                "dimension": manifest.content.dimension,
                "file_count": len(manifest.content.files),
                "total_bytes": sum(item.byte_size for item in manifest.content.files),
                "artifact_sha256": manifest.artifact_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


def seal_index_vectors(arguments: argparse.Namespace) -> int:
    content = IndexVectorBatchContent.model_validate_json(
        arguments.path.read_text(encoding="utf-8")
    )
    batch = IndexVectorBatch.seal(content)
    _write_json_output(arguments.output, batch)
    print(
        json.dumps(
            {
                "valid": True,
                "corpus_release_id": content.corpus_release_id,
                "manifest_sha256": content.manifest_sha256,
                "record_count": len(content.records),
                "dense_dimension": content.dense.dimension,
                "sparse_dimension": content.sparse.dimension,
                "batch_sha256": batch.batch_sha256,
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


async def produce_index_vectors(arguments: argparse.Namespace) -> int:
    bundle = CorpusReleaseBundle.model_validate_json(
        arguments.bundle.read_text(encoding="utf-8")
    )
    if arguments.checkpoint_interval_batches <= 0:
        raise ValueError("checkpoint interval must be a positive batch count")
    backend = _build_embedding_backend(
        arguments,
        baseline_dense_dimension=arguments.dense_dimension,
        baseline_sparse_dimension=arguments.sparse_dimension,
        maximum_batch_size=arguments.batch_size,
    )
    checkpoint: VectorProductionCheckpoint | None = None
    if arguments.checkpoint is not None:
        if arguments.checkpoint.absolute() == arguments.output.absolute():
            raise ValueError("vector checkpoint and final output paths must differ")
        if arguments.checkpoint.exists():
            checkpoint = VectorProductionCheckpoint.model_validate_json(
                arguments.checkpoint.read_text(encoding="utf-8")
            )

    def record_progress(value: VectorProductionCheckpoint) -> None:
        assert arguments.checkpoint is not None
        record_count = len(value.content.records)
        checkpoint_interval = (
            arguments.batch_size * arguments.checkpoint_interval_batches
        )
        if (
            record_count != len(bundle.evidence)
            and record_count % checkpoint_interval != 0
        ):
            return
        _write_json_output_atomic(arguments.checkpoint, value)
        print(
            json.dumps(
                {
                    "event": "VECTOR_CHECKPOINT",
                    "record_count": record_count,
                    "checkpoint_sha256": value.checkpoint_sha256,
                    "checkpoint": str(arguments.checkpoint),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    batch = await VectorBatchProducer(
        backend, batch_size=arguments.batch_size
    ).produce(
        bundle,
        checkpoint=checkpoint,
        progress_callback=(
            record_progress if arguments.checkpoint is not None else None
        ),
        checkpoint_interval_batches=arguments.checkpoint_interval_batches,
    )
    _write_json_output(arguments.output, batch)
    print(
        json.dumps(
            {
                "valid": True,
                "backend_class": (
                    "VERIFIED_LOCAL_CANDIDATE"
                    if arguments.embedding_backend != "deterministic"
                    else "DETERMINISTIC_BASELINE"
                ),
                "corpus_release_id": batch.content.corpus_release_id,
                "manifest_sha256": batch.content.manifest_sha256,
                "record_count": len(batch.content.records),
                "dense_model": batch.content.dense.model.model_dump(mode="json"),
                "dense_dimension": batch.content.dense.dimension,
                "sparse_model": batch.content.sparse.model.model_dump(mode="json"),
                "sparse_dimension": batch.content.sparse.dimension,
                "batch_sha256": batch.batch_sha256,
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


def seal_benchmark_suite(arguments: argparse.Namespace) -> int:
    content = BenchmarkSuiteContent.model_validate_json(
        arguments.path.read_text(encoding="utf-8")
    )
    suite = BenchmarkSuite.seal(content)
    _write_json_output(arguments.output, suite)
    print(
        json.dumps(
            {
                "valid": True,
                "benchmark_id": content.benchmark_id,
                "corpus_release_id": content.corpus_release_id,
                "case_count": len(content.cases),
                "modes": [item.value for item in content.modes],
                "candidate_mode": content.candidate_mode.value,
                "suite_sha256": suite.suite_sha256,
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


def seal_clinical_review(arguments: argparse.Namespace) -> int:
    content = ClinicalReviewDecisionContent.model_validate_json(
        arguments.path.read_text(encoding="utf-8")
    )
    decision = ClinicalReviewDecision.seal(content)
    _write_json_output(arguments.output, decision)
    print(
        json.dumps(
            {
                "valid": True,
                "review_id": content.review_id,
                "case_id": content.case_id,
                "decision_sha256": decision.decision_sha256,
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


def seal_disagreement_resolution(arguments: argparse.Namespace) -> int:
    content = DisagreementResolutionContent.model_validate_json(
        arguments.path.read_text(encoding="utf-8")
    )
    resolution = DisagreementResolution.seal(content)
    _write_json_output(arguments.output, resolution)
    print(
        json.dumps(
            {
                "valid": True,
                "resolution_id": content.resolution_id,
                "case_id": content.case_id,
                "resolution_sha256": resolution.resolution_sha256,
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


def _clinical_adjudication_service(
    arguments: argparse.Namespace,
) -> tuple[Database, ClinicalAdjudicationService]:
    database = Database(arguments.database_url)
    artifacts = ImmutableStewardArtifactStore(arguments.artifact_store)
    repository = SQLBenchmarkAdjudicationRepository(database, artifacts)
    return database, ClinicalAdjudicationService(repository, artifacts)


async def seal_benchmark_access_policy(arguments: argparse.Namespace) -> int:
    content = BenchmarkAccessPolicyContent.model_validate_json(
        arguments.path.read_text(encoding="utf-8")
    )
    database, service = _clinical_adjudication_service(arguments)
    try:
        policy = await service.seal_access_policy(content)
    finally:
        await database.close()
    _write_json_output(arguments.output, policy)
    print(
        json.dumps(
            {
                "valid": True,
                "policy_id": content.policy_id,
                "revision": content.revision,
                "policy_sha256": policy.policy_sha256,
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


async def seal_adjudication_process_policy(arguments: argparse.Namespace) -> int:
    content = AdjudicationProcessPolicyContent.model_validate_json(
        arguments.path.read_text(encoding="utf-8")
    )
    database, service = _clinical_adjudication_service(arguments)
    try:
        policy = await service.seal_adjudication_process(content)
    finally:
        await database.close()
    _write_json_output(arguments.output, policy)
    print(
        json.dumps(
            {
                "valid": True,
                "policy_id": content.policy_id,
                "revision": content.revision,
                "policy_sha256": policy.policy_sha256,
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


async def seal_benchmark_threshold_policy(arguments: argparse.Namespace) -> int:
    content = BenchmarkThresholdPolicyContent.model_validate_json(
        arguments.path.read_text(encoding="utf-8")
    )
    database, service = _clinical_adjudication_service(arguments)
    try:
        policy = await service.seal_threshold_policy(content)
    finally:
        await database.close()
    _write_json_output(arguments.output, policy)
    print(
        json.dumps(
            {
                "valid": True,
                "policy_id": content.policy_id,
                "revision": content.revision,
                "policy_sha256": policy.policy_sha256,
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


async def import_clinical_reviews(arguments: argparse.Namespace) -> int:
    imported = ClinicalReviewImport.model_validate_json(
        arguments.path.read_text(encoding="utf-8")
    )
    database, service = _clinical_adjudication_service(arguments)
    try:
        await service.import_review_decisions(
            imported,
            access_policy_sha256=arguments.access_policy_sha256,
            adjudication_process_sha256=arguments.adjudication_process_sha256,
        )
    finally:
        await database.close()
    print(
        json.dumps(
            {
                "valid": True,
                "review_count": len(imported.decisions),
                "case_count": len({item.content.case_id for item in imported.decisions}),
            },
            sort_keys=True,
        )
    )
    return 0


async def import_disagreement_resolutions(arguments: argparse.Namespace) -> int:
    imported = DisagreementResolutionImport.model_validate_json(
        arguments.path.read_text(encoding="utf-8")
    )
    database, service = _clinical_adjudication_service(arguments)
    try:
        await service.import_disagreement_resolutions(
            imported,
            access_policy_sha256=arguments.access_policy_sha256,
            adjudication_process_sha256=arguments.adjudication_process_sha256,
        )
    finally:
        await database.close()
    print(
        json.dumps(
            {
                "valid": True,
                "resolution_count": len(imported.resolutions),
            },
            sort_keys=True,
        )
    )
    return 0


async def seal_clinical_adjudication(arguments: argparse.Namespace) -> int:
    request = AdjudicationSealRequest.model_validate_json(
        arguments.path.read_text(encoding="utf-8")
    )
    database, service = _clinical_adjudication_service(arguments)
    try:
        record = await service.seal_adjudication(request)
    finally:
        await database.close()
    _write_json_output(arguments.output, record)
    print(
        json.dumps(
            {
                "valid": True,
                "adjudication_record_id": record.content.adjudication_record_id,
                "adjudication_record_sha256": record.adjudication_record_sha256,
                "case_count": len(record.content.cases),
                "development_case_count": sum(
                    item.suite_partition.value == "DEVELOPMENT"
                    for item in record.content.cases
                ),
                "sealed_holdout_case_count": sum(
                    item.suite_partition.value == "SEALED_HOLDOUT"
                    for item in record.content.cases
                ),
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


async def build_clinical_benchmark_suite(arguments: argparse.Namespace) -> int:
    request = BenchmarkSuiteBuildRequest.model_validate_json(
        arguments.path.read_text(encoding="utf-8")
    )
    bundle = CorpusReleaseBundle.model_validate_json(
        arguments.bundle.read_text(encoding="utf-8")
    )
    database, service = _clinical_adjudication_service(arguments)
    try:
        result = await service.build_suite(request, bundle)
    finally:
        await database.close()
    _write_json_output(arguments.output, result.suite)
    print(
        json.dumps(
            {
                "valid": True,
                "benchmark_id": result.suite.content.benchmark_id,
                "suite_partition": result.suite.content.suite_partition.value,
                "suite_sha256": result.suite.suite_sha256,
                "case_count": len(result.suite.content.cases),
                "artifact_sha256": result.artifact_sha256,
                "storage_key": result.storage_key,
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


def generate_benchmark_partition_seed(arguments: argparse.Namespace) -> int:
    if arguments.output.exists():
        raise ValueError("refusing to overwrite an existing benchmark partition seed")
    seed = secrets.token_bytes(32)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(seed)
    print(
        json.dumps(
            {
                "partition_seed_sha256": hashlib.sha256(seed).hexdigest(),
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


def seal_benchmark_generation_policy(arguments: argparse.Namespace) -> int:
    content = AutomatedBenchmarkGenerationPolicyContent.model_validate_json(
        arguments.path.read_text(encoding="utf-8")
    )
    policy = AutomatedBenchmarkGenerationPolicy.seal(content)
    _write_json_output(arguments.output, policy)
    print(
        json.dumps(
            {
                "valid": True,
                "policy_id": content.policy_id,
                "revision": content.revision,
                "policy_sha256": policy.policy_sha256,
                "partition_seed_sha256": content.partition_seed_sha256,
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


async def generate_source_derived_benchmarks(arguments: argparse.Namespace) -> int:
    bundle = CorpusReleaseBundle.model_validate_json(
        arguments.bundle.read_text(encoding="utf-8")
    )
    access = BenchmarkAccessPolicy.model_validate_json(
        arguments.access_policy.read_text(encoding="utf-8")
    )
    threshold = BenchmarkThresholdPolicy.model_validate_json(
        arguments.threshold_policy.read_text(encoding="utf-8")
    )
    generation_policy = AutomatedBenchmarkGenerationPolicy.model_validate_json(
        arguments.generation_policy.read_text(encoding="utf-8")
    )
    request = AutomatedBenchmarkGenerationRequest.model_validate_json(
        arguments.request.read_text(encoding="utf-8")
    )
    outputs = tuple(
        path.absolute()
        for path in (
            arguments.development_output,
            arguments.sealed_holdout_output,
            arguments.generation_record_output,
        )
        if path is not None
    )
    if len(outputs) != len(set(outputs)):
        raise ValueError("automated benchmark output paths must be distinct")

    database, policy_service = _clinical_adjudication_service(arguments)
    artifacts = ImmutableStewardArtifactStore(arguments.artifact_store)
    repository = SQLSourceDerivedBenchmarkRepository(database, artifacts)
    try:
        registered_access = await policy_service.seal_access_policy(access.content)
        registered_threshold = await policy_service.seal_threshold_policy(threshold.content)
        result = SourceDerivedBenchmarkGenerator().generate(
            bundle,
            access_policy=registered_access,
            threshold_policy=registered_threshold,
            generation_policy=generation_policy,
            request=request,
            partition_seed=arguments.partition_seed.read_bytes(),
        )
        await repository.register_generation(result, policy=generation_policy)
    finally:
        await database.close()

    _write_json_output(arguments.development_output, result.development_suite)
    _write_json_output(arguments.sealed_holdout_output, result.sealed_holdout_suite)
    _write_json_output(arguments.generation_record_output, result.generation_record)
    print(
        json.dumps(
            {
                "valid": True,
                "provenance_mode": "AUTOMATED_SOURCE_DERIVED",
                "generation_id": result.generation_record.content.generation_id,
                "generation_policy_sha256": generation_policy.policy_sha256,
                "generation_record_sha256": (
                    result.generation_record.generation_record_sha256
                ),
                "development_suite_sha256": result.development_suite.suite_sha256,
                "development_case_count": len(result.development_suite.content.cases),
                "sealed_holdout_suite_sha256": (
                    result.sealed_holdout_suite.suite_sha256
                ),
                "sealed_holdout_case_count": len(
                    result.sealed_holdout_suite.content.cases
                ),
                "partition_assignment_sha256": (
                    result.generation_record.content.partition_assignment_sha256
                ),
                "development_output": str(arguments.development_output),
                "sealed_holdout_exported": arguments.sealed_holdout_output is not None,
                "generation_record_exported": (
                    arguments.generation_record_output is not None
                ),
            },
            sort_keys=True,
        )
    )
    return 0


def seal_retrieval_candidate(arguments: argparse.Namespace) -> int:
    content = RetrievalCandidateContent.model_validate_json(
        arguments.path.read_text(encoding="utf-8")
    )
    candidate = RetrievalCandidateManifest.seal(content)
    _write_json_output(arguments.output, candidate)
    print(
        json.dumps(
            {
                "valid": True,
                "candidate_id": content.candidate_id,
                "lane_count": len(content.lanes),
                "candidate_sha256": candidate.candidate_sha256,
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


async def derive_benchmark_suite_for_candidate(arguments: argparse.Namespace) -> int:
    suite = BenchmarkSuite.model_validate_json(
        arguments.suite.read_text(encoding="utf-8")
    )
    candidate = RetrievalCandidateManifest.model_validate_json(
        arguments.candidate.read_text(encoding="utf-8")
    )
    derived = derive_development_suite_for_candidate(suite, candidate)
    if arguments.register:
        if not arguments.actor_identity:
            raise ValueError("--actor-identity is required with --register")
        database = Database(arguments.database_url)
        try:
            await SQLSourceDerivedBenchmarkRepository(
                database,
                ImmutableStewardArtifactStore(arguments.artifact_store),
            ).register_development_derivation(
                suite,
                derived,
                candidate,
                actor_identity=arguments.actor_identity,
            )
        finally:
            await database.close()
    _write_json_output(arguments.output, derived)
    print(
        json.dumps(
            {
                "valid": True,
                "benchmark_id": derived.content.benchmark_id,
                "candidate_id": candidate.content.candidate_id,
                "candidate_limit": candidate.content.candidate_limit,
                "top_k": derived.content.top_k,
                "suite_sha256": derived.suite_sha256,
                "registered": arguments.register,
                "output": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


def validate_bundle(path: Path, *, require_activatable: bool = False) -> int:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        bundle = CorpusReleaseBundle.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        print(
            json.dumps(
                {
                    "valid": False,
                    "path": str(path),
                    "error": str(error),
                },
                sort_keys=True,
            )
        )
        return 2

    blockers = bundle.activation_blockers()
    report = {
        "valid": True,
        "activatable_contract": not blockers,
        "path": str(path),
        "corpus_release_id": bundle.manifest.content.corpus_release_id,
        "contract_version": bundle.manifest.content.schema_version,
        "manifest_sha256": bundle.manifest.manifest_sha256,
        "evidence_count": len(bundle.evidence),
        "inventory_count": len(bundle.manifest.content.inventory_snapshots),
        "exception_count": len(bundle.manifest.content.exceptions),
        "activation_blockers": blockers,
    }
    print(json.dumps(report, sort_keys=True))
    return 3 if require_activatable and blockers else 0


def _signer(arguments: argparse.Namespace) -> Ed25519Signer:
    if not arguments.signing_key or not arguments.signing_key_id or not arguments.signer_identity:
        raise ValueError("a signing key path, signing key ID, and signer identity are required")
    return Ed25519Signer.from_pem(
        arguments.signing_key,
        key_id=arguments.signing_key_id,
        signer_identity=arguments.signer_identity,
    )


async def register_trust_root(arguments: argparse.Namespace) -> int:
    database = Database(arguments.database_url)
    try:
        definition = TrustRootDefinition.model_validate_json(
            arguments.path.read_text(encoding="utf-8")
        )
        registered = await SQLTrustRootRegistry(database).register(
            definition, replace=arguments.replace
        )
        print(
            json.dumps(
                {
                    "trust_root_id": registered.trust_root_id,
                    "trust_root_sha256": registered.sha256,
                    "connector": (f"{registered.connector_name}@{registered.connector_version}"),
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        await database.close()


async def register_key(arguments: argparse.Namespace) -> int:
    database = Database(arguments.database_url)
    try:
        purposes = tuple(
            BenchmarkAttestationPurpose(item)
            if item == BenchmarkAttestationPurpose.BENCHMARK_ACCEPTANCE.value
            else AttestationPurpose(item)
            for item in arguments.purpose
        )
        await SQLAttestationRepository(database).register_key(
            key_id=arguments.key_id,
            signer_identity=arguments.signer_identity,
            public_key_pem=arguments.public_key.read_text(encoding="ascii"),
            purposes=purposes,
        )
        print(
            json.dumps(
                {
                    "key_id": arguments.key_id,
                    "signer_identity": arguments.signer_identity,
                    "purposes": sorted(arguments.purpose),
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        await database.close()


def keygen(arguments: argparse.Namespace) -> int:
    if arguments.private_key.exists() or arguments.public_key.exists():
        raise ValueError("refusing to overwrite an existing key file")
    private_pem, public_pem = generate_ed25519_key_pair()
    arguments.private_key.parent.mkdir(parents=True, exist_ok=True)
    arguments.public_key.parent.mkdir(parents=True, exist_ok=True)
    arguments.private_key.write_bytes(private_pem)
    arguments.public_key.write_bytes(public_pem)
    print(
        json.dumps(
            {
                "private_key": str(arguments.private_key),
                "public_key": str(arguments.public_key),
            },
            sort_keys=True,
        )
    )
    return 0


def _reconciliation_service(
    database: Database, arguments: argparse.Namespace
) -> ReconciliationService:
    return ReconciliationService(
        trust_roots=SQLTrustRootRegistry(database),
        ledger=SQLReconciliationLedger(database),
        attestations=SQLAttestationRepository(database),
        artifacts=ImmutableStewardArtifactStore(arguments.artifact_store),
        transport=HTTPConnectorTransport(
            max_bytes=arguments.max_artifact_bytes,
            timeout_seconds=arguments.request_timeout,
            allow_private_networks=arguments.allow_private_networks,
        ),
        signer=_signer(arguments),
    )


async def reconcile(arguments: argparse.Namespace) -> int:
    database = Database(arguments.database_url)
    try:
        idempotency_key = arguments.idempotency_key or (
            f"manual:{arguments.trust_root}:{utc_now().astimezone(timezone.utc).date().isoformat()}"
        )
        report = await _reconciliation_service(database, arguments).reconcile(
            arguments.trust_root, idempotency_key=idempotency_key
        )
        payload = report.model_dump(mode="json")
        payload["idempotency_key"] = idempotency_key
        rendered = json.dumps(payload, indent=2 if arguments.output else None, sort_keys=True)
        if arguments.output:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 4 if report.state is JobState.BLOCKED else 0
    finally:
        await database.close()


async def approve_exception(arguments: argparse.Namespace) -> int:
    database = Database(arguments.database_url)
    try:
        content = CoverageExceptionContent.model_validate_json(
            arguments.path.read_text(encoding="utf-8")
        )
        approved = await _reconciliation_service(database, arguments).approve_exception(content)
        print(json.dumps(approved.model_dump(mode="json"), sort_keys=True))
        return 0
    finally:
        await database.close()


async def process_structured(arguments: argparse.Namespace) -> int:
    database = Database(arguments.database_url)
    try:
        artifact_store = ImmutableStewardArtifactStore(arguments.artifact_store)
        result = await StructuredPackageService(
            trust_roots=SQLTrustRootRegistry(database),
            repository=SQLStructuredPackageRepository(database),
            input_repository=SQLStructuredInputRepository(database),
            ledger=SQLReconciliationLedger(database),
            attestations=SQLAttestationRepository(database),
            artifacts=artifact_store,
            parser=FHIRPackageParser(
                FHIRPackageLimits(
                    max_entries=arguments.max_entries,
                    max_total_uncompressed_bytes=arguments.max_total_uncompressed_bytes,
                    max_member_bytes=arguments.max_member_bytes,
                    max_compression_ratio=arguments.max_compression_ratio,
                    max_json_depth=arguments.max_json_depth,
                )
            ),
            signer=_signer(arguments),
        ).process(arguments.candidate_id, item_id=arguments.item_id)
        rendered = json.dumps(
            result.model_dump(mode="json"),
            indent=2 if arguments.output else None,
            sort_keys=True,
        )
        if arguments.output:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 5 if result.state is StructuredRunState.BLOCKED else 0
    finally:
        await database.close()


async def resolve_structured_inputs(arguments: argparse.Namespace) -> int:
    database = Database(arguments.database_url)
    try:
        artifacts = ImmutableStewardArtifactStore(arguments.artifact_store)
        result = await StructuredInputClosureService(
            trust_roots=SQLTrustRootRegistry(database),
            source_repository=SQLStructuredPackageRepository(database),
            input_repository=SQLStructuredInputRepository(database),
            ledger=SQLReconciliationLedger(database),
            attestations=SQLAttestationRepository(database),
            artifacts=artifacts,
            parser=FHIRPackageParser(
                FHIRPackageLimits(
                    max_entries=arguments.max_entries,
                    max_total_uncompressed_bytes=arguments.max_total_uncompressed_bytes,
                    max_member_bytes=arguments.max_member_bytes,
                    max_compression_ratio=arguments.max_compression_ratio,
                    max_json_depth=arguments.max_json_depth,
                )
            ),
            transport=HTTPConnectorTransport(
                max_bytes=arguments.max_artifact_bytes,
                timeout_seconds=arguments.request_timeout,
                allow_private_networks=arguments.allow_private_networks,
            ),
            signer=_signer(arguments),
        ).resolve(arguments.candidate_id, item_id=arguments.item_id)
        rendered = json.dumps(
            result.model_dump(mode="json"),
            indent=2 if arguments.output else None,
            sort_keys=True,
        )
        if arguments.output:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 6 if result.state is StructuredInputState.BLOCKED else 0
    finally:
        await database.close()


async def materialize(arguments: argparse.Namespace) -> int:
    database = Database(arguments.database_url)
    try:
        artifacts = ImmutableStewardArtifactStore(arguments.artifact_store)
        result = await MaterializationService(
            trust_roots=SQLTrustRootRegistry(database),
            source_repository=SQLStructuredPackageRepository(database),
            input_repository=SQLStructuredInputRepository(database),
            repository=SQLMaterializationRepository(database),
            ledger=SQLReconciliationLedger(database),
            attestations=SQLAttestationRepository(database),
            artifacts=artifacts,
            extractor=DAKSourceExtractor(),
            signer=_signer(arguments),
        ).materialize(arguments.candidate_id, item_id=arguments.item_id)
        if arguments.output:
            rendered = json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True)
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(rendered + "\n", encoding="utf-8")
        candidate = result.corpus_release_candidate
        print(
            json.dumps(
                {
                    "state": result.state.value,
                    "reconciliation_candidate_id": arguments.candidate_id,
                    "materialization_run_id": (result.report.content.materialization_run_id),
                    "corpus_release_candidate_id": (
                        candidate.content.corpus_release_candidate_id
                        if candidate is not None
                        else None
                    ),
                    "candidate_sha256": (
                        candidate.candidate_sha256 if candidate is not None else None
                    ),
                    "report_sha256": result.report.report_sha256,
                    "evidence_count": len(result.report.content.evidence),
                    "coverage_complete": result.report.content.coverage.complete,
                    "blockers": list(result.report.content.blockers),
                    "output": str(arguments.output) if arguments.output else None,
                },
                sort_keys=True,
            )
        )
        return 7 if result.state is MaterializationState.BLOCKED else 0
    finally:
        await database.close()


async def qa(arguments: argparse.Namespace) -> int:
    database = Database(arguments.database_url)
    try:
        policy = (
            ReleasePolicy.model_validate_json(arguments.release_policy.read_text(encoding="utf-8"))
            if arguments.release_policy
            else ReleasePolicy()
        )
        artifacts = ImmutableStewardArtifactStore(arguments.artifact_store)
        result = await QAService(
            repository=SQLQARepository(database),
            releases=SQLCorpusReleaseRepository(database),
            trust_roots=SQLTrustRootRegistry(database),
            attestations=SQLAttestationRepository(database),
            ledger=SQLReconciliationLedger(database),
            artifacts=artifacts,
            extractor=DAKSourceExtractor(),
            signer=_signer(arguments),
        ).qa(
            arguments.candidate_id,
            release_policy=policy,
            previous_release_id=arguments.previous_release_id,
        )
        if result.corpus_release_bundle is not None and arguments.bundle_output:
            arguments.bundle_output.parent.mkdir(parents=True, exist_ok=True)
            arguments.bundle_output.write_text(
                json.dumps(
                    result.corpus_release_bundle.model_dump(mode="json"),
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        bundle = result.corpus_release_bundle
        print(
            json.dumps(
                {
                    "state": result.state.value,
                    "qa_run_id": result.qa_run_id,
                    "corpus_release_candidate_id": result.corpus_release_candidate_id,
                    "evidence_artifact_manifest_sha256": (result.evidence_artifact_manifest_sha256),
                    "evidence_manifest_artifact_sha256": (result.evidence_manifest_artifact_sha256),
                    "evidence_count": result.evidence_count,
                    "replay_passed_count": result.replay_passed_count,
                    "replay_failed_count": result.replay_failed_count,
                    "approved_count": result.approved_count,
                    "quarantined_count": result.quarantined_count,
                    "decision_batch_sha256": result.decision_batch_sha256,
                    "corpus_release_id": (
                        bundle.manifest.content.corpus_release_id if bundle else None
                    ),
                    "manifest_sha256": bundle.manifest.manifest_sha256 if bundle else None,
                    "bundle_sha256": canonical_sha256(bundle) if bundle else None,
                    "bundle_artifact_sha256": result.bundle_artifact_sha256,
                    "index_status": "NOT_BUILT" if bundle else None,
                    "bundle_output": (
                        str(arguments.bundle_output)
                        if bundle is not None and arguments.bundle_output
                        else None
                    ),
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        await database.close()


def _load_index_inputs(
    arguments: argparse.Namespace,
) -> tuple[CorpusReleaseBundle, IndexVectorBatch]:
    bundle = CorpusReleaseBundle.model_validate_json(
        arguments.bundle.read_text(encoding="utf-8")
    )
    vectors = IndexVectorBatch.model_validate_json(
        arguments.vectors.read_text(encoding="utf-8")
    )
    return bundle, vectors


def _failure_class(error: BaseException) -> str:
    """Record why a run failed, not merely what type refused it.

    The stored class name alone cannot distinguish an exhausted encode timeout from a
    wrong-dimension adapter response, so a failed run is undiagnosable once its console
    output is gone. The message is bounded to one line and the column truncates it.
    """

    name = type(error).__name__
    detail = " ".join(str(error).split())
    return f"{name}: {detail}" if detail else name


def _write_json_output(path: Path | None, value: object) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_json_output_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _index_summary(*, basis, report, output: Path | None) -> dict[str, object]:
    return {
        "valid": True,
        "corpus_release_id": basis.release.corpus_release_id,
        "release_state": basis.release.state.value,
        "manifest_sha256": basis.release.manifest_sha256,
        "bundle_sha256": basis.bundle_sha256,
        "qa_run_id": basis.qa_run_id,
        "materialized_count": basis.materialized_count,
        "approved_count": basis.approved_count,
        "quarantined_count": basis.quarantined_count,
        "qdrant_collection": report.content.qdrant_collection,
        "qdrant_version": report.content.qdrant_version,
        "point_count": report.content.point_count,
        "dense_dimension": report.content.dense.dimension,
        "sparse_dimension": report.content.sparse.dimension,
        "validation_report_sha256": report.report_sha256,
        "output": str(output) if output else None,
    }


async def qdrant_build(arguments: argparse.Namespace) -> int:
    database = Database(arguments.database_url)
    qdrant = QdrantRESTClient(
        arguments.qdrant_url,
        api_key=arguments.qdrant_api_key,
        timeout_seconds=arguments.qdrant_timeout,
    )
    try:
        bundle, vectors = _load_index_inputs(arguments)
        candidate = RetrievalCandidateManifest.model_validate_json(
            arguments.candidate.read_text(encoding="utf-8")
        )
        repository = SQLIndexBasisRepository(database)
        basis = await repository.load(bundle.manifest.content.corpus_release_id)
        repository.require_bundle_match(basis, bundle)
        if basis.release.state.value != "VALIDATED":
            raise ValueError("Qdrant build requires a VALIDATED corpus release")
        report = await QdrantIndexService(
            qdrant,
            expected_qdrant_version=arguments.expected_qdrant_version,
            upsert_batch_size=arguments.batch_size,
        ).build(
            basis.bundle,
            vectors,
            candidate,
            source_classes=basis.source_classes,
            smoke_samples=arguments.smoke_samples,
            smoke_limit=arguments.smoke_limit,
        )
        _write_json_output(arguments.output, report)
        print(
            json.dumps(
                _index_summary(basis=basis, report=report, output=arguments.output),
                sort_keys=True,
            )
        )
        return 0
    finally:
        await qdrant.close()
        await database.close()


async def qdrant_validate(arguments: argparse.Namespace) -> int:
    database = Database(arguments.database_url)
    qdrant = QdrantRESTClient(
        arguments.qdrant_url,
        api_key=arguments.qdrant_api_key,
        timeout_seconds=arguments.qdrant_timeout,
    )
    try:
        bundle, vectors = _load_index_inputs(arguments)
        candidate = RetrievalCandidateManifest.model_validate_json(
            arguments.candidate.read_text(encoding="utf-8")
        )
        repository = SQLIndexBasisRepository(database)
        basis = await repository.load(bundle.manifest.content.corpus_release_id)
        repository.require_bundle_match(basis, bundle)
        report = await QdrantIndexService(
            qdrant,
            expected_qdrant_version=arguments.expected_qdrant_version,
        ).validate(
            basis.bundle,
            vectors,
            candidate,
            source_classes=basis.source_classes,
            smoke_samples=arguments.smoke_samples,
            smoke_limit=arguments.smoke_limit,
        )
        _write_json_output(arguments.output, report)
        print(
            json.dumps(
                _index_summary(basis=basis, report=report, output=arguments.output),
                sort_keys=True,
            )
        )
        return 0
    finally:
        await qdrant.close()
        await database.close()


async def qdrant_attest(arguments: argparse.Namespace) -> int:
    database = Database(arguments.database_url)
    qdrant = QdrantRESTClient(
        arguments.qdrant_url,
        api_key=arguments.qdrant_api_key,
        timeout_seconds=arguments.qdrant_timeout,
    )
    try:
        bundle, vectors = _load_index_inputs(arguments)
        candidate = RetrievalCandidateManifest.model_validate_json(
            arguments.candidate.read_text(encoding="utf-8")
        )
        basis_repository = SQLIndexBasisRepository(database)
        basis = await basis_repository.load(bundle.manifest.content.corpus_release_id)
        basis_repository.require_bundle_match(basis, bundle)
        if basis.release.state.value != "VALIDATED":
            raise ValueError("index attestation requires a VALIDATED corpus release")
        if basis.release.index_status != "NOT_BUILT":
            raise ValueError("release index is already registered as validated")
        report = await QdrantIndexService(
            qdrant,
            expected_qdrant_version=arguments.expected_qdrant_version,
        ).validate(
            basis.bundle,
            vectors,
            candidate,
            source_classes=basis.source_classes,
            smoke_samples=arguments.smoke_samples,
            smoke_limit=arguments.smoke_limit,
        )
        content = IndexAttestationContent(
            corpus_release_id=basis.release.corpus_release_id,
            manifest_sha256=basis.release.manifest_sha256,
            qdrant_collection=report.content.qdrant_collection,
            validation_report_sha256=report.report_sha256,
            qa_run_id=basis.qa_run_id,
            materialized_count=basis.materialized_count,
            approved_count=basis.approved_count,
            quarantined_count=basis.quarantined_count,
            point_count=report.content.point_count,
            attested_at=report.content.validated_at,
        )
        signer = _signer(arguments)
        reference = await SQLAttestationRepository(database).record_and_verify(
            content,
            signer.sign(canonical_json_bytes(content)),
            purpose=AttestationPurpose.STAGE,
            predicate_type="https://med-rag.local/attestations/qdrant-index-validation",
        )
        signed = SignedIndexAttestation(
            content=content,
            statement_sha256=canonical_sha256(content),
            validation_report=report,
            attestation=reference,
        )
        _write_json_output(arguments.output, signed)
        release = await SQLCorpusReleaseRepository(database).mark_index_validated(
            basis.release.corpus_release_id,
            point_count=report.content.point_count,
            index_attestation_sha256=signed.statement_sha256,
            qdrant_collection=report.content.qdrant_collection,
        )
        summary = _index_summary(basis=basis, report=report, output=arguments.output)
        summary.update(
            {
                "index_status": release.index_status,
                "index_attestation_sha256": release.index_attestation_sha256,
                "attestation_id": reference.attestation_id,
                "signing_key_id": reference.signing_key_id,
                "signer_identity": reference.signer_identity,
            }
        )
        print(json.dumps(summary, sort_keys=True))
        return 0
    finally:
        await qdrant.close()
        await database.close()


async def benchmark_run(arguments: argparse.Namespace) -> int:
    qdrant = QdrantRESTClient(
        arguments.qdrant_url,
        api_key=arguments.qdrant_api_key,
        timeout_seconds=arguments.qdrant_timeout,
    )
    database: Database | None = None
    execution_repository: SQLSourceDerivedBenchmarkRepository | None = None
    run_id: str | None = None
    try:
        bundle, vectors = _load_index_inputs(arguments)
        candidate_manifest = RetrievalCandidateManifest.model_validate_json(
            arguments.candidate.read_text(encoding="utf-8")
        )
        backend = _build_embedding_backend(
            arguments,
            baseline_dense_dimension=vectors.content.dense.dimension,
            baseline_sparse_dimension=vectors.content.sparse.dimension,
            maximum_batch_size=1,
            expected_dense=vectors.content.dense,
            expected_sparse=vectors.content.sparse,
        )
        reranker = _build_reranker_backend(arguments, candidate_manifest)
        if arguments.registered_suite_sha256 is not None:
            if not arguments.actor_identity:
                raise ValueError(
                    "--actor-identity is required for a registered automated suite"
                )
            database = Database(arguments.database_url)
            execution_repository = SQLSourceDerivedBenchmarkRepository(
                database,
                ImmutableStewardArtifactStore(arguments.artifact_store),
            )
            suite, run_id = await execution_repository.claim_registered_execution(
                arguments.registered_suite_sha256,
                candidate_configuration_sha256=candidate_manifest.candidate_sha256,
                vector_batch_sha256=vectors.batch_sha256,
                actor_identity=arguments.actor_identity,
            )
        else:
            suite = BenchmarkSuite.model_validate_json(
                arguments.suite.read_text(encoding="utf-8")
            )
        if suite.content.provenance_mode is BenchmarkProvenanceMode.AUTOMATED_SOURCE_DERIVED:
            if not arguments.actor_identity:
                raise ValueError(
                    "--actor-identity is required for an automated source-derived suite"
                )
            if execution_repository is None:
                database = Database(arguments.database_url)
                execution_repository = SQLSourceDerivedBenchmarkRepository(
                    database,
                    ImmutableStewardArtifactStore(arguments.artifact_store),
                )
            if run_id is None:
                run_id = await execution_repository.claim_execution(
                    suite,
                    candidate_configuration_sha256=candidate_manifest.candidate_sha256,
                    vector_batch_sha256=vectors.batch_sha256,
                    actor_identity=arguments.actor_identity,
                )
        trace_case_ids = frozenset(arguments.development_trace_case_id or ())
        runner = RetrievalBenchmarkRunner(
            qdrant,
            backend,
            reranker=reranker,
            development_trace_case_ids=trace_case_ids,
            development_trace_depth=arguments.development_trace_depth,
        )
        try:
            report = await runner.run(
                bundle, vectors, suite, candidate_manifest
            )
        except Exception as error:
            if execution_repository is not None and run_id is not None:
                await execution_repository.fail_execution(
                    run_id,
                    failure_class=_failure_class(error),
                )
            raise
        if execution_repository is not None and run_id is not None:
            await execution_repository.complete_execution(run_id, report)
        _write_json_output(arguments.output, report)
        if arguments.development_trace_output is not None:
            if not trace_case_ids:
                raise ValueError(
                    "--development-trace-output requires --development-trace-case-id"
                )
            _write_json_output(
                arguments.development_trace_output,
                {
                    "schema_version": "1.0.0",
                    "development_only": True,
                    "benchmark_suite_sha256": suite.suite_sha256,
                    "candidate_configuration_sha256": candidate_manifest.candidate_sha256,
                    "vector_batch_sha256": vectors.batch_sha256,
                    "qdrant_collection": report.content.qdrant_collection,
                    "runner_version": report.content.runner_version,
                    "traces": sorted(
                        runner.development_traces, key=lambda item: item["case_id"]
                    ),
                },
            )
        candidate_summary = next(
            item
            for item in report.content.mode_summaries
            if item.mode is suite.content.candidate_mode
        )
        print(
            json.dumps(
                {
                    "benchmark_id": report.content.benchmark_id,
                    "suite_partition": report.content.suite_partition.value,
                    "benchmark_suite_sha256": report.content.benchmark_suite_sha256,
                    "corpus_release_id": report.content.corpus_release_id,
                    "vector_batch_sha256": report.content.vector_batch_sha256,
                    "candidate_mode": candidate_summary.mode.value,
                    "mean_recall_at_k": candidate_summary.mean_recall_at_k,
                    "mean_ndcg_at_k": candidate_summary.mean_ndcg_at_k,
                    "mean_reciprocal_rank": candidate_summary.mean_reciprocal_rank,
                    "mean_context_precision_at_k": (
                        candidate_summary.mean_context_precision_at_k
                    ),
                    "complete_evidence_set_rate": (
                        candidate_summary.complete_evidence_set_rate
                    ),
                    "mean_required_role_recall": (
                        candidate_summary.mean_required_role_recall
                    ),
                    "forbidden_leakage_case_count": (
                        candidate_summary.forbidden_leakage_case_count
                    ),
                    "candidate_failure_case_count": (
                        candidate_summary.candidate_failure_case_count
                    ),
                    "insufficient_evidence_accuracy": (
                        candidate_summary.insufficient_evidence_accuracy
                    ),
                    "safety_stratum_count": len(report.content.stratum_summaries),
                    "p95_latency_ms": candidate_summary.p95_latency_ms,
                    "outcome": report.content.outcome,
                    "blockers": list(report.content.blockers),
                    "report_sha256": report.report_sha256,
                    "output": str(arguments.output),
                },
                sort_keys=True,
            )
        )
        return 0 if report.content.outcome == "ACCEPTED" else 8
    finally:
        await qdrant.close()
        if database is not None:
            await database.close()


async def benchmark_accept(arguments: argparse.Namespace) -> int:
    database = Database(arguments.database_url)
    try:
        content = BenchmarkAcceptanceAttestationContent.model_validate_json(
            arguments.path.read_text(encoding="utf-8")
        )
        report = BenchmarkReport.model_validate_json(
            arguments.report.read_text(encoding="utf-8")
        )
        signer = _signer(arguments)
        envelope = signer.sign(canonical_json_bytes(content))
        await SQLAttestationRepository(database).record_and_verify(
            content,
            envelope,
            purpose=BenchmarkAttestationPurpose.BENCHMARK_ACCEPTANCE,
            predicate_type="https://med-rag.local/attestations/benchmark-acceptance",
        )
        acceptance = SignedBenchmarkAcceptance.seal(
            content,
            signature_sha256=envelope.signature_sha256,
            signer_identity=envelope.signer_identity,
            signing_key_id=envelope.key_id,
        )
        release = await SQLCorpusReleaseRepository(
            database
        ).register_benchmark_acceptance(
            content.corpus_release_id,
            acceptance=acceptance,
            report=report,
        )
        _write_json_output(arguments.output, acceptance)
        print(
            json.dumps(
                {
                    "acceptance_id": content.acceptance_id,
                    "corpus_release_id": content.corpus_release_id,
                    "benchmark_acceptance_sha256": acceptance.statement_sha256,
                    "benchmark_report_sha256": content.benchmark_report_sha256,
                    "candidate_configuration_sha256": (
                        content.candidate_configuration_sha256
                    ),
                    "benchmark_valid_until": release.benchmark_valid_until,
                    "output": str(arguments.output),
                },
                default=str,
                sort_keys=True,
            )
        )
        return 0
    finally:
        await database.close()


def _add_database_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database-url", default=get_settings().database_url)


def _add_adjudication_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    _add_database_argument(parser)
    parser.add_argument(
        "--artifact-store",
        type=Path,
        default=get_settings().steward_artifact_store_path,
    )


def _required_embedding_argument(arguments: argparse.Namespace, name: str):
    value = getattr(arguments, name)
    if value is None:
        raise ValueError(
            f"--{name.replace('_', '-')} is required for a verified-local candidate"
        )
    return value


def _verified_embedding_artifact(
    arguments: argparse.Namespace,
    *,
    prefix: str,
    kind: ModelArtifactKind,
):
    root = _required_embedding_argument(arguments, f"{prefix}_model_root")
    manifest_path = _required_embedding_argument(arguments, f"{prefix}_model_manifest")
    expected_digest = _required_embedding_argument(
        arguments, f"{prefix}_artifact_sha256"
    )
    manifest = ModelArtifactManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    return verify_model_artifact(
        root,
        manifest,
        expected_artifact_sha256=expected_digest,
        expected_kind=kind,
    )


def _build_embedding_backend(
    arguments: argparse.Namespace,
    *,
    baseline_dense_dimension: int,
    baseline_sparse_dimension: int,
    maximum_batch_size: int,
    expected_dense: DenseVectorDefinition | None = None,
    expected_sparse: SparseVectorDefinition | None = None,
) -> EmbeddingBackend:
    if arguments.embedding_backend == "deterministic":
        return DeterministicHashingBackend(
            dense_dimension=baseline_dense_dimension,
            sparse_dimension=baseline_sparse_dimension,
        )

    policy = EmbeddingExecutionPolicy(
        max_batch_size=maximum_batch_size,
        timeout_seconds=arguments.embedding_timeout,
        max_attempts=arguments.embedding_max_attempts,
        retry_delay_seconds=arguments.embedding_retry_delay,
        max_retry_delay_seconds=arguments.embedding_max_retry_delay,
    )
    dense_artifact = _verified_embedding_artifact(
        arguments, prefix="dense", kind=ModelArtifactKind.DENSE
    )
    sparse_artifact = _verified_embedding_artifact(
        arguments, prefix="sparse", kind=ModelArtifactKind.SPARSE
    )
    if expected_dense is not None and (
        dense_artifact.reference != expected_dense.model
        or dense_artifact.manifest.content.dimension != expected_dense.dimension
    ):
        raise ValueError("dense model artifact does not match the vector batch pin")
    if expected_sparse is not None and (
        sparse_artifact.reference != expected_sparse.model
        or sparse_artifact.manifest.content.dimension != expected_sparse.dimension
    ):
        raise ValueError("sparse model artifact does not match the vector batch pin")

    registry = default_adapter_registry()
    dense = registry.create_dense(dense_artifact, device=arguments.device)
    sparse = registry.create_sparse(sparse_artifact)
    return ProductionEmbeddingBackend(
        dense,
        sparse,
        policy=policy,
    )


def _build_reranker_backend(
    arguments: argparse.Namespace,
    candidate: RetrievalCandidateManifest,
) -> RerankerBackend | None:
    configured = candidate.content.reranker
    if configured is None:
        return None
    root = _required_embedding_argument(arguments, "reranker_model_root")
    manifest_path = _required_embedding_argument(arguments, "reranker_model_manifest")
    expected_digest = _required_embedding_argument(
        arguments, "reranker_artifact_sha256"
    )
    manifest = ModelArtifactManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    artifact = verify_model_artifact(
        root,
        manifest,
        expected_artifact_sha256=expected_digest,
        expected_kind=ModelArtifactKind.RERANKER,
    )
    if artifact.reference != configured.model:
        raise ValueError("reranker artifact does not match the sealed candidate pin")
    return Qwen3RerankerAdapter(
        artifact,
        device=arguments.reranker_device,
        score_cache=arguments.reranker_score_cache,
    )


def _add_embedding_backend_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("embedding backend")
    group.add_argument(
        "--embedding-backend",
        choices=("deterministic", "candidate", "verified-local", "production"),
        default="deterministic",
        help=(
            "Use the plumbing baseline or manifest-selected verified local adapters. "
            "The 'production' value is a deprecated compatibility alias for 'candidate'."
        ),
    )
    group.add_argument("--dense-model-root", type=Path)
    group.add_argument("--dense-model-manifest", type=Path)
    group.add_argument("--dense-artifact-sha256")
    group.add_argument("--sparse-model-root", type=Path)
    group.add_argument("--sparse-model-manifest", type=Path)
    group.add_argument("--sparse-artifact-sha256")
    group.add_argument(
        "--device",
        help="Require the dense runtime device to match the sealed adapter parameter.",
    )
    group.add_argument("--embedding-timeout", type=float, default=120.0)
    group.add_argument("--embedding-max-attempts", type=int, default=2)
    group.add_argument("--embedding-retry-delay", type=float, default=0.25)
    group.add_argument("--embedding-max-retry-delay", type=float, default=2.0)


def _add_reranker_backend_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("reranker backend")
    group.add_argument("--reranker-model-root", type=Path)
    group.add_argument("--reranker-model-manifest", type=Path)
    group.add_argument("--reranker-artifact-sha256")
    group.add_argument("--reranker-score-cache", type=Path)
    group.add_argument(
        "--reranker-device",
        help="Require the runtime device to match the sealed reranker parameter.",
    )


def _add_signer_arguments(parser: argparse.ArgumentParser) -> None:
    settings = get_settings()
    parser.add_argument(
        "--signing-key", type=Path, default=settings.corpus_steward_signing_key_path
    )
    parser.add_argument("--signing-key-id", default=settings.corpus_steward_signing_key_id)
    parser.add_argument("--signer-identity", default=settings.corpus_steward_signer_identity)


def _add_qdrant_arguments(
    parser: argparse.ArgumentParser, *, output_required: bool = False
) -> None:
    settings = get_settings()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument(
        "--candidate",
        type=Path,
        required=True,
        help="Sealed candidate whose vector profile determines the collection identity.",
    )
    parser.add_argument("--qdrant-url", default=settings.qdrant_url)
    parser.add_argument("--qdrant-api-key", default=settings.qdrant_api_key)
    parser.add_argument(
        "--qdrant-timeout", type=float, default=settings.qdrant_timeout_seconds
    )
    parser.add_argument(
        "--expected-qdrant-version",
        default=settings.qdrant_expected_version or QDRANT_PINNED_VERSION,
    )
    parser.add_argument("--smoke-samples", type=int, default=3)
    parser.add_argument("--smoke-limit", type=int, default=10)
    parser.add_argument("--output", type=Path, required=output_required)
    _add_database_argument(parser)


def _add_reconciliation_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    settings = get_settings()
    _add_database_argument(parser)
    _add_signer_arguments(parser)
    parser.add_argument("--artifact-store", type=Path, default=settings.steward_artifact_store_path)
    parser.add_argument(
        "--max-artifact-bytes", type=int, default=settings.steward_max_artifact_bytes
    )
    parser.add_argument(
        "--request-timeout", type=float, default=settings.steward_request_timeout_seconds
    )
    parser.add_argument(
        "--allow-private-networks",
        action="store_true",
        default=settings.steward_allow_private_networks,
    )


def _add_structured_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    settings = get_settings()
    _add_database_argument(parser)
    _add_signer_arguments(parser)
    parser.add_argument("--artifact-store", type=Path, default=settings.steward_artifact_store_path)
    parser.add_argument("--max-entries", type=int, default=settings.steward_fhir_max_entries)
    parser.add_argument(
        "--max-total-uncompressed-bytes",
        type=int,
        default=settings.steward_fhir_max_total_uncompressed_bytes,
    )
    parser.add_argument(
        "--max-member-bytes",
        type=int,
        default=settings.steward_fhir_max_member_bytes,
    )
    parser.add_argument(
        "--max-compression-ratio",
        type=float,
        default=settings.steward_fhir_max_compression_ratio,
    )
    parser.add_argument("--max-json-depth", type=int, default=settings.steward_fhir_max_json_depth)
    parser.add_argument(
        "--max-artifact-bytes", type=int, default=settings.steward_max_artifact_bytes
    )
    parser.add_argument(
        "--request-timeout", type=float, default=settings.steward_request_timeout_seconds
    )
    parser.add_argument(
        "--allow-private-networks",
        action="store_true",
        default=settings.steward_allow_private_networks,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="corpus-steward",
        description="Build-side tools for immutable clinical-guideline corpus releases.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser(
        "validate-bundle", description="Validate a frozen corpus release bundle."
    )
    validate.add_argument("path", type=Path)
    validate.add_argument(
        "--require-activatable",
        action="store_true",
        help="Return a non-zero status when deterministic activation gates are incomplete.",
    )
    export = subparsers.add_parser(
        "export-schema", description="Export the versioned corpus bundle JSON Schema."
    )
    export.add_argument("path", type=Path)
    export_activation = subparsers.add_parser(
        "export-activation-schema",
        description="Export the signed activation-decision JSON Schema.",
    )
    export_activation.add_argument("path", type=Path)
    export_materialization = subparsers.add_parser(
        "export-materialization-schema",
        description="Export the signed Phase 3 materialization-result JSON Schema.",
    )
    export_materialization.add_argument("path", type=Path)
    export_index_vectors = subparsers.add_parser(
        "export-index-vector-schema",
        description="Export the sealed Qdrant vector-batch JSON Schema.",
    )
    export_index_vectors.add_argument("path", type=Path)
    export_index_attestation = subparsers.add_parser(
        "export-index-attestation-schema",
        description="Export the signed Qdrant index-attestation JSON Schema.",
    )
    export_index_attestation.add_argument("path", type=Path)
    export_benchmark_suite = subparsers.add_parser(
        "export-benchmark-suite-schema",
        description="Export the sealed retrieval benchmark-suite JSON Schema.",
    )
    export_benchmark_suite.add_argument("path", type=Path)
    export_benchmark_report = subparsers.add_parser(
        "export-benchmark-report-schema",
        description="Export the sealed retrieval benchmark-report JSON Schema.",
    )
    export_benchmark_report.add_argument("path", type=Path)
    export_candidate = subparsers.add_parser(
        "export-candidate-schema",
        description="Export the sealed retrieval-candidate JSON Schema.",
    )
    export_candidate.add_argument("path", type=Path)
    export_benchmark_acceptance = subparsers.add_parser(
        "export-benchmark-acceptance-schema",
        description="Export the signed benchmark-acceptance JSON Schema.",
    )
    export_benchmark_acceptance.add_argument("path", type=Path)
    export_bm25_statistics = subparsers.add_parser(
        "export-bm25-statistics-schema",
        description="Export the sealed release-derived BM25 statistics JSON Schema.",
    )
    export_bm25_statistics.add_argument("path", type=Path)
    export_qwen_matrix = subparsers.add_parser(
        "export-qwen-runtime-matrix-schemas",
        description="Export pinned Qwen runtime-matrix request/report JSON Schemas.",
    )
    export_qwen_matrix.add_argument("directory", type=Path)
    export_reranker_matrix = subparsers.add_parser(
        "export-reranker-runtime-matrix-schemas",
        description="Export pinned reranker runtime-matrix request/report JSON Schemas.",
    )
    export_reranker_matrix.add_argument("directory", type=Path)
    export_adjudication = subparsers.add_parser(
        "export-benchmark-adjudication-schemas",
        description="Export the clinical adjudication workflow JSON Schemas.",
    )
    export_adjudication.add_argument("directory", type=Path)
    export_source_derived = subparsers.add_parser(
        "export-benchmark-source-derived-schemas",
        description="Export automated source-derived benchmark workflow JSON Schemas.",
    )
    export_source_derived.add_argument("directory", type=Path)
    export_model_artifact = subparsers.add_parser(
        "export-model-artifact-schema",
        description="Export the sealed local model-artifact manifest JSON Schema.",
    )
    export_model_artifact.add_argument("path", type=Path)
    model_manifest = subparsers.add_parser(
        "model-artifact-manifest",
        description="Inventory and seal an immutable local embedding artifact.",
    )
    model_manifest.add_argument("root", type=Path)
    model_manifest.add_argument(
        "--kind", choices=[item.value for item in ModelArtifactKind], required=True
    )
    model_manifest.add_argument("--model-id", required=True)
    model_manifest.add_argument("--revision", required=True)
    model_manifest.add_argument("--dimension", type=int, required=True)
    model_manifest.add_argument("--adapter-id", required=True)
    model_manifest.add_argument("--adapter-revision", required=True)
    model_manifest.add_argument("--adapter-parameters", type=Path)
    model_manifest.add_argument("--output", type=Path, required=True)
    model_verify = subparsers.add_parser(
        "model-artifact-verify",
        description="Exhaustively verify local model bytes against a sealed manifest.",
    )
    model_verify.add_argument("root", type=Path)
    model_verify.add_argument("--manifest", type=Path, required=True)
    model_verify.add_argument("--expected-artifact-sha256", required=True)
    bm25_statistics = subparsers.add_parser(
        "bm25-release-statistics",
        description="Derive sealed BM25 document-length statistics from an exact release.",
    )
    bm25_statistics.add_argument("bundle", type=Path)
    bm25_statistics.add_argument("--adapter-parameters", type=Path, required=True)
    bm25_statistics.add_argument("--output", type=Path, required=True)
    qwen_matrix = subparsers.add_parser(
        "qwen-runtime-matrix",
        description="Measure pinned verified-local Qwen3 targets or seal their blockers.",
    )
    qwen_matrix.add_argument("path", type=Path)
    qwen_matrix.add_argument("--bundle", type=Path, required=True)
    qwen_matrix.add_argument("--suite", type=Path, required=True)
    qwen_matrix.add_argument("--workspace-root", type=Path, default=Path.cwd())
    qwen_matrix.add_argument("--output", type=Path, required=True)
    reranker_matrix = subparsers.add_parser(
        "reranker-runtime-matrix",
        description=(
            "Measure pinned verified-local reranker targets or seal their blockers."
        ),
    )
    reranker_matrix.add_argument("path", type=Path)
    reranker_matrix.add_argument("--bundle", type=Path, required=True)
    reranker_matrix.add_argument("--suite", type=Path, required=True)
    reranker_matrix.add_argument("--workspace-root", type=Path, default=Path.cwd())
    reranker_matrix.add_argument("--output", type=Path, required=True)
    seal_vectors = subparsers.add_parser(
        "index-seal-vectors",
        description="Validate vector shapes/model pins and seal an index vector batch.",
    )
    seal_vectors.add_argument("path", type=Path)
    seal_vectors.add_argument("--output", type=Path, required=True)
    produce_vectors = subparsers.add_parser(
        "index-produce-vectors",
        description=(
            "Produce a sealed vector batch with either the deterministic baseline or "
            "verified local production adapters."
        ),
    )
    produce_vectors.add_argument("bundle", type=Path)
    produce_vectors.add_argument("--output", type=Path, required=True)
    produce_vectors.add_argument("--batch-size", type=int, default=32)
    produce_vectors.add_argument(
        "--checkpoint",
        type=Path,
        help="Atomically persist and resume a digest-sealed vector-production prefix.",
    )
    produce_vectors.add_argument(
        "--checkpoint-interval-batches",
        type=int,
        default=10,
        help="Persist every N completed batches and always persist the final batch.",
    )
    produce_vectors.add_argument("--dense-dimension", type=int, default=384)
    produce_vectors.add_argument("--sparse-dimension", type=int, default=2**18)
    _add_embedding_backend_arguments(produce_vectors)
    seal_benchmark = subparsers.add_parser(
        "benchmark-seal-suite",
        description="Validate benchmark cases and seal their release-bound suite.",
    )
    seal_benchmark.add_argument("path", type=Path)
    seal_benchmark.add_argument("--output", type=Path, required=True)
    seal_review = subparsers.add_parser(
        "benchmark-seal-review",
        description="Validate and digest-seal one imported clinical reviewer decision.",
    )
    seal_review.add_argument("path", type=Path)
    seal_review.add_argument("--output", type=Path, required=True)
    seal_resolution = subparsers.add_parser(
        "benchmark-seal-resolution",
        description="Validate and digest-seal one clinical disagreement resolution.",
    )
    seal_resolution.add_argument("path", type=Path)
    seal_resolution.add_argument("--output", type=Path, required=True)
    access_policy = subparsers.add_parser(
        "benchmark-register-access-policy",
        description="Seal and register an immutable benchmark access policy.",
    )
    access_policy.add_argument("path", type=Path)
    access_policy.add_argument("--output", type=Path, required=True)
    _add_adjudication_runtime_arguments(access_policy)
    process_policy = subparsers.add_parser(
        "benchmark-register-adjudication-process",
        description="Seal and register an immutable adjudication process policy.",
    )
    process_policy.add_argument("path", type=Path)
    process_policy.add_argument("--output", type=Path, required=True)
    _add_adjudication_runtime_arguments(process_policy)
    threshold_policy = subparsers.add_parser(
        "benchmark-register-threshold-policy",
        description="Seal and register prespecified clinical benchmark thresholds.",
    )
    threshold_policy.add_argument("path", type=Path)
    threshold_policy.add_argument("--output", type=Path, required=True)
    _add_adjudication_runtime_arguments(threshold_policy)
    import_reviews = subparsers.add_parser(
        "benchmark-import-reviews",
        description="Import immutable reviewer decisions under registered policies.",
    )
    import_reviews.add_argument("path", type=Path)
    import_reviews.add_argument("--access-policy-sha256", required=True)
    import_reviews.add_argument("--adjudication-process-sha256", required=True)
    _add_adjudication_runtime_arguments(import_reviews)
    import_resolutions = subparsers.add_parser(
        "benchmark-import-resolutions",
        description="Import immutable disagreement resolutions under registered policies.",
    )
    import_resolutions.add_argument("path", type=Path)
    import_resolutions.add_argument("--access-policy-sha256", required=True)
    import_resolutions.add_argument("--adjudication-process-sha256", required=True)
    _add_adjudication_runtime_arguments(import_resolutions)
    seal_adjudication = subparsers.add_parser(
        "benchmark-seal-adjudication",
        description="Resolve the review ledger into one immutable adjudication record.",
    )
    seal_adjudication.add_argument("path", type=Path)
    seal_adjudication.add_argument("--output", type=Path, required=True)
    _add_adjudication_runtime_arguments(seal_adjudication)
    build_clinical_suite = subparsers.add_parser(
        "benchmark-build-clinical-suite",
        description=(
            "Build a policy-guarded development or one-time sealed-holdout suite."
        ),
    )
    build_clinical_suite.add_argument("path", type=Path)
    build_clinical_suite.add_argument("--bundle", type=Path, required=True)
    build_clinical_suite.add_argument("--output", type=Path, required=True)
    _add_adjudication_runtime_arguments(build_clinical_suite)
    partition_seed = subparsers.add_parser(
        "benchmark-generate-partition-seed",
        description="Create a custody-controlled benchmark partition seed.",
    )
    partition_seed.add_argument("--output", type=Path, required=True)
    seal_generation_policy = subparsers.add_parser(
        "benchmark-seal-generation-policy",
        description="Validate and digest-seal an automated generation policy.",
    )
    seal_generation_policy.add_argument("path", type=Path)
    seal_generation_policy.add_argument("--output", type=Path, required=True)
    generate_source_derived = subparsers.add_parser(
        "benchmark-generate-source-derived",
        description=(
            "Deterministically build registered development and sealed-holdout suites "
            "from canonical evidence."
        ),
    )
    generate_source_derived.add_argument("bundle", type=Path)
    generate_source_derived.add_argument("--access-policy", type=Path, required=True)
    generate_source_derived.add_argument("--threshold-policy", type=Path, required=True)
    generate_source_derived.add_argument("--generation-policy", type=Path, required=True)
    generate_source_derived.add_argument("--request", type=Path, required=True)
    generate_source_derived.add_argument("--partition-seed", type=Path, required=True)
    generate_source_derived.add_argument(
        "--development-output", type=Path, required=True
    )
    generate_source_derived.add_argument("--sealed-holdout-output", type=Path)
    generate_source_derived.add_argument("--generation-record-output", type=Path)
    _add_adjudication_runtime_arguments(generate_source_derived)
    seal_candidate = subparsers.add_parser(
        "candidate-seal",
        description="Validate and seal an exact retrieval candidate configuration.",
    )
    seal_candidate.add_argument("path", type=Path)
    seal_candidate.add_argument("--output", type=Path, required=True)
    derive_suite = subparsers.add_parser(
        "benchmark-derive-development-suite",
        description=(
            "Re-seal an automated development suite with one candidate's pool and fusion settings."
        ),
    )
    derive_suite.add_argument("suite", type=Path)
    derive_suite.add_argument("--candidate", type=Path, required=True)
    derive_suite.add_argument("--output", type=Path, required=True)
    derive_suite.add_argument("--register", action="store_true")
    derive_suite.add_argument("--actor-identity")
    _add_adjudication_runtime_arguments(derive_suite)
    trust_root = subparsers.add_parser(
        "register-trust-root", description="Register a versioned authoritative trust root."
    )
    trust_root.add_argument("path", type=Path)
    trust_root.add_argument("--replace", action="store_true")
    _add_database_argument(trust_root)
    signing_key = subparsers.add_parser(
        "register-key", description="Register a trusted Ed25519 public key."
    )
    signing_key.add_argument("--public-key", type=Path, required=True)
    signing_key.add_argument("--key-id", required=True)
    signing_key.add_argument("--signer-identity", required=True)
    signing_key.add_argument(
        "--purpose",
        action="append",
        choices=[
            *(item.value for item in AttestationPurpose),
            *(item.value for item in BenchmarkAttestationPurpose),
        ],
        required=True,
    )
    _add_database_argument(signing_key)
    generate_key = subparsers.add_parser(
        "keygen", description="Generate a local Ed25519 key pair without overwriting files."
    )
    generate_key.add_argument("--private-key", type=Path, required=True)
    generate_key.add_argument("--public-key", type=Path, required=True)
    reconcile_command = subparsers.add_parser(
        "reconcile", description="Reconcile one complete authoritative inventory."
    )
    reconcile_command.add_argument("trust_root")
    reconcile_command.add_argument("--idempotency-key")
    reconcile_command.add_argument("--output", type=Path)
    _add_reconciliation_runtime_arguments(reconcile_command)
    structured = subparsers.add_parser(
        "process-structured",
        description="Validate and inventory one reconciled FHIR package artifact.",
    )
    structured.add_argument("candidate_id")
    structured.add_argument("--item-id")
    structured.add_argument("--output", type=Path)
    _add_structured_runtime_arguments(structured)
    structured_inputs = subparsers.add_parser(
        "resolve-structured-inputs",
        description="Acquire and attest narrative assets and the FHIR dependency closure.",
    )
    structured_inputs.add_argument("candidate_id")
    structured_inputs.add_argument("--item-id")
    structured_inputs.add_argument("--output", type=Path)
    _add_structured_runtime_arguments(structured_inputs)
    materialization = subparsers.add_parser(
        "materialize",
        description=("Bind source authority, extract anchored DAK evidence, and sign a corpus RC."),
    )
    materialization.add_argument("candidate_id")
    materialization.add_argument("--item-id")
    materialization.add_argument("--output", type=Path)
    _add_database_argument(materialization)
    _add_signer_arguments(materialization)
    materialization.add_argument(
        "--artifact-store",
        type=Path,
        default=get_settings().steward_artifact_store_path,
    )
    qa_command = subparsers.add_parser(
        "qa",
        description=(
            "Replay evidence anchors, record a signed QA batch, and register a validated release."
        ),
    )
    qa_command.add_argument("candidate_id")
    qa_command.add_argument("--release-policy", type=Path)
    qa_command.add_argument("--previous-release-id")
    qa_command.add_argument("--bundle-output", type=Path)
    _add_database_argument(qa_command)
    _add_signer_arguments(qa_command)
    qa_command.add_argument(
        "--artifact-store",
        type=Path,
        default=get_settings().steward_artifact_store_path,
    )
    qdrant_build_command = subparsers.add_parser(
        "qdrant-build",
        description=(
            "Create or resume an isolated candidate vector-profile collection and validate it."
        ),
    )
    _add_qdrant_arguments(qdrant_build_command)
    qdrant_build_command.add_argument("--batch-size", type=int, default=64)
    qdrant_validate_command = subparsers.add_parser(
        "qdrant-validate",
        description="Exhaustively validate a release-specific Qdrant collection.",
    )
    _add_qdrant_arguments(qdrant_validate_command)
    qdrant_attest_command = subparsers.add_parser(
        "qdrant-attest",
        description=(
            "Revalidate, sign the index attestation, and register the index as VALIDATED."
        ),
    )
    _add_qdrant_arguments(qdrant_attest_command, output_required=True)
    _add_signer_arguments(qdrant_attest_command)
    benchmark_command = subparsers.add_parser(
        "benchmark-run",
        description="Run release-bound sparse, dense, and hybrid retrieval ablations.",
    )
    benchmark_command.add_argument("bundle", type=Path)
    benchmark_command.add_argument("--vectors", type=Path, required=True)
    suite_source = benchmark_command.add_mutually_exclusive_group(required=True)
    suite_source.add_argument("--suite", type=Path)
    suite_source.add_argument("--registered-suite-sha256")
    benchmark_command.add_argument("--candidate", type=Path, required=True)
    benchmark_command.add_argument("--output", type=Path, required=True)
    benchmark_command.add_argument(
        "--development-trace-case-id",
        action="append",
        help="Development-only case ID to trace; repeat for multiple cases.",
    )
    benchmark_command.add_argument(
        "--development-trace-depth", type=int, default=100
    )
    benchmark_command.add_argument("--development-trace-output", type=Path)
    settings = get_settings()
    benchmark_command.add_argument("--qdrant-url", default=settings.qdrant_url)
    benchmark_command.add_argument("--qdrant-api-key", default=settings.qdrant_api_key)
    benchmark_command.add_argument(
        "--qdrant-timeout", type=float, default=settings.qdrant_timeout_seconds
    )
    benchmark_command.add_argument("--actor-identity")
    _add_database_argument(benchmark_command)
    benchmark_command.add_argument(
        "--artifact-store",
        type=Path,
        default=settings.steward_artifact_store_path,
    )
    _add_embedding_backend_arguments(benchmark_command)
    _add_reranker_backend_arguments(benchmark_command)
    benchmark_accept_command = subparsers.add_parser(
        "benchmark-accept",
        description="Sign and register one accepted sealed-holdout benchmark result.",
    )
    benchmark_accept_command.add_argument("path", type=Path)
    benchmark_accept_command.add_argument("--report", type=Path, required=True)
    benchmark_accept_command.add_argument("--output", type=Path, required=True)
    _add_database_argument(benchmark_accept_command)
    _add_signer_arguments(benchmark_accept_command)
    exception = subparsers.add_parser(
        "approve-exception", description="Sign and register a bounded coverage exception."
    )
    exception.add_argument("path", type=Path)
    _add_reconciliation_runtime_arguments(exception)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "validate-bundle":
        return validate_bundle(
            arguments.path,
            require_activatable=arguments.require_activatable,
        )
    if arguments.command == "export-schema":
        return export_schema(arguments.path, corpus_schema_document())
    if arguments.command == "export-activation-schema":
        return export_schema(arguments.path, activation_schema_document())
    if arguments.command == "export-materialization-schema":
        return export_schema(arguments.path, materialization_schema_document())
    if arguments.command == "export-index-vector-schema":
        return export_schema(arguments.path, index_vector_schema_document())
    if arguments.command == "export-index-attestation-schema":
        return export_schema(arguments.path, index_attestation_schema_document())
    if arguments.command == "export-benchmark-suite-schema":
        return export_schema(arguments.path, benchmark_suite_schema_document())
    if arguments.command == "export-benchmark-report-schema":
        return export_schema(arguments.path, benchmark_report_schema_document())
    if arguments.command == "export-candidate-schema":
        return export_schema(arguments.path, candidate_schema_document())
    if arguments.command == "export-benchmark-acceptance-schema":
        return export_schema(arguments.path, benchmark_acceptance_schema_document())
    if arguments.command == "export-bm25-statistics-schema":
        return export_schema(arguments.path, bm25_statistics_schema_document())
    if arguments.command == "export-qwen-runtime-matrix-schemas":
        return export_qwen_runtime_matrix_schemas(arguments.directory)
    if arguments.command == "export-reranker-runtime-matrix-schemas":
        return export_reranker_runtime_matrix_schemas(arguments.directory)
    if arguments.command == "export-benchmark-adjudication-schemas":
        return export_benchmark_adjudication_schemas(arguments.directory)
    if arguments.command == "export-benchmark-source-derived-schemas":
        return export_benchmark_source_derived_schemas(arguments.directory)
    if arguments.command == "export-model-artifact-schema":
        return export_schema(arguments.path, model_artifact_schema_document())
    try:
        if arguments.command == "model-artifact-manifest":
            return create_model_artifact_manifest(arguments)
        if arguments.command == "model-artifact-verify":
            return verify_model_artifact_command(arguments)
        if arguments.command == "bm25-release-statistics":
            return derive_bm25_statistics_command(arguments)
        if arguments.command == "qwen-runtime-matrix":
            return asyncio.run(qwen_runtime_matrix_command(arguments))
        if arguments.command == "reranker-runtime-matrix":
            return asyncio.run(reranker_runtime_matrix_command(arguments))
        if arguments.command == "index-seal-vectors":
            return seal_index_vectors(arguments)
        if arguments.command == "index-produce-vectors":
            return asyncio.run(produce_index_vectors(arguments))
        if arguments.command == "benchmark-seal-suite":
            return seal_benchmark_suite(arguments)
        if arguments.command == "benchmark-seal-review":
            return seal_clinical_review(arguments)
        if arguments.command == "benchmark-seal-resolution":
            return seal_disagreement_resolution(arguments)
        if arguments.command == "benchmark-register-access-policy":
            return asyncio.run(seal_benchmark_access_policy(arguments))
        if arguments.command == "benchmark-register-adjudication-process":
            return asyncio.run(seal_adjudication_process_policy(arguments))
        if arguments.command == "benchmark-register-threshold-policy":
            return asyncio.run(seal_benchmark_threshold_policy(arguments))
        if arguments.command == "benchmark-import-reviews":
            return asyncio.run(import_clinical_reviews(arguments))
        if arguments.command == "benchmark-import-resolutions":
            return asyncio.run(import_disagreement_resolutions(arguments))
        if arguments.command == "benchmark-seal-adjudication":
            return asyncio.run(seal_clinical_adjudication(arguments))
        if arguments.command == "benchmark-build-clinical-suite":
            return asyncio.run(build_clinical_benchmark_suite(arguments))
        if arguments.command == "benchmark-generate-partition-seed":
            return generate_benchmark_partition_seed(arguments)
        if arguments.command == "benchmark-seal-generation-policy":
            return seal_benchmark_generation_policy(arguments)
        if arguments.command == "benchmark-generate-source-derived":
            return asyncio.run(generate_source_derived_benchmarks(arguments))
        if arguments.command == "candidate-seal":
            return seal_retrieval_candidate(arguments)
        if arguments.command == "benchmark-derive-development-suite":
            return asyncio.run(derive_benchmark_suite_for_candidate(arguments))
        if arguments.command == "register-trust-root":
            return asyncio.run(register_trust_root(arguments))
        if arguments.command == "register-key":
            return asyncio.run(register_key(arguments))
        if arguments.command == "keygen":
            return keygen(arguments)
        if arguments.command == "reconcile":
            return asyncio.run(reconcile(arguments))
        if arguments.command == "process-structured":
            return asyncio.run(process_structured(arguments))
        if arguments.command == "resolve-structured-inputs":
            return asyncio.run(resolve_structured_inputs(arguments))
        if arguments.command == "materialize":
            return asyncio.run(materialize(arguments))
        if arguments.command == "qa":
            return asyncio.run(qa(arguments))
        if arguments.command == "qdrant-build":
            return asyncio.run(qdrant_build(arguments))
        if arguments.command == "qdrant-validate":
            return asyncio.run(qdrant_validate(arguments))
        if arguments.command == "qdrant-attest":
            return asyncio.run(qdrant_attest(arguments))
        if arguments.command == "benchmark-run":
            return asyncio.run(benchmark_run(arguments))
        if arguments.command == "benchmark-accept":
            return asyncio.run(benchmark_accept(arguments))
        if arguments.command == "approve-exception":
            return asyncio.run(approve_exception(arguments))
    except (OSError, ValueError, ValidationError, RuntimeError) as error:
        print(json.dumps({"error": str(error), "command": arguments.command}, sort_keys=True))
        return 2
    raise AssertionError(f"unhandled command: {arguments.command}")


if __name__ == "__main__":
    raise SystemExit(main())
