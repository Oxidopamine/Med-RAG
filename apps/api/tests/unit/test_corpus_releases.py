import json
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import func, select

from app.corpus.releases import (
    RELEASABLE_SOURCE_STATES,
    CorpusReleaseConflictError,
    CorpusReleaseGateError,
    SQLCorpusReleaseRepository,
)
from app.corpus_steward.benchmark import BENCHMARK_RUNNER_VERSION
from app.corpus_steward.benchmark_schemas import (
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
from app.corpus_steward.crypto import Ed25519Signer
from app.corpus_steward.registry import SQLAttestationRepository
from app.corpus_steward.schemas import AttestationPurpose, BenchmarkAttestationPurpose
from app.persistence.database import Database
from app.persistence.models import (
    AcquisitionRow,
    ActiveCorpusReleaseRow,
    ArtifactRow,
    BenchmarkAcceptanceRow,
    CanonicalEvidenceRow,
    CorpusReleaseRow,
    OutboxEventRow,
    PublisherRow,
    SourceRow,
    SourceVersionRow,
)
from app.schemas.corpus import (
    ActivationDecisionContent,
    CorpusReleaseBundle,
    CorpusReleaseManifestContent,
    ReleaseState,
    SignedActivationDecision,
    canonical_json_bytes,
    canonical_sha256,
)
from app.schemas.domain import SERVABLE_LIFECYCLE_VALUES, SourceStatus, utc_now

FIXTURE_PATH = Path(__file__).parents[4] / "data" / "fixtures" / "corpus-release-v1.json"


def load_fixture_bundle() -> CorpusReleaseBundle:
    return CorpusReleaseBundle.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8"))


ACTIVATION_SIGNER = Ed25519Signer(
    key_id="fixture-key",
    signer_identity="fixture-control-plane",
    private_key=Ed25519PrivateKey.generate(),
)
BENCHMARK_SIGNER = Ed25519Signer(
    key_id="fixture-benchmark-key",
    signer_identity="fixture-benchmark-authority",
    private_key=Ed25519PrivateKey.generate(),
)


async def accepted_benchmark(
    database: Database,
    repository: SQLCorpusReleaseRepository,
    bundle: CorpusReleaseBundle,
    *,
    accepted_at_delta: timedelta = timedelta(0),
    valid_until_delta: timedelta = timedelta(days=1),
) -> SignedBenchmarkAcceptance:
    now = utc_now()
    accepted_at = now + accepted_at_delta
    report = BenchmarkReport.seal(
        BenchmarkReportContent(
            runner_version=BENCHMARK_RUNNER_VERSION,
            benchmark_id="fixture-sealed-holdout",
            suite_partition=BenchmarkSuitePartition.SEALED_HOLDOUT,
            provenance_mode=BenchmarkProvenanceMode.INDEPENDENT_REVIEWED,
            access_policy_sha256="d" * 64,
            adjudication_process_sha256="e" * 64,
            adjudication_record_sha256="6" * 64,
            threshold_policy_sha256="7" * 64,
            benchmark_suite_sha256="a" * 64,
            candidate_configuration_sha256="b" * 64,
            corpus_release_id=bundle.manifest.content.corpus_release_id,
            manifest_sha256=bundle.manifest.manifest_sha256,
            vector_batch_sha256="c" * 64,
            qdrant_collection=bundle.manifest.content.qdrant_collection,
            top_k=1,
            candidate_limit=1,
            rrf_k=60,
            rrf_weights=RRFWeights(),
            candidate_mode=RetrievalMode.HYBRID,
            case_results=(
                BenchmarkCaseResult(
                    case_id="BQ_ACCEPTANCE_001",
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
                    generation_context_budget=10,
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
            generated_at=accepted_at - timedelta(minutes=1),
            outcome="ACCEPTED",
        )
    )
    content = BenchmarkAcceptanceAttestationContent(
        acceptance_id="BA_FIXTURE_001",
        benchmark_suite_sha256=report.content.benchmark_suite_sha256,
        benchmark_report_sha256=report.report_sha256,
        runner_version=report.content.runner_version,
        candidate_configuration_sha256=report.content.candidate_configuration_sha256,
        corpus_release_id=report.content.corpus_release_id,
        manifest_sha256=report.content.manifest_sha256,
        vector_batch_sha256=report.content.vector_batch_sha256,
        qdrant_collection=report.content.qdrant_collection,
        index_attestation_sha256="f" * 64,
        holdout_access_policy_sha256="d" * 64,
        provenance_mode=BenchmarkProvenanceMode.INDEPENDENT_REVIEWED,
        adjudication_process_sha256="e" * 64,
        adjudication_record_sha256="6" * 64,
        threshold_policy_sha256="7" * 64,
        accepted_at=accepted_at,
        valid_until=now + valid_until_delta,
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
    await repository.register_benchmark_acceptance(
        bundle.manifest.content.corpus_release_id,
        acceptance=acceptance,
        report=report,
    )
    return acceptance


async def activation_decision(
    database: Database,
    bundle: CorpusReleaseBundle,
    *,
    index_point_count: int | None = None,
    benchmark_acceptance_sha256: str = "a" * 64,
) -> SignedActivationDecision:
    content = bundle.manifest.content
    repository = SQLAttestationRepository(database)
    await repository.register_key(
        key_id=ACTIVATION_SIGNER.key_id,
        signer_identity=ACTIVATION_SIGNER.signer_identity,
        public_key_pem=ACTIVATION_SIGNER.public_key_pem(),
        purposes=(AttestationPurpose.ACTIVATION,),
    )
    decision_content = ActivationDecisionContent(
        corpus_release_id=content.corpus_release_id,
        manifest_sha256=bundle.manifest.manifest_sha256,
        release_policy_sha256=content.release_policy_sha256,
        qdrant_collection=content.qdrant_collection,
        index_point_count=(
            len(bundle.evidence) if index_point_count is None else index_point_count
        ),
        index_attestation_sha256="f" * 64,
        benchmark_acceptance_sha256=benchmark_acceptance_sha256,
        decided_at=utc_now(),
    )
    envelope = ACTIVATION_SIGNER.sign(canonical_json_bytes(decision_content))
    await repository.record_and_verify(
        decision_content,
        envelope,
        purpose=AttestationPurpose.ACTIVATION,
        predicate_type="https://med-rag.local/attestations/activation-decision",
    )
    return SignedActivationDecision.seal(
        decision_content,
        signature_sha256=envelope.signature_sha256,
        signer_identity=envelope.signer_identity,
        signing_key_id=envelope.key_id,
    )


async def seed_fixture_source(database: Database) -> None:
    now = utc_now()
    async with database.session() as session:
        session.add(
            PublisherRow(
                publisher_id="PUB_FIXTURE",
                name="Fixture Publisher",
                created_at=now,
            )
        )
        session.add(
            SourceRow(
                source_id="SRC_FIXTURE_001",
                publisher_id="PUB_FIXTURE",
                title="Synthetic Guideline",
                source_class="E1",
                jurisdiction="TEST",
                canonical_url="https://fixtures.invalid/synthetic-guideline",
                # A fully licensed source: both the excerpt and the page-reproduction
                # permission. They are separate columns because branch A of the WHO
                # decision grants the first and withholds the second.
                license_excerpt_allowed=True,
                license_render_allowed=True,
                created_at=now,
            )
        )
        session.add(
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
            )
        )
        session.add(
            ArtifactRow(
                artifact_id="ART_FIXTURE",
                sha256="a" * 64,
                byte_size=1,
                media_type="application/pdf",
                storage_key="sha256/aa/fixture.pdf",
                created_at=now,
            )
        )
        session.add(
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
            )
        )


async def test_release_requires_validated_index_before_atomic_activation(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'corpus.sqlite3'}")
    await database.create_schema_for_tests()
    await seed_fixture_source(database)
    repository = SQLCorpusReleaseRepository(database)
    bundle = load_fixture_bundle()

    registered = await repository.register_candidate(bundle)

    assert registered.state is ReleaseState.VALIDATED
    assert registered.evidence_count == 3
    assert await repository.active_release() is None
    with pytest.raises(CorpusReleaseGateError, match="RETRIEVAL_INDEX_NOT_VALIDATED"):
        await repository.activate(
            registered.corpus_release_id,
            decision=await activation_decision(database, bundle),
        )
    with pytest.raises(CorpusReleaseGateError, match="INDEX_EVIDENCE_COUNT_MISMATCH"):
        await repository.mark_index_validated(
            registered.corpus_release_id,
            point_count=2,
            index_attestation_sha256="f" * 64,
        )

    indexed = await repository.mark_index_validated(
        registered.corpus_release_id,
        point_count=3,
        index_attestation_sha256="f" * 64,
    )
    with pytest.raises(CorpusReleaseGateError, match="BENCHMARK_ACCEPTANCE_MISSING"):
        await repository.activate(
            registered.corpus_release_id,
            decision=await activation_decision(database, bundle),
        )
    acceptance = await accepted_benchmark(database, repository, bundle)
    unsigned_content = ActivationDecisionContent(
        corpus_release_id=bundle.manifest.content.corpus_release_id,
        manifest_sha256=bundle.manifest.manifest_sha256,
        release_policy_sha256=bundle.manifest.content.release_policy_sha256,
        qdrant_collection=bundle.manifest.content.qdrant_collection,
        index_point_count=3,
        index_attestation_sha256="f" * 64,
        benchmark_acceptance_sha256=acceptance.statement_sha256,
        decided_at=utc_now(),
    )
    unsigned_decision = SignedActivationDecision.seal(
        unsigned_content,
        signature_sha256="e" * 64,
        signer_identity="untrusted-control-plane",
        signing_key_id="unregistered-key",
    )
    with pytest.raises(CorpusReleaseGateError, match="ACTIVATION_SIGNATURE_NOT_VERIFIED"):
        await repository.activate(
            registered.corpus_release_id,
            decision=unsigned_decision,
        )
    wrong_decision = await activation_decision(
        database,
        bundle,
        index_point_count=2,
        benchmark_acceptance_sha256=acceptance.statement_sha256,
    )
    with pytest.raises(CorpusReleaseGateError, match="ACTIVATION_INDEX_COUNT_MISMATCH"):
        await repository.activate(
            registered.corpus_release_id,
            decision=wrong_decision,
        )
    with pytest.raises(
        CorpusReleaseGateError, match="ACTIVATION_BENCHMARK_ACCEPTANCE_MISMATCH"
    ):
        await repository.activate(
            registered.corpus_release_id,
            decision=await activation_decision(
                database,
                bundle,
                benchmark_acceptance_sha256="9" * 64,
            ),
        )
    active = await repository.activate(
        registered.corpus_release_id,
        decision=await activation_decision(
            database,
            bundle,
            benchmark_acceptance_sha256=acceptance.statement_sha256,
        ),
    )

    assert indexed.index_status == "VALIDATED"
    assert active.corpus_release_id == registered.corpus_release_id
    assert active.manifest_sha256 == registered.manifest_sha256
    assert await repository.active_release() == active
    async with database.session() as session:
        pointer = await session.get(ActiveCorpusReleaseRow, 1)
        release = await session.get(CorpusReleaseRow, registered.corpus_release_id)
        outbox_count = await session.scalar(select(func.count()).select_from(OutboxEventRow))
    assert pointer is not None
    assert release is not None and release.state == ReleaseState.ACTIVE.value
    assert outbox_count == 4
    await database.close()


async def test_index_validation_selects_one_candidate_profile_collection(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'candidate-index.sqlite3'}")
    await database.create_schema_for_tests()
    await seed_fixture_source(database)
    repository = SQLCorpusReleaseRepository(database)
    bundle = load_fixture_bundle()
    registered = await repository.register_candidate(bundle)
    candidate_collection = f"{registered.qdrant_collection}--vp-{'1' * 24}"

    indexed = await repository.mark_index_validated(
        registered.corpus_release_id,
        point_count=len(bundle.evidence),
        index_attestation_sha256="f" * 64,
        qdrant_collection=candidate_collection,
    )

    assert indexed.qdrant_collection == candidate_collection
    with pytest.raises(CorpusReleaseConflictError):
        await repository.mark_index_validated(
            registered.corpus_release_id,
            point_count=len(bundle.evidence),
            index_attestation_sha256="f" * 64,
            qdrant_collection=f"{candidate_collection}--vp-{'2' * 24}",
        )
    await database.close()


async def test_stale_or_tampered_benchmark_acceptance_cannot_activate(tmp_path) -> None:
    stale_database = Database(f"sqlite+aiosqlite:///{tmp_path / 'stale.sqlite3'}")
    await stale_database.create_schema_for_tests()
    await seed_fixture_source(stale_database)
    stale_repository = SQLCorpusReleaseRepository(stale_database)
    bundle = load_fixture_bundle()
    registered = await stale_repository.register_candidate(bundle)
    await stale_repository.mark_index_validated(
        registered.corpus_release_id,
        point_count=len(bundle.evidence),
        index_attestation_sha256="f" * 64,
    )
    with pytest.raises(CorpusReleaseGateError, match="BENCHMARK_ACCEPTANCE_STALE"):
        await accepted_benchmark(
            stale_database,
            stale_repository,
            bundle,
            accepted_at_delta=timedelta(days=-2),
            valid_until_delta=timedelta(days=-1),
        )
    await stale_database.close()

    tampered_database = Database(f"sqlite+aiosqlite:///{tmp_path / 'tampered.sqlite3'}")
    await tampered_database.create_schema_for_tests()
    await seed_fixture_source(tampered_database)
    tampered_repository = SQLCorpusReleaseRepository(tampered_database)
    registered = await tampered_repository.register_candidate(bundle)
    await tampered_repository.mark_index_validated(
        registered.corpus_release_id,
        point_count=len(bundle.evidence),
        index_attestation_sha256="f" * 64,
    )
    acceptance = await accepted_benchmark(
        tampered_database, tampered_repository, bundle
    )
    async with tampered_database.session() as session:
        row = await session.get(BenchmarkAcceptanceRow, acceptance.content.acceptance_id)
        assert row is not None
        payload = dict(row.payload)
        payload["holdout_access_policy_sha256"] = "0" * 64
        row.payload = payload
    with pytest.raises(CorpusReleaseGateError, match="BENCHMARK_ACCEPTANCE_TAMPERED"):
        await tampered_repository.activate(
            registered.corpus_release_id,
            decision=await activation_decision(
                tampered_database,
                bundle,
                benchmark_acceptance_sha256=acceptance.statement_sha256,
            ),
        )
    await tampered_database.close()


async def test_candidate_registration_is_idempotent_and_content_is_immutable(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'immutable.sqlite3'}")
    await database.create_schema_for_tests()
    await seed_fixture_source(database)
    repository = SQLCorpusReleaseRepository(database)
    bundle = load_fixture_bundle()

    first = await repository.register_candidate(bundle)
    second = await repository.register_candidate(bundle)

    assert second == first
    async with database.session() as session:
        outbox_count = await session.scalar(select(func.count()).select_from(OutboxEventRow))
    assert outbox_count == 1

    changed = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    changed["manifest"]["content"]["created_at"] = "2026-08-25T00:00:01Z"
    content = changed["manifest"]["content"]
    changed["manifest"]["manifest_sha256"] = canonical_sha256(
        CorpusReleaseManifestContent.model_validate(content)
    )
    conflicting = CorpusReleaseBundle.model_validate(changed)
    with pytest.raises(CorpusReleaseConflictError, match="different manifest"):
        await repository.register_candidate(conflicting)

    await database.close()


async def test_evidence_details_only_resolve_from_the_current_active_release(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'evidence-details.sqlite3'}")
    await database.create_schema_for_tests()
    await seed_fixture_source(database)
    repository = SQLCorpusReleaseRepository(database)
    bundle = load_fixture_bundle()
    registered = await repository.register_candidate(bundle)

    requested_ids = {"EV_FIXTURE_PRIMARY_001", "EV_NOT_IN_CANONICAL_CORPUS"}
    assert await repository.evidence_details(registered.corpus_release_id, requested_ids) == []

    await repository.mark_index_validated(
        registered.corpus_release_id,
        point_count=len(bundle.evidence),
        index_attestation_sha256="f" * 64,
    )
    acceptance = await accepted_benchmark(database, repository, bundle)
    await repository.activate(
        registered.corpus_release_id,
        decision=await activation_decision(
            database,
            bundle,
            benchmark_acceptance_sha256=acceptance.statement_sha256,
        ),
    )

    details = await repository.evidence_details(registered.corpus_release_id, requested_ids)

    assert len(details) == 1
    detail = details[0]
    assert detail.evidence_id == "EV_FIXTURE_PRIMARY_001"
    assert detail.exact_text == (
        "For adults in the synthetic test population, offer Example Intervention A."
    )
    assert detail.evidence_type is None
    assert detail.evidence_roles == ["PRIMARY_SUPPORT"]
    assert detail.section_path == []
    assert detail.source_id == "SRC_FIXTURE_001"
    assert detail.source_version_id == "SV_FIXTURE_2026"
    assert detail.source_title == "Synthetic Guideline"
    assert detail.source_version_label == "2026"
    assert detail.publisher_name == "Fixture Publisher"
    assert detail.jurisdiction == "TEST"
    assert detail.approval_status == "APPROVED"
    assert detail.render_allowed is True
    assert len(detail.locators) == 1
    assert detail.locators[0].pdf_page == 1
    assert detail.locators[0].exact_highlight_available is True
    await database.close()


async def test_evidence_detail_resolution_honors_license_and_fails_closed(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'evidence-gates.sqlite3'}")
    await database.create_schema_for_tests()
    await seed_fixture_source(database)
    repository = SQLCorpusReleaseRepository(database)
    bundle = load_fixture_bundle()
    registered = await repository.register_candidate(bundle)
    await repository.mark_index_validated(
        registered.corpus_release_id,
        point_count=len(bundle.evidence),
        index_attestation_sha256="f" * 64,
    )
    acceptance = await accepted_benchmark(database, repository, bundle)
    await repository.activate(
        registered.corpus_release_id,
        decision=await activation_decision(
            database,
            bundle,
            benchmark_acceptance_sha256=acceptance.statement_sha256,
        ),
    )

    # Branch A of the rendering-licence decision: the passage may be quoted, a region of
    # the page may not be reproduced. Withdrawing only the page permission must leave the
    # excerpt intact, or the decision cannot be expressed at all.
    async with database.session() as session:
        source = await session.get(SourceRow, "SRC_FIXTURE_001")
        assert source is not None
        source.license_render_allowed = False

    excerpt_only = await repository.evidence_details(
        registered.corpus_release_id,
        {"EV_FIXTURE_PRIMARY_001"},
    )
    assert len(excerpt_only) == 1
    assert excerpt_only[0].render_allowed is True
    assert excerpt_only[0].exact_text is not None
    assert excerpt_only[0].locators[0].exact_highlight_available is False

    # Withdrawing the excerpt permission withdraws the page one with it: a page whose
    # text may not be quoted cannot have that text reproduced as a picture instead.
    async with database.session() as session:
        source = await session.get(SourceRow, "SRC_FIXTURE_001")
        assert source is not None
        source.license_excerpt_allowed = False
        source.license_render_allowed = True

    restricted = await repository.evidence_details(
        registered.corpus_release_id,
        {"EV_FIXTURE_PRIMARY_001"},
    )
    assert len(restricted) == 1
    assert restricted[0].render_allowed is False
    assert restricted[0].exact_text is None
    assert restricted[0].locators[0].exact_highlight_available is False

    async with database.session() as session:
        evidence = await session.get(CanonicalEvidenceRow, "EV_FIXTURE_PRIMARY_001")
        assert evidence is not None
        evidence.payload = {"malformed": "canonical payload"}

    assert (
        await repository.evidence_details(
            registered.corpus_release_id,
            {"EV_FIXTURE_PRIMARY_001"},
        )
        == []
    )
    await database.close()


def test_servable_states_are_a_subset_of_releasable_states() -> None:
    """Nothing may be served that could not have been released.

    Two independent literals previously answered "may this be served?" - retrieval
    admitted EFFECTIVE only while evidence-detail resolution also admitted APPROVED and
    PARTIALLY_SUPERSEDED. With one HIV source version that was inert; with a second
    edition the two would have disagreed about the same record, and a detail rendering
    for a record retrieval refuses to return is the system contradicting itself about
    what is current.
    """

    assert SERVABLE_LIFECYCLE_VALUES <= RELEASABLE_SOURCE_STATES
    # The direction that matters: a superseded or withdrawn source is never servable.
    assert SourceStatus.SUPERSEDED.value not in SERVABLE_LIFECYCLE_VALUES
    assert SourceStatus.WITHDRAWN.value not in SERVABLE_LIFECYCLE_VALUES
    assert SourceStatus.PARTIALLY_SUPERSEDED.value not in SERVABLE_LIFECYCLE_VALUES
    # Approved is not in force. Releasable, never servable.
    assert SourceStatus.APPROVED.value in RELEASABLE_SOURCE_STATES
    assert SourceStatus.APPROVED.value not in SERVABLE_LIFECYCLE_VALUES


def test_retrieval_and_detail_resolution_share_one_definition() -> None:
    """The serving path and the corpus layer must read the same constant, not copies."""

    from app.reasoning.retrieval_service import (
        SERVABLE_LIFECYCLE_STATES as retrieval_states,
    )

    assert {status.value for status in retrieval_states} == set(SERVABLE_LIFECYCLE_VALUES)
