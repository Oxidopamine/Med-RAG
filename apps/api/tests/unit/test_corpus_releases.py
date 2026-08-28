import json
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import func, select

from app.corpus.releases import (
    MAX_TABLE_NEIGHBOUR_RADIUS,
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
    CorpusReleaseEvidenceRow,
    CorpusReleaseRow,
    OutboxEventRow,
    PublisherRow,
    SourceRow,
    SourceVersionRow,
    StewardArtifactRow,
)
from app.schemas.corpus import (
    ActivationDecisionContent,
    CorpusEvidenceRecord,
    CorpusReleaseBundle,
    CorpusReleaseManifestContent,
    LocatorKind,
    ReleaseState,
    SignedActivationDecision,
    SourceAnchor,
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


async def seed_page_artifact(database: Database) -> None:
    """The steward-store row `source_page_artifact` resolves the PDF bytes through."""

    async with database.session() as session:
        session.add(
            StewardArtifactRow(
                sha256="a" * 64,
                kind="NARRATIVE_SOURCE",
                byte_size=1,
                media_type="application/pdf",
                storage_key="sha256/aa/fixture.pdf",
                created_at=utc_now(),
            )
        )


async def test_source_page_artifact_is_withheld_until_the_page_licence_permits(
    tmp_path,
) -> None:
    """Page reproduction is refused under branch A and granted only by its own flag.

    This is the whole gate for the one route that reproduces a region of a source page,
    so it is asserted in both directions: that the excerpt permission alone does not open
    it, and that the page permission does.
    """

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'page-artifact.sqlite3'}")
    await database.create_schema_for_tests()
    await seed_fixture_source(database)
    await seed_page_artifact(database)
    repository = SQLCorpusReleaseRepository(database)
    bundle = load_fixture_bundle()
    registered = await repository.register_candidate(bundle)

    # Branch A exactly: quoting permitted, page reproduction withheld.
    async with database.session() as session:
        source = await session.get(SourceRow, "SRC_FIXTURE_001")
        assert source is not None
        source.license_excerpt_allowed = True
        source.license_render_allowed = False

    assert (
        await repository.source_page_artifact(
            registered.corpus_release_id, "SRC_FIXTURE_001"
        )
        is None
    )

    async with database.session() as session:
        source = await session.get(SourceRow, "SRC_FIXTURE_001")
        assert source is not None
        source.license_render_allowed = True

    granted = await repository.source_page_artifact(
        registered.corpus_release_id, "SRC_FIXTURE_001"
    )
    assert granted is not None
    assert granted.source_id == "SRC_FIXTURE_001"
    assert granted.artifact_sha256 == "a" * 64
    assert granted.storage_key == "sha256/aa/fixture.pdf"

    # A source that is licensed but contributes nothing to this release still has no page
    # here: membership is established through servable approved evidence, not the registry.
    assert (
        await repository.source_page_artifact(
            registered.corpus_release_id, "SRC_NOT_IN_RELEASE"
        )
        is None
    )
    assert await repository.source_page_artifact("CR_UNKNOWN", "SRC_FIXTURE_001") is None
    await database.close()


async def test_only_a_pdf_source_has_a_page_view(tmp_path) -> None:
    """A spreadsheet has no pages, and inventing some would misplace every citation.

    The renderer does not refuse a non-PDF - it rasterises one into hundreds of pages at a
    size and numbering that exist nowhere in the document. The DAK annexes are XLSX and are
    cited by table and cell, so a reader shown "page 12 of Annex B" would be reading a page
    number this system made up.
    """

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'page-media-type.sqlite3'}")
    await database.create_schema_for_tests()
    await seed_fixture_source(database)
    repository = SQLCorpusReleaseRepository(database)
    bundle = load_fixture_bundle()
    registered = await repository.register_candidate(bundle)

    async with database.session() as session:
        source = await session.get(SourceRow, "SRC_FIXTURE_001")
        assert source is not None
        source.license_render_allowed = True
        session.add(
            StewardArtifactRow(
                sha256="a" * 64,
                kind="NARRATIVE_SOURCE",
                byte_size=1,
                media_type=(
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ),
                storage_key="sha256/aa/fixture.xlsx",
                created_at=utc_now(),
            )
        )

    assert (
        await repository.source_page_artifact(
            registered.corpus_release_id, "SRC_FIXTURE_001"
        )
        is None
    )
    await database.close()


async def seed_table_rows(
    database: Database,
    corpus_release_id: str,
    rows: dict[int, str],
    *,
    table_id: str = "HIV.D",
) -> None:
    """Add workbook-row evidence to an already-registered release.

    The signed fixture bundle is a PDF source, and the corpus this serves is mostly
    spreadsheets, so the table cases need records the fixture does not contain. Building a
    second signed bundle to get them would test the release-registration path over again;
    what is under test here is the projection and the neighbourhood query, so the records
    are written the same way `register_candidate` writes them - digest included, which is
    what `_safe_evidence_detail` re-checks before serving any of them.
    """

    template = CorpusEvidenceRecord.model_validate(load_fixture_bundle().evidence[0])
    now = utc_now()
    async with database.session() as session:
        for row_index, text in rows.items():
            record = template.model_copy(
                update={
                    "evidence_id": f"EV_ROW_{row_index:03d}",
                    "corpus_release_id": corpus_release_id,
                    "content_exact": text,
                    "content_search": text.lower(),
                    "anchors": tuple(
                        SourceAnchor(
                            kind=LocatorKind.TABLE_CELL,
                            source_uri="source://SV_FIXTURE_2026/annex/B",
                            table_id=table_id,
                            row_index=row_index,
                            column_index=column_index,
                        )
                        for column_index in (0, 4)
                    ),
                }
            )
            session.add(
                CanonicalEvidenceRow(
                    evidence_id=record.evidence_id,
                    evidence_sha256=record.sha256,
                    source_id=record.source_id,
                    source_version_id=record.source_version_id,
                    approval_status=record.verification.approval_status.value,
                    payload=record.model_dump(mode="json"),
                    created_at=now,
                )
            )
            session.add(
                CorpusReleaseEvidenceRow(
                    corpus_release_id=corpus_release_id,
                    evidence_id=record.evidence_id,
                )
            )


async def registered_release_with_rows(tmp_path, name: str, rows: dict[int, str]):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / name}")
    await database.create_schema_for_tests()
    await seed_fixture_source(database)
    repository = SQLCorpusReleaseRepository(database)
    registered = await repository.register_candidate(load_fixture_bundle())
    await seed_table_rows(database, registered.corpus_release_id, rows)
    return database, repository, registered.corpus_release_id


async def test_section_path_is_derived_from_the_worksheet_an_anchor_names(tmp_path) -> None:
    """A workbook anchor already names its sheet, so the section is read, not invented.

    The canonical contract carries no section hierarchy, and the projection used to send an
    empty list for every record - which the interface displayed as "Section: not supplied",
    reading as data loss rather than as a field the corpus does not have. A `TABLE_CELL`
    anchor's `table_id` is the worksheet its row belongs to, which is what a reader means
    by the section of a spreadsheet source, and it is already sealed into the record.
    """

    database, repository, release_id = await registered_release_with_rows(
        tmp_path, "section-path.sqlite3", {145: "A146=HIV.D.DE12\nE146=Detectable"}
    )

    details = await repository.research_evidence_details(release_id, {"EV_ROW_145"})
    assert [detail.section_path for detail in details] == [["HIV.D"]]

    # A page number is not a section. Promoting one would manufacture a hierarchy the
    # document does not have, so a PDF record still reports none.
    pdf_details = await repository.research_evidence_details(
        release_id, {"EV_FIXTURE_PRIMARY_001"}
    )
    assert [detail.section_path for detail in pdf_details] == [[]]
    await database.close()


async def test_table_row_neighbourhood_returns_the_rows_around_a_cited_one(tmp_path) -> None:
    """A decision table is not decidable one row at a time.

    The row above opens the condition and the row below carries the exception, so a reader
    checking a citation against the published table is really checking whether the
    neighbours change what the cited row means.
    """

    database, repository, release_id = await registered_release_with_rows(
        tmp_path,
        "neighbourhood.sqlite3",
        {index: f"A{index + 1}=HIV.D.DE{index}" for index in range(140, 152)},
    )

    neighbours = await repository.table_row_neighbourhood(
        release_id, "SRC_FIXTURE_001", "HIV.D", 145, radius=2
    )

    # Reading order, which for a table is the row order the document has.
    assert [item.row_index for item in neighbours] == [143, 144, 145, 146, 147]
    assert [item.is_anchor_row for item in neighbours] == [False, False, True, False, False]
    # A neighbour is an evidence record like any other, projected through the same path as
    # a citation - so it carries its provenance and its licence answer, not bare text.
    anchored = neighbours[2]
    assert anchored.detail.evidence_id == "EV_ROW_145"
    assert anchored.detail.exact_text == "A146=HIV.D.DE145"
    assert anchored.detail.source_version_label == "2026"
    assert anchored.detail.section_path == ["HIV.D"]
    await database.close()


async def test_table_row_neighbourhood_clamps_the_window_at_the_start_of_a_table(
    tmp_path,
) -> None:
    database, repository, release_id = await registered_release_with_rows(
        tmp_path,
        "neighbourhood-start.sqlite3",
        {index: f"A{index + 1}=value" for index in range(0, 4)},
    )

    neighbours = await repository.table_row_neighbourhood(
        release_id, "SRC_FIXTURE_001", "HIV.D", 1, radius=3
    )

    assert [item.row_index for item in neighbours] == [0, 1, 2, 3]
    await database.close()


async def test_table_row_neighbourhood_carries_the_licence_the_answer_carried(
    tmp_path,
) -> None:
    """The window performs no licence act the answer did not already perform.

    A source whose licence withholds excerpts yields neighbours with their addresses - not
    a copyright act - and no text. That is the same answer the cited row itself gets, which
    is the point: a reader must not be able to reach through the neighbourhood for text the
    citation withheld.
    """

    database, repository, release_id = await registered_release_with_rows(
        tmp_path, "neighbourhood-licence.sqlite3", {145: "A146=Detectable"}
    )
    async with database.session() as session:
        source = await session.get(SourceRow, "SRC_FIXTURE_001")
        assert source is not None
        source.license_excerpt_allowed = False

    neighbours = await repository.table_row_neighbourhood(
        release_id, "SRC_FIXTURE_001", "HIV.D", 145, radius=1
    )

    assert len(neighbours) == 1
    assert neighbours[0].detail.exact_text is None
    assert neighbours[0].detail.render_allowed is False
    # The address survives, because a coordinate is not a reproduction.
    assert neighbours[0].detail.locators[0].table_id == "HIV.D"
    await database.close()


async def test_table_row_neighbourhood_answers_empty_for_what_it_will_not_serve(
    tmp_path,
) -> None:
    """One answer for every refusal: there is nothing here.

    Which rows a release does not contain is a fact about a corpus the caller has not been
    granted, so an unknown release, an unknown table, a source that is not in the release
    and a radius outside the permitted range are all the same empty window.
    """

    database, repository, release_id = await registered_release_with_rows(
        tmp_path, "neighbourhood-empty.sqlite3", {145: "A146=Detectable"}
    )

    async def window(**kwargs):
        arguments = {
            "corpus_release_id": release_id,
            "source_id": "SRC_FIXTURE_001",
            "table_id": "HIV.D",
            "row_index": 145,
            "radius": 1,
            **kwargs,
        }
        return await repository.table_row_neighbourhood(
            arguments["corpus_release_id"],
            arguments["source_id"],
            arguments["table_id"],
            arguments["row_index"],
            radius=arguments["radius"],
        )

    assert await window() != []
    assert await window(corpus_release_id="CR_UNKNOWN") == []
    assert await window(source_id="SRC_NOT_IN_RELEASE") == []
    assert await window(table_id="HIV.X") == []
    assert await window(row_index=900) == []
    assert await window(radius=-1) == []
    assert await window(radius=MAX_TABLE_NEIGHBOUR_RADIUS + 1) == []
    await database.close()


async def test_table_row_neighbourhood_will_not_serve_an_unservable_release(tmp_path) -> None:
    """Research serving lowers the activation bar, not the validation one."""

    database, repository, release_id = await registered_release_with_rows(
        tmp_path, "neighbourhood-state.sqlite3", {145: "A146=Detectable"}
    )
    async with database.session() as session:
        release = await session.get(CorpusReleaseRow, release_id)
        assert release is not None
        release.state = ReleaseState.CANDIDATE.value

    assert (
        await repository.table_row_neighbourhood(
            release_id, "SRC_FIXTURE_001", "HIV.D", 145, radius=1
        )
        == []
    )
    await database.close()
