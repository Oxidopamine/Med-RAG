import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.corpus_steward.adjudication_repository import (
    AdjudicationRepositoryConflictError,
    AdjudicationRepositoryError,
    SQLBenchmarkAdjudicationRepository,
)
from app.corpus_steward.adjudication_schemas import (
    AdjudicationProcessPolicyContent,
    AdjudicationSealRequest,
    BenchmarkAccessPolicyContent,
    BenchmarkSuiteBuildRequest,
    BenchmarkThresholdPolicyContent,
    ClinicalReviewDecision,
    ClinicalReviewDecisionContent,
    ClinicalReviewImport,
    DisagreementResolution,
    DisagreementResolutionContent,
    DisagreementResolutionImport,
    SafetyTopicSampleTarget,
)
from app.corpus_steward.adjudication_service import (
    AdjudicationWorkflowError,
    BenchmarkAccessDeniedError,
    ClinicalAdjudicationService,
)
from app.corpus_steward.benchmark_schemas import (
    BenchmarkAcceptance,
    BenchmarkCase,
    BenchmarkFilter,
    BenchmarkGoldEvidence,
    BenchmarkSuitePartition,
    ClinicalSafetyTopic,
    SafetyStratumThreshold,
)
from app.corpus_steward.cli import benchmark_adjudication_schema_documents
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.persistence.database import Database
from app.persistence.models import CorpusReleaseRow
from app.schemas.corpus import CorpusReleaseBundle, EvidenceRole

FIXTURE_PATH = Path(__file__).parents[4] / "data" / "fixtures" / "corpus-release-v1.json"
SCHEMA_ROOT = Path(__file__).parents[4] / "packages" / "schemas"
NOW = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)


def fixture_bundle() -> CorpusReleaseBundle:
    return CorpusReleaseBundle.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8"))


def clinical_case(case_id: str, *, question: str) -> BenchmarkCase:
    return BenchmarkCase(
        case_id=case_id,
        question=question,
        gold_evidence=(
            BenchmarkGoldEvidence(evidence_id="EV_FIXTURE_PRIMARY_001"),
        ),
        required_evidence_roles=(EvidenceRole.PRIMARY_SUPPORT,),
        retrieval_filter=BenchmarkFilter(jurisdictions=("TEST",)),
        strata={"clinical_risk": "high", "language": "en"},
        safety_topics=tuple(ClinicalSafetyTopic),
    )


def acceptance() -> BenchmarkAcceptance:
    return BenchmarkAcceptance(
        minimum_total_case_count=1,
        maximum_p95_latency_ms=60_000,
        safety_strata=tuple(
            SafetyStratumThreshold(
                stratum_key="safety_topic",
                stratum_value=topic.value,
                minimum_case_count=1,
            )
            for topic in ClinicalSafetyTopic
        ),
    )


def threshold_policy() -> BenchmarkThresholdPolicyContent:
    return BenchmarkThresholdPolicyContent(
        policy_id="clinical-thresholds",
        revision="2026-08-26",
        development_minimum_case_count=1,
        sealed_holdout_minimum_case_count=1,
        safety_topic_sample_targets=tuple(
            SafetyTopicSampleTarget(
                safety_topic=topic,
                development_minimum=1,
                sealed_holdout_minimum=1,
            )
            for topic in ClinicalSafetyTopic
        ),
        minimum_exact_agreement_rate=0.0,
        development_acceptance=acceptance(),
        sealed_holdout_acceptance=acceptance(),
        established_at=NOW,
    )


def review(
    *,
    review_id: str,
    case: BenchmarkCase,
    partition: BenchmarkSuitePartition,
    identity: str,
    access_sha256: str,
    instructions_sha256: str,
    decided_at: datetime,
) -> ClinicalReviewDecision:
    return ClinicalReviewDecision.seal(
        ClinicalReviewDecisionContent(
            review_id=review_id,
            case_id=case.case_id,
            suite_partition=partition,
            adjudicator_identity=identity,
            clinical_role="consultant physician",
            instructions_sha256=instructions_sha256,
            evidence_access_revision_sha256=access_sha256,
            decided_case=case,
            decided_at=decided_at,
        )
    )


async def setup_service(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'adjudication.sqlite3'}")
    await database.create_schema_for_tests()
    artifacts = ImmutableStewardArtifactStore(tmp_path / "artifacts")
    repository = SQLBenchmarkAdjudicationRepository(database, artifacts)
    service = ClinicalAdjudicationService(repository, artifacts)
    bundle = fixture_bundle()
    manifest = bundle.manifest.content
    async with database.session() as session:
        session.add(
            CorpusReleaseRow(
                corpus_release_id=manifest.corpus_release_id,
                contract_version=manifest.schema_version,
                manifest_sha256=bundle.manifest.manifest_sha256,
                manifest=bundle.manifest.model_dump(mode="json"),
                state="VALIDATED",
                previous_release_id=manifest.previous_release_id,
                qdrant_collection=manifest.qdrant_collection,
                cutoff_at=manifest.cutoff_at,
                index_status="NOT_BUILT",
                index_point_count=None,
                index_attestation_sha256=None,
                index_validated_at=None,
                benchmark_acceptance_sha256=None,
                benchmark_accepted_at=None,
                benchmark_valid_until=None,
                validated_at=manifest.created_at,
                activated_at=None,
                activated_by=None,
                activation_decision_sha256=None,
                created_at=manifest.created_at,
            )
        )
    return database, artifacts, repository, service, bundle


async def prepare_adjudication(tmp_path):
    database, artifacts, repository, service, bundle = await setup_service(tmp_path)
    access = await service.seal_access_policy(
        BenchmarkAccessPolicyContent(
            policy_id="clinical-benchmark-access",
            revision="2026-08-26",
            development_builder_identities=("candidate-developer",),
            holdout_custodian_identities=("holdout-custodian",),
            candidate_team_identities=("candidate-developer",),
            effective_at=NOW,
        )
    )
    process = await service.seal_adjudication_process(
        AdjudicationProcessPolicyContent(
            policy_id="clinical-adjudication",
            revision="2026-08-26",
            instructions_sha256="a" * 64,
            minimum_independent_reviews_per_case=2,
            allowed_clinical_roles=("consultant physician",),
            allowed_resolver_roles=("clinical adjudication chair",),
            effective_at=NOW,
        )
    )
    thresholds = await service.seal_threshold_policy(threshold_policy())

    development = clinical_case("BQ_DEV_001", question="What treatment is recommended?")
    holdout_a = clinical_case("BQ_HOLDOUT_001", question="When is treatment recommended?")
    holdout_b = holdout_a.model_copy(
        update={"question": "When should the treatment be offered?"}
    )
    decisions = (
        review(
            review_id="REV_DEV_1",
            case=development,
            partition=BenchmarkSuitePartition.DEVELOPMENT,
            identity="clinician-one",
            access_sha256=access.policy_sha256,
            instructions_sha256=process.content.instructions_sha256,
            decided_at=NOW + timedelta(hours=1),
        ),
        review(
            review_id="REV_DEV_2",
            case=development,
            partition=BenchmarkSuitePartition.DEVELOPMENT,
            identity="clinician-two",
            access_sha256=access.policy_sha256,
            instructions_sha256=process.content.instructions_sha256,
            decided_at=NOW + timedelta(hours=1),
        ),
        review(
            review_id="REV_HOLDOUT_1",
            case=holdout_a,
            partition=BenchmarkSuitePartition.SEALED_HOLDOUT,
            identity="clinician-one-holdout",
            access_sha256=access.policy_sha256,
            instructions_sha256=process.content.instructions_sha256,
            decided_at=NOW + timedelta(hours=1),
        ),
        review(
            review_id="REV_HOLDOUT_2",
            case=holdout_b,
            partition=BenchmarkSuitePartition.SEALED_HOLDOUT,
            identity="clinician-two-holdout",
            access_sha256=access.policy_sha256,
            instructions_sha256=process.content.instructions_sha256,
            decided_at=NOW + timedelta(hours=1),
        ),
    )
    await service.import_review_decisions(
        ClinicalReviewImport(decisions=decisions),
        access_policy_sha256=access.policy_sha256,
        adjudication_process_sha256=process.policy_sha256,
    )
    resolution = DisagreementResolution.seal(
        DisagreementResolutionContent(
            resolution_id="RES_HOLDOUT_1",
            case_id=holdout_a.case_id,
            review_decision_sha256s=(
                decisions[2].decision_sha256,
                decisions[3].decision_sha256,
            ),
            resolver_identity="adjudication-chair",
            resolver_clinical_role="clinical adjudication chair",
            resolution_note_sha256="b" * 64,
            finalized_case=holdout_a,
            resolved_at=NOW + timedelta(hours=2),
        )
    )
    await service.import_disagreement_resolutions(
        DisagreementResolutionImport(resolutions=(resolution,)),
        access_policy_sha256=access.policy_sha256,
        adjudication_process_sha256=process.policy_sha256,
    )
    record = await service.seal_adjudication(
        AdjudicationSealRequest(
            adjudication_record_id="ADJ_CLINICAL_V1",
            access_policy_sha256=access.policy_sha256,
            adjudication_process_sha256=process.policy_sha256,
            case_ids=(development.case_id, holdout_a.case_id),
            sealed_at=NOW + timedelta(hours=3),
        )
    )
    return (
        database,
        artifacts,
        repository,
        service,
        bundle,
        access,
        thresholds,
        record,
    )


async def test_adjudication_workflow_builds_disjoint_development_and_holdout_suites(
    tmp_path,
) -> None:
    (
        database,
        _artifacts,
        repository,
        service,
        bundle,
        access,
        thresholds,
        record,
    ) = await prepare_adjudication(tmp_path)
    try:
        development = await service.build_suite(
            BenchmarkSuiteBuildRequest(
                benchmark_id="clinical-development-v1",
                suite_partition=BenchmarkSuitePartition.DEVELOPMENT,
                adjudication_record_sha256=record.adjudication_record_sha256,
                threshold_policy_sha256=thresholds.policy_sha256,
                candidate_configuration_sha256="c" * 64,
                corpus_release_id=bundle.manifest.content.corpus_release_id,
                manifest_sha256=bundle.manifest.manifest_sha256,
                actor_identity="candidate-developer",
                requested_at=NOW + timedelta(hours=4),
                top_k=1,
                candidate_limit=1,
            ),
            bundle,
        )
        holdout_request = BenchmarkSuiteBuildRequest(
            benchmark_id="clinical-holdout-v1",
            suite_partition=BenchmarkSuitePartition.SEALED_HOLDOUT,
            adjudication_record_sha256=record.adjudication_record_sha256,
            threshold_policy_sha256=thresholds.policy_sha256,
            candidate_configuration_sha256="c" * 64,
            corpus_release_id=bundle.manifest.content.corpus_release_id,
            manifest_sha256=bundle.manifest.manifest_sha256,
            actor_identity="holdout-custodian",
            requested_at=NOW + timedelta(hours=4),
            top_k=1,
            candidate_limit=1,
        )
        holdout = await service.build_suite(holdout_request, bundle)

        assert {item.case_id for item in development.suite.content.cases} == {"BQ_DEV_001"}
        assert {item.case_id for item in holdout.suite.content.cases} == {"BQ_HOLDOUT_001"}
        assert development.suite.content.adjudication_record_sha256 == (
            record.adjudication_record_sha256
        )
        assert holdout.suite.content.access_policy_sha256 == access.policy_sha256
        assert holdout.suite.content.threshold_policy_sha256 == thresholds.policy_sha256
        assert holdout.suite.content.cases[0].adjudication is not None
        assert holdout.suite.content.cases[0].adjudication.disagreement_observed is True
        assert await service.build_suite(holdout_request, bundle) == holdout
        assert await repository.holdout_suite_for_record(
            record.adjudication_record_sha256
        ) == holdout
    finally:
        await database.close()


async def test_holdout_access_and_one_build_only_are_enforced(tmp_path) -> None:
    (
        database,
        _artifacts,
        _repository,
        service,
        bundle,
        _access,
        thresholds,
        record,
    ) = await prepare_adjudication(tmp_path)
    try:
        common = dict(
            suite_partition=BenchmarkSuitePartition.SEALED_HOLDOUT,
            adjudication_record_sha256=record.adjudication_record_sha256,
            threshold_policy_sha256=thresholds.policy_sha256,
            corpus_release_id=bundle.manifest.content.corpus_release_id,
            manifest_sha256=bundle.manifest.manifest_sha256,
            requested_at=NOW + timedelta(hours=4),
            top_k=1,
            candidate_limit=1,
        )
        with pytest.raises(BenchmarkAccessDeniedError, match="may not build"):
            await service.build_suite(
                BenchmarkSuiteBuildRequest(
                    benchmark_id="clinical-holdout-v1",
                    candidate_configuration_sha256="c" * 64,
                    actor_identity="candidate-developer",
                    **common,
                ),
                bundle,
            )
        await service.build_suite(
            BenchmarkSuiteBuildRequest(
                benchmark_id="clinical-holdout-v1",
                candidate_configuration_sha256="c" * 64,
                actor_identity="holdout-custodian",
                **common,
            ),
            bundle,
        )
        with pytest.raises(AdjudicationRepositoryConflictError, match="different inputs"):
            await service.build_suite(
                BenchmarkSuiteBuildRequest(
                    benchmark_id="clinical-holdout-v2",
                    candidate_configuration_sha256="d" * 64,
                    actor_identity="holdout-custodian",
                    **common,
                ),
                bundle,
            )
    finally:
        await database.close()


async def test_sealing_rejects_unresolved_disagreement(tmp_path) -> None:
    database, _artifacts, _repository, service, _bundle = await setup_service(tmp_path)
    try:
        access = await service.seal_access_policy(
            BenchmarkAccessPolicyContent(
                policy_id="access",
                revision="1",
                development_builder_identities=("developer",),
                holdout_custodian_identities=("custodian",),
                effective_at=NOW,
            )
        )
        process = await service.seal_adjudication_process(
            AdjudicationProcessPolicyContent(
                policy_id="process",
                revision="1",
                instructions_sha256="a" * 64,
                allowed_clinical_roles=("consultant physician",),
                allowed_resolver_roles=("clinical adjudication chair",),
                effective_at=NOW,
            )
        )
        first = clinical_case("BQ_DISPUTED", question="First interpretation?")
        second = first.model_copy(update={"question": "Second interpretation?"})
        decisions = tuple(
            review(
                review_id=f"REV_DISPUTED_{index}",
                case=case,
                partition=BenchmarkSuitePartition.DEVELOPMENT,
                identity=f"clinician-{index}",
                access_sha256=access.policy_sha256,
                instructions_sha256=process.content.instructions_sha256,
                decided_at=NOW + timedelta(hours=1),
            )
            for index, case in enumerate((first, second), start=1)
        )
        await service.import_review_decisions(
            ClinicalReviewImport(decisions=decisions),
            access_policy_sha256=access.policy_sha256,
            adjudication_process_sha256=process.policy_sha256,
        )

        with pytest.raises(AdjudicationWorkflowError, match="unresolved"):
            await service.seal_adjudication(
                AdjudicationSealRequest(
                    adjudication_record_id="ADJ_DISPUTED",
                    access_policy_sha256=access.policy_sha256,
                    adjudication_process_sha256=process.policy_sha256,
                    case_ids=(first.case_id,),
                    sealed_at=NOW + timedelta(hours=2),
                )
            )
    finally:
        await database.close()


async def test_repository_detects_suite_artifact_tampering(tmp_path) -> None:
    (
        database,
        artifacts,
        repository,
        service,
        bundle,
        _access,
        thresholds,
        record,
    ) = await prepare_adjudication(tmp_path)
    try:
        result = await service.build_suite(
            BenchmarkSuiteBuildRequest(
                benchmark_id="clinical-development-v1",
                suite_partition=BenchmarkSuitePartition.DEVELOPMENT,
                adjudication_record_sha256=record.adjudication_record_sha256,
                threshold_policy_sha256=thresholds.policy_sha256,
                candidate_configuration_sha256="c" * 64,
                corpus_release_id=bundle.manifest.content.corpus_release_id,
                manifest_sha256=bundle.manifest.manifest_sha256,
                actor_identity="candidate-developer",
                requested_at=NOW + timedelta(hours=4),
                top_k=1,
                candidate_limit=1,
            ),
            bundle,
        )
        artifacts.path_for(result.storage_key).write_bytes(b"tampered")

        with pytest.raises(AdjudicationRepositoryError, match="tampered"):
            await repository.suite_for_benchmark(
                "clinical-development-v1", BenchmarkSuitePartition.DEVELOPMENT
            )
    finally:
        await database.close()


def test_threshold_policy_requires_every_safety_topic() -> None:
    with pytest.raises(ValueError, match="must target every safety topic"):
        BenchmarkThresholdPolicyContent(
            policy_id="incomplete",
            revision="1",
            development_minimum_case_count=1,
            sealed_holdout_minimum_case_count=1,
            safety_topic_sample_targets=(
                SafetyTopicSampleTarget(
                    safety_topic=ClinicalSafetyTopic.DOSE,
                    development_minimum=1,
                    sealed_holdout_minimum=1,
                ),
            ),
            development_acceptance=acceptance(),
            sealed_holdout_acceptance=acceptance(),
            established_at=NOW,
        )


def test_checked_in_adjudication_schemas_match_contracts() -> None:
    for filename, expected in benchmark_adjudication_schema_documents().items():
        actual = json.loads((SCHEMA_ROOT / filename).read_text(encoding="utf-8"))
        assert actual == expected
