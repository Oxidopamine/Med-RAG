"""Drive the serving seam end to end against local Postgres and Qdrant.

Unit tests fake the index and the release. This does not: it registers the synthetic
fixture release into a scratch database, produces its vectors, builds and validates a
real Qdrant collection, signs an index attestation and a sealed-holdout acceptance,
runs the real activation gate, and then asks a question through the same
``QuestionService`` the API serves.

Two deliberate limits:

* The scratch database is a separate one, created here. Activation writes a singleton
  active-release pointer, and a local exercise has no business moving the pointer of a
  working corpus database.
* The generation lane is stubbed. There are no Bedrock or Vertex credentials to use
  locally, and stubbing the model is what leaves everything before it - release
  resolution, binding checks, encoding, search, fusion, evidence resolution, licence
  policy - running for real.

    python scripts/serving/local_serving_exercise.py --reset
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import timedelta
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "apps" / "api"))

from app.corpus.releases import (  # noqa: E402
    SQLCorpusReleaseRepository,
)
from app.corpus_steward.benchmark_schemas import (  # noqa: E402
    BenchmarkAcceptanceAttestationContent,
    BenchmarkCaseMetrics,
    BenchmarkCaseResult,
    BenchmarkModeSummary,
    BenchmarkProvenanceMode,
    BenchmarkProvenanceSummary,
    BenchmarkReport,
    BenchmarkReportContent,
    BenchmarkSuitePartition,
    MetricConfidenceInterval,
    RetrievalMode,
    RetrievedEvidence,
    RRFWeights,
    SignedBenchmarkAcceptance,
)
from app.corpus_steward.candidate_schemas import RetrievalCandidateManifest  # noqa: E402
from app.corpus_steward.crypto import Ed25519Signer  # noqa: E402
from app.corpus_steward.index_schemas import (  # noqa: E402
    IndexAttestationContent,
)
from app.corpus_steward.qdrant_index import (  # noqa: E402
    QdrantIndexService,
    QdrantRESTClient,
    candidate_qdrant_collection,
)
from app.corpus_steward.registry import SQLAttestationRepository  # noqa: E402
from app.corpus_steward.schemas import (  # noqa: E402
    AttestationPurpose,
    BenchmarkAttestationPurpose,
)
from app.corpus_steward.vector_producer import (  # noqa: E402
    DeterministicHashingBackend,
    VectorBatchProducer,
)
from app.persistence.database import Database  # noqa: E402
from app.persistence.models import (  # noqa: E402
    AcquisitionRow,
    ArtifactRow,
    PublisherRow,
    SourceRow,
    SourceVersionRow,
)
from app.reasoning.answer_service import GroundedAnswerComposer  # noqa: E402
from app.reasoning.generation_schemas import ModelAnswer, ModelClaim  # noqa: E402
from app.reasoning.question_service import QuestionService  # noqa: E402
from app.retrieval.serving import ServingRetrievalEngine  # noqa: E402
from app.schemas.corpus import (  # noqa: E402
    ActivationDecisionContent,
    CorpusReleaseBundle,
    SignedActivationDecision,
    canonical_json_bytes,
    canonical_sha256,
)
from app.schemas.domain import utc_now  # noqa: E402
from app.schemas.questions import (  # noqa: E402
    TERMINAL_STATUSES,
    QuestionCreate,
    QuestionStatus,
    SourceFilters,
)

FIXTURE_BUNDLE = REPOSITORY_ROOT / "data" / "fixtures" / "corpus-release-v1.json"
FIXTURE_CANDIDATE = REPOSITORY_ROOT / "models" / "configs" / "synthetic-candidate-v1.json"
# The dimensions the sealed synthetic candidate pins by artifact digest.
DENSE_DIMENSION = 32
SPARSE_DIMENSION = 2**8

ACTIVATION_SIGNER = Ed25519Signer(
    key_id="local-exercise-activation",
    signer_identity="local-exercise-control-plane",
    private_key=Ed25519PrivateKey.generate(),
)
BENCHMARK_SIGNER = Ed25519Signer(
    key_id="local-exercise-benchmark",
    signer_identity="local-exercise-benchmark-authority",
    private_key=Ed25519PrivateKey.generate(),
)


class StubGeneration:
    """Stands in for Bedrock or Vertex, citing only what it was handed."""

    def __init__(self) -> None:
        self.user_content: str | None = None

    async def generate(self, *, system_prompt: str, user_content: str) -> ModelAnswer:
        self.user_content = user_content
        evidence_ids = [
            line.split("evidence_id: ", 1)[1].split(" |", 1)[0].strip()
            for line in user_content.splitlines()
            if "evidence_id: " in line
        ]
        return ModelAnswer(
            sufficient_evidence=True,
            claims=[
                ModelClaim(
                    text="Stubbed claim over the evidence this question retrieved.",
                    evidence_ids=evidence_ids[:1],
                )
            ],
        )


async def ensure_database(admin_url: str, name: str, *, reset: bool) -> None:
    import asyncpg

    dsn = admin_url.replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(dsn)
    try:
        exists = await connection.fetchval(
            "select 1 from pg_database where datname = $1", name
        )
        if exists and reset:
            await connection.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity "
                "where datname = $1 and pid <> pg_backend_pid()",
                name,
            )
            await connection.execute(f'drop database "{name}"')
            exists = None
        if not exists:
            await connection.execute(f'create database "{name}"')
    finally:
        await connection.close()


async def drop_collection(qdrant_url: str, collection: str) -> None:
    """Remove the scratch collection so a re-run rebuilds it from nothing."""

    import httpx

    async with httpx.AsyncClient(base_url=qdrant_url.rstrip("/"), timeout=30.0) as client:
        response = await client.delete(f"/collections/{collection}")
        if response.status_code not in (200, 404):
            raise RuntimeError(f"could not drop {collection}: {response.status_code}")


async def seed_source_rows(database: Database) -> None:
    """Insert the source lineage the fixture release cites.

    Each row is flushed before the row that references it. Postgres enforces the
    foreign keys the test suite gets away with reordering under SQLite.
    """

    now = utc_now()
    async with database.session() as session:
        rows = (
            PublisherRow(publisher_id="PUB_FIXTURE", name="Fixture Publisher", created_at=now),
            SourceRow(
                source_id="SRC_FIXTURE_001",
                publisher_id="PUB_FIXTURE",
                title="Synthetic Guideline",
                source_class="E1",
                jurisdiction="TEST",
                canonical_url="https://fixtures.invalid/synthetic-guideline",
                license_render_allowed=True,
                created_at=now,
            ),
            SourceVersionRow(
                source_version_id="SV_FIXTURE_2026",
                source_id="SRC_FIXTURE_001",
                version_label="2026",
                status="EFFECTIVE",
                effective_from=None,
                effective_to=None,
                approved_for_retrieval=True,
                created_at=now,
                updated_at=now,
            ),
            ArtifactRow(
                artifact_id="ART_FIXTURE",
                sha256="a" * 64,
                byte_size=1,
                media_type="application/pdf",
                storage_key="sha256/aa/fixture.pdf",
                created_at=now,
            ),
            AcquisitionRow(
                acquisition_id="ACQ_FIXTURE",
                source_version_id="SV_FIXTURE_2026",
                artifact_id="ART_FIXTURE",
                requested_url="https://fixtures.invalid/synthetic-guideline.pdf",
                final_url="https://fixtures.invalid/synthetic-guideline.pdf",
                publisher_domain="fixtures.invalid",
                expected_sha256="a" * 64,
                http_etag=None,
                http_last_modified=None,
                acquired_at=now,
            ),
        )
        for row in rows:
            session.add(row)
            await session.flush()


async def sign_index_attestation(
    database: Database, bundle: CorpusReleaseBundle, report
) -> str:
    content = IndexAttestationContent(
        corpus_release_id=bundle.manifest.content.corpus_release_id,
        manifest_sha256=bundle.manifest.manifest_sha256,
        qdrant_collection=report.content.qdrant_collection,
        validation_report_sha256=report.report_sha256,
        qa_run_id="QA_LOCAL_EXERCISE",
        materialized_count=len(bundle.evidence),
        approved_count=len(bundle.evidence),
        quarantined_count=0,
        point_count=report.content.point_count,
        attested_at=report.content.validated_at,
    )
    attestations = SQLAttestationRepository(database)
    await attestations.register_key(
        key_id=ACTIVATION_SIGNER.key_id,
        signer_identity=ACTIVATION_SIGNER.signer_identity,
        public_key_pem=ACTIVATION_SIGNER.public_key_pem(),
        purposes=(AttestationPurpose.STAGE, AttestationPurpose.ACTIVATION),
    )
    await attestations.record_and_verify(
        content,
        ACTIVATION_SIGNER.sign(canonical_json_bytes(content)),
        purpose=AttestationPurpose.STAGE,
        predicate_type="https://med-rag.local/attestations/qdrant-index-validation",
    )
    return canonical_sha256(content)


async def register_acceptance(
    database: Database,
    releases: SQLCorpusReleaseRepository,
    bundle: CorpusReleaseBundle,
    candidate: RetrievalCandidateManifest,
    *,
    collection: str,
    vector_batch_sha256: str,
    index_attestation_sha256: str,
) -> SignedBenchmarkAcceptance:
    """Seal the acceptance this exercise activates against.

    It is synthetic by construction and says so: the report is over one fixture case.
    What it exercises is the binding - the release will only activate if these digests
    agree with the collection that was actually built.
    """

    now = utc_now()
    report = BenchmarkReport.seal(
        BenchmarkReportContent(
            runner_version="1.6.0",
            benchmark_id="local-exercise-sealed-holdout",
            suite_partition=BenchmarkSuitePartition.SEALED_HOLDOUT,
            provenance_mode=BenchmarkProvenanceMode.INDEPENDENT_REVIEWED,
            access_policy_sha256="d" * 64,
            adjudication_process_sha256="e" * 64,
            adjudication_record_sha256="6" * 64,
            threshold_policy_sha256="7" * 64,
            benchmark_suite_sha256="a" * 64,
            candidate_configuration_sha256=candidate.candidate_sha256,
            corpus_release_id=bundle.manifest.content.corpus_release_id,
            manifest_sha256=bundle.manifest.manifest_sha256,
            vector_batch_sha256=vector_batch_sha256,
            qdrant_collection=collection,
            top_k=1,
            candidate_limit=1,
            rrf_k=60,
            rrf_weights=RRFWeights(),
            candidate_mode=RetrievalMode.HYBRID,
            case_results=(
                BenchmarkCaseResult(
                    case_id="BQ_LOCAL_EXERCISE_001",
                    mode=RetrievalMode.HYBRID,
                    retrieved=(
                        RetrievedEvidence(
                            evidence_id="EV_FIXTURE_PRIMARY_001", rank=1, score=1.0
                        ),
                    ),
                    metrics=BenchmarkCaseMetrics(
                        recall_at_k=1.0,
                        ndcg_at_k=1.0,
                        reciprocal_rank=1.0,
                        context_precision_at_k=1.0,
                        context_precision_ceiling_at_k=1.0,
                        r_precision=1.0,
                        complete_evidence_set_recalled=True,
                        complete_evidence_set_recalled_at_budget=True,
                        required_role_recall=1.0,
                        latency_ms=1.0,
                    ),
                ),
            ),
            mode_summaries=(
                BenchmarkModeSummary(
                    mode=RetrievalMode.HYBRID,
                    case_count=1,
                    mean_recall_at_k=1.0,
                    mean_ndcg_at_k=1.0,
                    mean_reciprocal_rank=1.0,
                    mean_context_precision_at_k=1.0,
                    mean_context_precision_ceiling_at_k=1.0,
                    mean_r_precision=1.0,
                    complete_evidence_set_rate=1.0,
                    generation_context_budget=1,
                    complete_evidence_at_budget_rate=1.0,
                    mean_required_role_recall=1.0,
                    forbidden_leakage_case_count=0,
                    candidate_failure_case_count=0,
                    answerable_case_count=1,
                    answerable_mean_recall_at_k=1.0,
                    answerable_mean_ndcg_at_k=1.0,
                    answerable_mean_reciprocal_rank=1.0,
                    answerable_mean_context_precision_at_k=1.0,
                    answerable_mean_r_precision=1.0,
                    answerable_complete_evidence_set_rate=1.0,
                    answerable_complete_evidence_at_budget_rate=1.0,
                    answerable_mean_required_role_recall=1.0,
                    answerable_complete_evidence_set_confidence=MetricConfidenceInterval(
                        sample_count=1, successes=1, lower=0.2065, upper=1.0
                    ),
                    answerable_complete_evidence_at_budget_confidence=(
                        MetricConfidenceInterval(
                            sample_count=1, successes=1, lower=0.2065, upper=1.0
                        )
                    ),
                    insufficient_evidence_case_count=0,
                    complete_evidence_set_confidence=MetricConfidenceInterval(
                        sample_count=1, successes=1, lower=0.2065, upper=1.0
                    ),
                    p95_latency_ms=1.0,
                ),
            ),
            provenance_summary=BenchmarkProvenanceSummary(
                provenance_mode=BenchmarkProvenanceMode.INDEPENDENT_REVIEWED,
                case_count=1,
                adjudicated_case_count=1,
                automated_case_count=0,
                source_evidence_count=0,
                independent_review_count=2,
                disagreement_case_count=0,
                disagreement_rate=0.0,
            ),
            generated_at=now - timedelta(minutes=1),
            outcome="ACCEPTED",
        )
    )
    content = BenchmarkAcceptanceAttestationContent(
        acceptance_id="BA_LOCAL_EXERCISE_001",
        benchmark_suite_sha256=report.content.benchmark_suite_sha256,
        benchmark_report_sha256=report.report_sha256,
        runner_version=report.content.runner_version,
        candidate_configuration_sha256=report.content.candidate_configuration_sha256,
        corpus_release_id=report.content.corpus_release_id,
        manifest_sha256=report.content.manifest_sha256,
        vector_batch_sha256=report.content.vector_batch_sha256,
        qdrant_collection=report.content.qdrant_collection,
        index_attestation_sha256=index_attestation_sha256,
        holdout_access_policy_sha256="d" * 64,
        provenance_mode=BenchmarkProvenanceMode.INDEPENDENT_REVIEWED,
        adjudication_process_sha256="e" * 64,
        adjudication_record_sha256="6" * 64,
        threshold_policy_sha256="7" * 64,
        accepted_at=now,
        valid_until=now + timedelta(days=1),
    )
    attestations = SQLAttestationRepository(database)
    await attestations.register_key(
        key_id=BENCHMARK_SIGNER.key_id,
        signer_identity=BENCHMARK_SIGNER.signer_identity,
        public_key_pem=BENCHMARK_SIGNER.public_key_pem(),
        purposes=(BenchmarkAttestationPurpose.BENCHMARK_ACCEPTANCE,),
    )
    envelope = BENCHMARK_SIGNER.sign(canonical_json_bytes(content))
    await attestations.record_and_verify(
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
    await releases.register_benchmark_acceptance(
        bundle.manifest.content.corpus_release_id, acceptance=acceptance, report=report
    )
    return acceptance


async def activate(
    database: Database,
    releases: SQLCorpusReleaseRepository,
    bundle: CorpusReleaseBundle,
    *,
    collection: str,
    point_count: int,
    index_attestation_sha256: str,
    acceptance: SignedBenchmarkAcceptance,
):
    content = ActivationDecisionContent(
        corpus_release_id=bundle.manifest.content.corpus_release_id,
        manifest_sha256=bundle.manifest.manifest_sha256,
        release_policy_sha256=bundle.manifest.content.release_policy_sha256,
        qdrant_collection=collection,
        index_point_count=point_count,
        index_attestation_sha256=index_attestation_sha256,
        benchmark_acceptance_sha256=acceptance.statement_sha256,
        decided_at=utc_now(),
    )
    attestations = SQLAttestationRepository(database)
    envelope = ACTIVATION_SIGNER.sign(canonical_json_bytes(content))
    await attestations.record_and_verify(
        content,
        envelope,
        purpose=AttestationPurpose.ACTIVATION,
        predicate_type="https://med-rag.local/attestations/activation-decision",
    )
    return await releases.activate(
        bundle.manifest.content.corpus_release_id,
        decision=SignedActivationDecision.seal(
            content,
            signature_sha256=envelope.signature_sha256,
            signer_identity=envelope.signer_identity,
            signing_key_id=envelope.key_id,
        ),
    )


async def run(arguments: argparse.Namespace) -> int:
    bundle = CorpusReleaseBundle.model_validate_json(
        FIXTURE_BUNDLE.read_text(encoding="utf-8")
    )
    candidate = RetrievalCandidateManifest.model_validate_json(
        FIXTURE_CANDIDATE.read_text(encoding="utf-8")
    )
    print(
        f"candidate {candidate.content.candidate_id} builds "
        f"{candidate_qdrant_collection(bundle, candidate)}"
    )

    await ensure_database(
        arguments.admin_database_url, arguments.database, reset=arguments.reset
    )
    if arguments.reset:
        await drop_collection(arguments.qdrant_url, candidate_qdrant_collection(bundle, candidate))
    database = Database(f"{arguments.database_url_prefix}/{arguments.database}")
    qdrant = QdrantRESTClient(arguments.qdrant_url, timeout_seconds=arguments.qdrant_timeout)
    try:
        await database.create_schema_for_tests()
        releases = SQLCorpusReleaseRepository(database)
        backend = DeterministicHashingBackend(
            dense_dimension=DENSE_DIMENSION, sparse_dimension=SPARSE_DIMENSION
        )
        if await releases.active_serving_binding() is not None:
            # Already built by an earlier run. Re-running then exercises only the query
            # path, which is the part worth repeating; pass --reset to rebuild the rest.
            print("scratch release already active; exercising the query path only")
            return await ask(releases, qdrant, backend, candidate, arguments)

        await seed_source_rows(database)
        registered = await releases.register_candidate(bundle)
        print(
            f"release registered: {registered.corpus_release_id} "
            f"state={registered.state.value}"
        )

        # Pin generation time to the release itself. The batch digest covers it, the
        # collection metadata covers the batch digest, so a floating clock would make
        # every re-run disagree with the collection the previous one built.
        vectors = await VectorBatchProducer(backend).produce(
            bundle, generated_at=bundle.manifest.content.created_at
        )
        print(
            f"vectors produced: {len(vectors.content.records)} "
            f"batch={vectors.batch_sha256[:12]}"
        )

        report = await QdrantIndexService(qdrant).build(
            bundle,
            vectors,
            candidate,
            source_classes={"SRC_FIXTURE_001": "E1"},
            smoke_samples=2,
            smoke_limit=3,
        )
        print(
            f"collection validated: {report.content.qdrant_collection} "
            f"points={report.content.point_count}"
        )

        attestation_sha256 = await sign_index_attestation(database, bundle, report)
        await releases.mark_index_validated(
            bundle.manifest.content.corpus_release_id,
            point_count=report.content.point_count,
            index_attestation_sha256=attestation_sha256,
            qdrant_collection=report.content.qdrant_collection,
        )
        acceptance = await register_acceptance(
            database,
            releases,
            bundle,
            candidate,
            collection=report.content.qdrant_collection,
            vector_batch_sha256=vectors.batch_sha256,
            index_attestation_sha256=attestation_sha256,
        )
        active = await activate(
            database,
            releases,
            bundle,
            collection=report.content.qdrant_collection,
            point_count=report.content.point_count,
            index_attestation_sha256=attestation_sha256,
            acceptance=acceptance,
        )
        print(f"release activated: {active.corpus_release_id} -> {active.qdrant_collection}")

        return await ask(releases, qdrant, backend, candidate, arguments)
    finally:
        await qdrant.close()
        await database.close()


async def ask(
    releases: SQLCorpusReleaseRepository,
    qdrant: QdrantRESTClient,
    backend: DeterministicHashingBackend,
    candidate: RetrievalCandidateManifest,
    arguments: argparse.Namespace,
) -> int:
    """Put one question through the serving path the API uses."""

    binding = await releases.active_serving_binding()
    assert binding is not None, "activation did not produce a serving binding"
    engine = ServingRetrievalEngine(qdrant, backend, candidate, releases)
    mismatch = engine.binding_mismatch(binding)
    print(f"serving binding: {'OK' if mismatch is None else mismatch}")

    generation = StubGeneration()
    service = QuestionService(
        active_release_provider=releases.active_release,
        retrieval_engine=engine,
        answer_composer=GroundedAnswerComposer(generation),
    )
    accepted = await service.submit(
        QuestionCreate(
            question=arguments.question,
            source_filters=SourceFilters(jurisdictions=[arguments.jurisdiction]),
        )
    )
    statuses = []
    async for event in service.events(accepted.question_id):
        statuses.append(event.status.value)
        if event.status in TERMINAL_STATUSES:
            break
    result = service.result(accepted.question_id)
    await service.close()

    print(f"statuses: {' -> '.join(statuses)}")
    served = result.corpus_release.corpus_release_id if result.corpus_release else None
    print(f"release served: {served}")
    for claim in result.claims:
        print(f"claim {claim.claim_id} [{claim.verification_status}] cites {claim.evidence_ids}")
    for detail in result.evidence_details:
        print(f"evidence {detail.evidence_id}: {(detail.exact_text or '')[:70]}")
    if result.abstention is not None:
        print(f"abstained: {result.abstention.reason_code} - {result.abstention.message}")
    if generation.user_content:
        print("--- evidence block handed to the model ---")
        print(generation.user_content)
    return 0 if result.status is QuestionStatus.ANSWER_READY else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url-prefix", default="postgresql+asyncpg://medrag:medrag@localhost:5432"
    )
    parser.add_argument(
        "--admin-database-url",
        default="postgresql+asyncpg://medrag:medrag@localhost:5432/medrag",
    )
    parser.add_argument("--database", default="medrag_serving_exercise")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--qdrant-timeout", type=float, default=60.0)
    parser.add_argument("--jurisdiction", default="TEST")
    parser.add_argument(
        "--question",
        default="What does the synthetic guideline recommend in the outpatient setting?",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="drop and recreate the scratch database before running",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
