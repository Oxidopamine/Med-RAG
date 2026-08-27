import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.corpus_steward.adjudication_repository import SQLBenchmarkAdjudicationRepository
from app.corpus_steward.adjudication_schemas import (
    BenchmarkAccessPolicyContent,
    BenchmarkThresholdPolicyContent,
    SafetyTopicSampleTarget,
    ThresholdDerivation,
)
from app.corpus_steward.adjudication_service import ClinicalAdjudicationService
from app.corpus_steward.benchmark import derive_development_suite_for_candidate
from app.corpus_steward.benchmark_schemas import (
    BenchmarkAcceptance,
    BenchmarkProvenanceMode,
    ClinicalSafetyTopic,
    SafetyStratumThreshold,
)
from app.corpus_steward.candidate_schemas import RetrievalCandidateManifest
from app.corpus_steward.cli import (
    _failure_class,
    benchmark_source_derived_schema_documents,
)
from app.corpus_steward.source_derived_benchmark import (
    AutomatedBenchmarkGenerationPolicy,
    AutomatedBenchmarkGenerationPolicyContent,
    AutomatedBenchmarkGenerationRequest,
    SourceDerivedBenchmarkGenerationError,
    SourceDerivedBenchmarkGenerator,
)
from app.corpus_steward.source_derived_repository import (
    SourceDerivedBenchmarkConflictError,
    SQLSourceDerivedBenchmarkRepository,
)
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.corpus_steward.vector_producer import VectorProductionError
from app.persistence.database import Database
from app.persistence.models import CorpusReleaseRow
from app.schemas.corpus import (
    CorpusEvidenceRecord,
    CorpusReleaseBundle,
    CorpusReleaseManifest,
    CorpusReleaseManifestContent,
    EvidenceRole,
    ManifestEvidenceEntry,
)

REPOSITORY_ROOT = Path(__file__).parents[4]
FIXTURE_PATH = REPOSITORY_ROOT / "data" / "fixtures" / "corpus-release-v1.json"
RERANK_POOL_20_CANDIDATE_PATH = (
    REPOSITORY_ROOT
    / "models"
    / "configs"
    / "who-smart-hiv-qwen3-0.6b-rerank-pool-20.json"
)
SCHEMA_ROOT = Path(__file__).parents[4] / "packages" / "schemas"
NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
SEED = bytes(range(32))


def source_derived_bundle() -> CorpusReleaseBundle:
    fixture = CorpusReleaseBundle.model_validate_json(
        FIXTURE_PATH.read_text(encoding="utf-8")
    )
    base = fixture.evidence[0]
    records = []
    for index in range(64):
        polarity = (
            "Do not offer ART when contraindicated."
            if (index // 2) % 2
            else "Offer ART when clinically indicated."
        )
        # Records are built in pairs that differ only by their numeric identifier, which
        # the paraphrase drops. A controlled vocabulary paraphrase is accepted only when
        # its source is reachable but not already the top lexical match, so both
        # perfectly unique and wholly indistinct rows are rejected by design.
        marker = f"cohort{'abcdefgh'[(index // 2) % 8]}{'ijklmnop'[index // 16]}"
        content = (
            f"HIV ART {index} applies to adult and adolescent populations "
            f"of the {marker} programme. "
            f"Use a 10 mg dose threshold and monitor follow-up testing. {polarity}"
        )
        payload = base.model_dump(mode="python")
        payload.update(
            {
                "evidence_id": f"EV_AUTOMATED_{index:03d}",
                "source_id": f"SRC_AUTOMATED_{index:03d}",
                "source_version_id": f"SV_AUTOMATED_{index:03d}",
                "language": "es" if index % 3 == 0 else "en",
                "evidence_roles": tuple(EvidenceRole),
                "content_exact": content,
                "content_search": content,
            }
        )
        records.append(CorpusEvidenceRecord.model_validate(payload))
    entries = tuple(
        ManifestEvidenceEntry(
            evidence_id=record.evidence_id,
            source_version_id=record.source_version_id,
            evidence_sha256=record.sha256,
            artifact_sha256=record.sha256,
            qa_decision_sha256="9" * 64,
        )
        for record in records
    )
    manifest_payload = fixture.manifest.content.model_dump(mode="python")
    manifest_payload["evidence"] = entries
    manifest = CorpusReleaseManifest.seal(
        CorpusReleaseManifestContent.model_validate(manifest_payload)
    )
    return CorpusReleaseBundle(manifest=manifest, evidence=tuple(records))


def acceptance() -> BenchmarkAcceptance:
    return BenchmarkAcceptance(
        minimum_total_case_count=len(ClinicalSafetyTopic),
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


def threshold_content() -> BenchmarkThresholdPolicyContent:
    return BenchmarkThresholdPolicyContent(
        policy_id="automated-thresholds",
        revision="2026-08-26",
        development_minimum_case_count=len(ClinicalSafetyTopic),
        sealed_holdout_minimum_case_count=len(ClinicalSafetyTopic),
        safety_topic_sample_targets=tuple(
            SafetyTopicSampleTarget(
                safety_topic=topic,
                development_minimum=1,
                sealed_holdout_minimum=1,
            )
            for topic in ClinicalSafetyTopic
        ),
        development_acceptance=acceptance(),
        sealed_holdout_acceptance=acceptance(),
        threshold_derivation=ThresholdDerivation.PRESPECIFIED_ABSOLUTE,
        established_at=NOW,
    )


def generation_inputs():
    from app.corpus_steward.adjudication_schemas import (
        BenchmarkAccessPolicy,
        BenchmarkThresholdPolicy,
    )

    access = BenchmarkAccessPolicy.seal(
        BenchmarkAccessPolicyContent(
            policy_id="automated-access",
            revision="2026-08-26",
            development_builder_identities=("benchmark-custodian",),
            holdout_custodian_identities=("benchmark-custodian",),
            effective_at=NOW,
        )
    )
    threshold = BenchmarkThresholdPolicy.seal(threshold_content())
    generation = AutomatedBenchmarkGenerationPolicy.seal(
        AutomatedBenchmarkGenerationPolicyContent(
            policy_id="source-derived-generation",
            revision="2026-08-26",
            partition_seed_sha256=hashlib.sha256(SEED).hexdigest(),
            established_at=NOW,
        )
    )
    request = AutomatedBenchmarkGenerationRequest(
        generation_id="ABG_SOURCE_DERIVED_V1",
        development_benchmark_id="source-derived-development-v1",
        holdout_benchmark_id="source-derived-holdout-v1",
        actor_identity="benchmark-custodian",
        generated_at=NOW + timedelta(hours=1),
        top_k=1,
        candidate_limit=1,
    )
    return access, threshold, generation, request


def generation_result():
    access, threshold, generation, request = generation_inputs()
    result = SourceDerivedBenchmarkGenerator().generate(
        source_derived_bundle(),
        access_policy=access,
        threshold_policy=threshold,
        generation_policy=generation,
        request=request,
        partition_seed=SEED,
    )
    return result, access, threshold, generation


def test_generation_is_deterministic_complete_and_partitioned() -> None:
    first, access, threshold, generation = generation_result()
    second = SourceDerivedBenchmarkGenerator().generate(
        source_derived_bundle(),
        access_policy=access,
        threshold_policy=threshold,
        generation_policy=generation,
        request=generation_inputs()[3],
        partition_seed=SEED,
    )

    assert first == second
    assert first.development_suite.content.provenance_mode is (
        BenchmarkProvenanceMode.AUTOMATED_SOURCE_DERIVED
    )
    assert first.sealed_holdout_suite.content.provenance_mode is (
        BenchmarkProvenanceMode.AUTOMATED_SOURCE_DERIVED
    )
    development_sources = {
        source.evidence_id
        for case in first.development_suite.content.cases
        for source in case.automated_provenance.source_evidence
    }
    holdout_sources = {
        source.evidence_id
        for case in first.sealed_holdout_suite.content.cases
        for source in case.automated_provenance.source_evidence
    }
    assert development_sources.isdisjoint(holdout_sources)
    covered_topics = {
        topic
        for case in first.development_suite.content.cases
        for topic in case.safety_topics
    }
    assert covered_topics == set(ClinicalSafetyTopic)
    assert all(
        item.case.adjudication is None
        for item in first.generation_record.content.cases
    )
    negative_topics = {
        topic
        for suite in (first.development_suite, first.sealed_holdout_suite)
        for case in suite.content.cases
        if case.evidence_expectation.value == "INSUFFICIENT_EVIDENCE"
        for topic in case.safety_topics
    }
    assert {
        ClinicalSafetyTopic.STALE_SOURCE,
        ClinicalSafetyTopic.WRONG_JURISDICTION,
        ClinicalSafetyTopic.INSUFFICIENT_EVIDENCE,
    } <= negative_topics


def test_generation_rejects_an_unpinned_partition_seed() -> None:
    access, threshold, generation, request = generation_inputs()
    with pytest.raises(SourceDerivedBenchmarkGenerationError, match="partition seed"):
        SourceDerivedBenchmarkGenerator().generate(
            source_derived_bundle(),
            access_policy=access,
            threshold_policy=threshold,
            generation_policy=generation,
            request=request,
            partition_seed=b"wrong",
        )


async def test_repository_registers_generation_and_consumes_holdout_once(tmp_path) -> None:
    result, access, threshold, generation = generation_result()
    bundle = source_derived_bundle()
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'automated.sqlite3'}")
    await database.create_schema_for_tests()
    artifacts = ImmutableStewardArtifactStore(tmp_path / "artifacts")
    policy_service = ClinicalAdjudicationService(
        SQLBenchmarkAdjudicationRepository(database, artifacts), artifacts
    )
    repository = SQLSourceDerivedBenchmarkRepository(database, artifacts)
    manifest = bundle.manifest.content
    try:
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
        assert await policy_service.seal_access_policy(access.content) == access
        assert await policy_service.seal_threshold_policy(threshold.content) == threshold
        await repository.register_generation(result, policy=generation)
        candidate = RetrievalCandidateManifest.model_validate_json(
            RERANK_POOL_20_CANDIDATE_PATH.read_text(encoding="utf-8")
        )
        derived = derive_development_suite_for_candidate(
            result.development_suite, candidate
        )
        await repository.register_development_derivation(
            result.development_suite,
            derived,
            candidate,
            actor_identity="benchmark-custodian",
            derived_at=NOW + timedelta(hours=2),
        )
        opened_development, _development_run_id = (
            await repository.claim_registered_execution(
                derived.suite_sha256,
                candidate_configuration_sha256=candidate.candidate_sha256,
                vector_batch_sha256="d" * 64,
                actor_identity="benchmark-custodian",
            )
        )
        assert opened_development == derived
        with pytest.raises(
            RuntimeError, match="belongs to a different candidate"
        ):
            await repository.claim_execution(
                derived,
                candidate_configuration_sha256="e" * 64,
                vector_batch_sha256="d" * 64,
                actor_identity="benchmark-custodian",
            )
        opened, _run_id = await repository.claim_registered_execution(
            result.sealed_holdout_suite.suite_sha256,
            candidate_configuration_sha256="c" * 64,
            vector_batch_sha256="d" * 64,
            actor_identity="benchmark-custodian",
        )
        assert opened == result.sealed_holdout_suite
        with pytest.raises(SourceDerivedBenchmarkConflictError, match="already been consumed"):
            await repository.claim_registered_execution(
                result.sealed_holdout_suite.suite_sha256,
                candidate_configuration_sha256="c" * 64,
                vector_batch_sha256="d" * 64,
                actor_identity="benchmark-custodian",
            )
    finally:
        await database.close()


def test_checked_in_source_derived_schemas_match_contracts() -> None:
    for filename, expected in benchmark_source_derived_schema_documents().items():
        actual = json.loads((SCHEMA_ROOT / filename).read_text(encoding="utf-8"))
        assert actual == expected


def test_failed_run_records_why_it_failed_not_only_the_exception_type() -> None:
    """A stored class name alone cannot distinguish an exhausted timeout from a bad shape."""

    exhausted = VectorProductionError("dense adapter exhausted 2 bounded attempts")
    assert _failure_class(exhausted) == (
        "VectorProductionError: dense adapter exhausted 2 bounded attempts"
    )

    wrong_shape = VectorProductionError(
        "dense adapter returned the wrong dimension at input 7"
    )
    assert _failure_class(exhausted) != _failure_class(wrong_shape)

    # Multi-line adapter messages collapse to one bounded line for a 200-char column.
    assert _failure_class(ValueError("first line\n  second line")) == (
        "ValueError: first line second line"
    )

    # An exception carrying no message still records its type.
    assert _failure_class(VectorProductionError()) == "VectorProductionError"
