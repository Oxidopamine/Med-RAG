from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import timezone
from pathlib import Path

from pydantic import ValidationError

from app.core.config import get_settings
from app.corpus.releases import SQLCorpusReleaseRepository
from app.corpus_steward.adapter_registry import default_adapter_registry
from app.corpus_steward.benchmark import RetrievalBenchmarkRunner
from app.corpus_steward.benchmark_schemas import (
    BENCHMARK_CONTRACT_VERSION,
    BenchmarkAcceptanceAttestationContent,
    BenchmarkReport,
    BenchmarkSuite,
    BenchmarkSuiteContent,
    SignedBenchmarkAcceptance,
)
from app.corpus_steward.candidate_schemas import (
    CANDIDATE_CONTRACT_VERSION,
    RetrievalCandidateContent,
    RetrievalCandidateManifest,
)
from app.corpus_steward.connectors import HTTPConnectorTransport
from app.corpus_steward.crypto import Ed25519Signer, generate_ed25519_key_pair
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
from app.corpus_steward.registry import (
    SQLAttestationRepository,
    SQLTrustRootRegistry,
)
from app.corpus_steward.schemas import (
    AttestationPurpose,
    BenchmarkAttestationPurpose,
    CoverageExceptionContent,
    JobState,
    TrustRootDefinition,
)
from app.corpus_steward.service import ReconciliationService
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
    backend = _build_embedding_backend(
        arguments,
        baseline_dense_dimension=arguments.dense_dimension,
        baseline_sparse_dimension=arguments.sparse_dimension,
        maximum_batch_size=arguments.batch_size,
    )
    batch = await VectorBatchProducer(
        backend, batch_size=arguments.batch_size
    ).produce(bundle)
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


def _write_json_output(path: Path | None, value: object) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
        repository = SQLIndexBasisRepository(database)
        basis = await repository.load(bundle.manifest.content.corpus_release_id)
        repository.require_bundle_match(basis, bundle)
        if basis.release.state.value != "VALIDATED":
            raise ValueError("Qdrant build requires a VALIDATED corpus release")
        if basis.release.index_status != "NOT_BUILT":
            raise ValueError("refusing to write a release index that is already validated")
        report = await QdrantIndexService(
            qdrant,
            expected_qdrant_version=arguments.expected_qdrant_version,
            upsert_batch_size=arguments.batch_size,
        ).build(
            basis.bundle,
            vectors,
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
        repository = SQLIndexBasisRepository(database)
        basis = await repository.load(bundle.manifest.content.corpus_release_id)
        repository.require_bundle_match(basis, bundle)
        report = await QdrantIndexService(
            qdrant,
            expected_qdrant_version=arguments.expected_qdrant_version,
        ).validate(
            basis.bundle,
            vectors,
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
            source_classes=basis.source_classes,
            smoke_samples=arguments.smoke_samples,
            smoke_limit=arguments.smoke_limit,
        )
        content = IndexAttestationContent(
            corpus_release_id=basis.release.corpus_release_id,
            manifest_sha256=basis.release.manifest_sha256,
            qdrant_collection=basis.release.qdrant_collection,
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
    try:
        bundle, vectors = _load_index_inputs(arguments)
        suite = BenchmarkSuite.model_validate_json(
            arguments.suite.read_text(encoding="utf-8")
        )
        candidate = RetrievalCandidateManifest.model_validate_json(
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
        report = await RetrievalBenchmarkRunner(qdrant, backend).run(
            bundle, vectors, suite, candidate
        )
        _write_json_output(arguments.output, report)
        candidate = next(
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
                    "candidate_mode": candidate.mode.value,
                    "mean_recall_at_k": candidate.mean_recall_at_k,
                    "mean_ndcg_at_k": candidate.mean_ndcg_at_k,
                    "mean_reciprocal_rank": candidate.mean_reciprocal_rank,
                    "mean_context_precision_at_k": (
                        candidate.mean_context_precision_at_k
                    ),
                    "complete_evidence_set_rate": candidate.complete_evidence_set_rate,
                    "mean_required_role_recall": candidate.mean_required_role_recall,
                    "forbidden_leakage_case_count": (
                        candidate.forbidden_leakage_case_count
                    ),
                    "candidate_failure_case_count": candidate.candidate_failure_case_count,
                    "insufficient_evidence_accuracy": (
                        candidate.insufficient_evidence_accuracy
                    ),
                    "safety_stratum_count": len(report.content.stratum_summaries),
                    "p95_latency_ms": candidate.p95_latency_ms,
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
    model_manifest.add_argument("--kind", choices=["DENSE", "SPARSE"], required=True)
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
    produce_vectors.add_argument("--dense-dimension", type=int, default=384)
    produce_vectors.add_argument("--sparse-dimension", type=int, default=2**18)
    _add_embedding_backend_arguments(produce_vectors)
    seal_benchmark = subparsers.add_parser(
        "benchmark-seal-suite",
        description="Validate benchmark cases and seal their release-bound suite.",
    )
    seal_benchmark.add_argument("path", type=Path)
    seal_benchmark.add_argument("--output", type=Path, required=True)
    seal_candidate = subparsers.add_parser(
        "candidate-seal",
        description="Validate and seal an exact retrieval candidate configuration.",
    )
    seal_candidate.add_argument("path", type=Path)
    seal_candidate.add_argument("--output", type=Path, required=True)
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
            "Create or safely resume a reserved release collection, then validate it."
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
    benchmark_command.add_argument("--suite", type=Path, required=True)
    benchmark_command.add_argument("--candidate", type=Path, required=True)
    benchmark_command.add_argument("--output", type=Path, required=True)
    settings = get_settings()
    benchmark_command.add_argument("--qdrant-url", default=settings.qdrant_url)
    benchmark_command.add_argument("--qdrant-api-key", default=settings.qdrant_api_key)
    benchmark_command.add_argument(
        "--qdrant-timeout", type=float, default=settings.qdrant_timeout_seconds
    )
    _add_embedding_backend_arguments(benchmark_command)
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
    if arguments.command == "export-model-artifact-schema":
        return export_schema(arguments.path, model_artifact_schema_document())
    try:
        if arguments.command == "model-artifact-manifest":
            return create_model_artifact_manifest(arguments)
        if arguments.command == "model-artifact-verify":
            return verify_model_artifact_command(arguments)
        if arguments.command == "index-seal-vectors":
            return seal_index_vectors(arguments)
        if arguments.command == "index-produce-vectors":
            return asyncio.run(produce_index_vectors(arguments))
        if arguments.command == "benchmark-seal-suite":
            return seal_benchmark_suite(arguments)
        if arguments.command == "candidate-seal":
            return seal_retrieval_candidate(arguments)
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
