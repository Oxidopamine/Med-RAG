import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.corpus_steward.benchmark import (
    BenchmarkExecutionError,
    RetrievalBenchmarkRunner,
)
from app.corpus_steward.benchmark_schemas import (
    BenchmarkAcceptance,
    BenchmarkCase,
    BenchmarkFilter,
    BenchmarkGoldEvidence,
    BenchmarkSuite,
    BenchmarkSuiteContent,
    BenchmarkSuitePartition,
    ClinicalSafetyTopic,
    EvidenceExpectation,
    MinimumCompleteEvidenceSet,
    RetrievalMode,
    RRFWeights,
    SafetyStratumThreshold,
)
from app.corpus_steward.candidate_schemas import (
    CandidateLane,
    CandidateLaneKind,
    RetrievalCandidateContent,
    RetrievalCandidateManifest,
)
from app.corpus_steward.cli import (
    benchmark_acceptance_schema_document,
    benchmark_report_schema_document,
    benchmark_suite_schema_document,
    candidate_schema_document,
)
from app.corpus_steward.qdrant_index import stable_qdrant_point_id
from app.corpus_steward.vector_producer import (
    DeterministicHashingBackend,
    EmbeddedText,
    VectorBatchProducer,
    VectorProductionError,
)
from app.schemas.corpus import CorpusReleaseBundle, EvidenceRole

FIXTURE_PATH = Path(__file__).parents[4] / "data" / "fixtures" / "corpus-release-v1.json"
SUITE_SCHEMA_PATH = (
    Path(__file__).parents[4]
    / "packages"
    / "schemas"
    / "retrieval-benchmark-suite-1.2.0.schema.json"
)
REPORT_SCHEMA_PATH = (
    Path(__file__).parents[4]
    / "packages"
    / "schemas"
    / "retrieval-benchmark-report-1.2.0.schema.json"
)
SYNTHETIC_SUITE_CONTENT_PATH = (
    Path(__file__).parents[4] / "benchmarks" / "suites" / "synthetic-v1.content.json"
)
SYNTHETIC_SUITE_PATH = (
    Path(__file__).parents[4] / "benchmarks" / "suites" / "synthetic-v1.json"
)
CANDIDATE_SCHEMA_PATH = (
    Path(__file__).parents[4]
    / "packages"
    / "schemas"
    / "retrieval-candidate-1.0.0.schema.json"
)
BENCHMARK_ACCEPTANCE_SCHEMA_PATH = (
    Path(__file__).parents[4]
    / "packages"
    / "schemas"
    / "signed-benchmark-acceptance-1.2.0.schema.json"
)
SYNTHETIC_CANDIDATE_PATH = (
    Path(__file__).parents[4] / "models" / "configs" / "synthetic-candidate-v1.json"
)


def fixture_bundle() -> CorpusReleaseBundle:
    return CorpusReleaseBundle.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8"))


async def test_vector_producer_is_complete_batched_and_reproducible() -> None:
    bundle = fixture_bundle()
    generated_at = datetime(2026, 8, 25, tzinfo=timezone.utc)
    backend = DeterministicHashingBackend(dense_dimension=32, sparse_dimension=256)
    producer = VectorBatchProducer(backend, batch_size=2)

    first = await producer.produce(bundle, generated_at=generated_at)
    second = await producer.produce(bundle, generated_at=generated_at)

    assert first == second
    assert first.batch_sha256 == second.batch_sha256
    assert first.content.dense.model.model_id == "med-rag/baseline-hashing-dense"
    assert first.content.sparse.model.model_id == "med-rag/baseline-hashing-sparse"
    assert [item.evidence_id for item in first.content.records] == sorted(
        item.evidence_id for item in bundle.evidence
    )
    assert {item.evidence_id: item.evidence_sha256 for item in first.content.records} == {
        item.evidence_id: item.sha256 for item in bundle.evidence
    }
    assert all(len(item.dense) == 32 for item in first.content.records)
    assert all(item.sparse.indices for item in first.content.records)
    assert DeterministicHashingBackend(
        dense_dimension=64, sparse_dimension=256
    ).dense_definition.model.artifact_sha256 != backend.dense_definition.model.artifact_sha256


class _ShortBackend:
    def __init__(self) -> None:
        self._delegate = DeterministicHashingBackend(
            dense_dimension=16, sparse_dimension=64
        )

    @property
    def dense_definition(self):
        return self._delegate.dense_definition

    @property
    def sparse_definition(self):
        return self._delegate.sparse_definition

    async def embed(self, texts: Sequence[str]) -> Sequence[EmbeddedText]:
        embedded = await self._delegate.embed(texts)
        return embedded[:-1]

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[EmbeddedText]:
        return await self.embed(texts)

    async def embed_queries(self, texts: Sequence[str]) -> Sequence[EmbeddedText]:
        return await self.embed(texts)


async def test_vector_producer_rejects_incomplete_backend_output() -> None:
    with pytest.raises(VectorProductionError, match="different number"):
        await VectorBatchProducer(_ShortBackend()).produce(fixture_bundle())


class FakeBenchmarkQdrant:
    def __init__(
        self,
        bundle: CorpusReleaseBundle,
        rankings: dict[str, list[str]],
    ) -> None:
        self._evidence = {item.evidence_id: item for item in bundle.evidence}
        self._release_id = bundle.manifest.content.corpus_release_id
        self._rankings = rankings
        self.requests: list[dict[str, Any]] = []

    async def query_points(
        self, collection: str, request: dict[str, Any]
    ) -> list[dict[str, Any]]:
        self.requests.append(request)
        must = request["filter"]["must"]
        assert {
            "key": "corpus_release_id",
            "match": {"value": self._release_id},
        } in must
        assert {"key": "approval_status", "match": {"value": "APPROVED"}} in must
        ranking = self._rankings[request["using"]][: request["limit"]]
        results = []
        for rank, evidence_id in enumerate(ranking, start=1):
            evidence = self._evidence[evidence_id]
            payload = {
                "approval_status": "APPROVED",
                "corpus_release_id": self._release_id,
                "evidence_id": evidence_id,
                "evidence_roles": [item.value for item in evidence.evidence_roles],
                "evidence_sha256": evidence.sha256,
                "jurisdiction": evidence.jurisdiction,
                "language": evidence.language,
                "publisher_id": evidence.publisher_id,
                "source_class": "E1",
            }
            results.append(
                {
                    "id": stable_qdrant_point_id(evidence_id),
                    "payload": payload,
                    "score": 1.0 / rank,
                }
            )
        return results


def benchmark_suite(
    bundle: CorpusReleaseBundle,
    *,
    candidate_sha256: str,
    gold_ids: tuple[str, ...],
    forbidden_ids: tuple[str, ...] = (),
    top_k: int = 3,
) -> BenchmarkSuite:
    return BenchmarkSuite.seal(
        BenchmarkSuiteContent(
            benchmark_id="synthetic-retrieval-v1",
            suite_partition=BenchmarkSuitePartition.SYNTHETIC,
            access_policy_sha256="1" * 64,
            adjudication_process_sha256="2" * 64,
            adjudication_record_sha256="3" * 64,
            threshold_policy_sha256="4" * 64,
            candidate_configuration_sha256=candidate_sha256,
            corpus_release_id=bundle.manifest.content.corpus_release_id,
            manifest_sha256=bundle.manifest.manifest_sha256,
            cases=(
                BenchmarkCase(
                    case_id="BQ_SYNTHETIC_001",
                    question="When should Example Intervention A be offered and avoided?",
                    gold_evidence=tuple(
                        BenchmarkGoldEvidence(evidence_id=item) for item in gold_ids
                    ),
                    required_evidence_roles=(
                        EvidenceRole.APPLICABILITY,
                        EvidenceRole.EXCEPTION_OR_CONTRAINDICATION,
                        EvidenceRole.PRIMARY_SUPPORT,
                    )
                    if len(gold_ids) == 3
                    else (EvidenceRole.PRIMARY_SUPPORT,),
                    retrieval_filter=BenchmarkFilter(jurisdictions=("TEST",)),
                    forbidden_evidence_ids=forbidden_ids,
                    strata={"clinical_risk": "synthetic", "language": "en"},
                ),
            ),
            top_k=top_k,
            candidate_limit=3,
            acceptance=BenchmarkAcceptance(maximum_p95_latency_ms=60_000),
        )
    )


def retrieval_candidate(
    vectors,
    *,
    dense_weight: float = 1.0,
    sparse_weight: float = 1.0,
    rrf_k: int = 60,
    candidate_limit: int = 3,
    output_depth: int = 3,
) -> RetrievalCandidateManifest:
    return RetrievalCandidateManifest.seal(
        RetrievalCandidateContent(
            candidate_id="synthetic-candidate-v1",
            lanes=(
                CandidateLane(
                    lane_id="dense",
                    kind=CandidateLaneKind.DENSE,
                    vector_name=vectors.content.dense.name,
                    model=vectors.content.dense.model,
                    adapter_id="med-rag/deterministic-dense",
                    adapter_revision="1.0.0",
                    weight=dense_weight,
                ),
                CandidateLane(
                    lane_id="sparse",
                    kind=CandidateLaneKind.BM25,
                    vector_name=vectors.content.sparse.name,
                    model=vectors.content.sparse.model,
                    adapter_id="med-rag/deterministic-sparse",
                    adapter_revision="1.0.0",
                    weight=sparse_weight,
                ),
            ),
            rrf_k=rrf_k,
            candidate_limit=candidate_limit,
            output_depth=output_depth,
        )
    )


async def test_benchmark_runs_all_ablations_and_accepts_complete_hybrid() -> None:
    bundle = fixture_bundle()
    backend = DeterministicHashingBackend(dense_dimension=32, sparse_dimension=256)
    vectors = await VectorBatchProducer(backend).produce(bundle)
    primary = "EV_FIXTURE_PRIMARY_001"
    applicability = "EV_FIXTURE_APPLICABILITY_001"
    exception = "EV_FIXTURE_EXCEPTION_001"
    qdrant = FakeBenchmarkQdrant(
        bundle,
        {
            "dense": [primary, applicability, exception],
            "sparse": [primary, exception, applicability],
        },
    )
    candidate = retrieval_candidate(vectors)
    suite = benchmark_suite(
        bundle,
        candidate_sha256=candidate.candidate_sha256,
        gold_ids=(primary, applicability, exception),
        top_k=3,
    )

    report = await RetrievalBenchmarkRunner(qdrant, backend).run(
        bundle, vectors, suite, candidate
    )

    assert report.content.outcome == "ACCEPTED"
    assert report.content.blockers == ()
    assert len(report.content.case_results) == 3
    assert len(qdrant.requests) == 4
    assert [item.mode for item in report.content.mode_summaries] == [
        RetrievalMode.SPARSE,
        RetrievalMode.DENSE,
        RetrievalMode.HYBRID,
    ]
    hybrid = report.content.mode_summaries[-1]
    assert hybrid.mean_recall_at_k == 1.0
    assert hybrid.complete_evidence_set_rate == 1.0
    assert hybrid.mean_required_role_recall == 1.0


async def test_benchmark_rejects_forbidden_evidence_leakage() -> None:
    bundle = fixture_bundle()
    backend = DeterministicHashingBackend(dense_dimension=32, sparse_dimension=256)
    vectors = await VectorBatchProducer(backend).produce(bundle)
    primary = "EV_FIXTURE_PRIMARY_001"
    forbidden = "EV_FIXTURE_EXCEPTION_001"
    qdrant = FakeBenchmarkQdrant(
        bundle,
        {"dense": [primary, forbidden], "sparse": [primary, forbidden]},
    )
    candidate = retrieval_candidate(vectors, output_depth=2)
    suite = benchmark_suite(
        bundle,
        candidate_sha256=candidate.candidate_sha256,
        gold_ids=(primary,),
        forbidden_ids=(forbidden,),
        top_k=2,
    )

    report = await RetrievalBenchmarkRunner(qdrant, backend).run(
        bundle, vectors, suite, candidate
    )

    assert report.content.outcome == "REJECTED"
    assert "FORBIDDEN_EVIDENCE_LEAKAGE_EXCEEDS_THRESHOLD" in report.content.blockers


async def test_benchmark_rejects_query_model_pin_mismatch() -> None:
    bundle = fixture_bundle()
    producer_backend = DeterministicHashingBackend(
        dense_dimension=32, sparse_dimension=256
    )
    vectors = await VectorBatchProducer(producer_backend).produce(bundle)
    different_backend = DeterministicHashingBackend(
        dense_dimension=64, sparse_dimension=256
    )
    candidate = retrieval_candidate(vectors, output_depth=1)
    suite = benchmark_suite(
        bundle,
        candidate_sha256=candidate.candidate_sha256,
        gold_ids=("EV_FIXTURE_PRIMARY_001",),
        top_k=1,
    )
    qdrant = FakeBenchmarkQdrant(
        bundle,
        {
            "dense": ["EV_FIXTURE_PRIMARY_001"],
            "sparse": ["EV_FIXTURE_PRIMARY_001"],
        },
    )

    with pytest.raises(BenchmarkExecutionError, match="dense model"):
        await RetrievalBenchmarkRunner(qdrant, different_backend).run(
            bundle, vectors, suite, candidate
        )


async def test_benchmark_rejects_candidate_adapter_revision_mismatch() -> None:
    bundle = fixture_bundle()
    backend = DeterministicHashingBackend(dense_dimension=32, sparse_dimension=256)
    vectors = await VectorBatchProducer(backend).produce(bundle)
    candidate = retrieval_candidate(vectors, output_depth=1)
    mismatched_lanes = tuple(
        lane.model_copy(update={"adapter_revision": "2.0.0"})
        if lane.kind is CandidateLaneKind.DENSE
        else lane
        for lane in candidate.content.lanes
    )
    mismatched = RetrievalCandidateManifest.seal(
        candidate.content.model_copy(update={"lanes": mismatched_lanes})
    )
    suite = benchmark_suite(
        bundle,
        candidate_sha256=mismatched.candidate_sha256,
        gold_ids=("EV_FIXTURE_PRIMARY_001",),
        top_k=1,
    )
    qdrant = FakeBenchmarkQdrant(
        bundle,
        {
            "dense": ["EV_FIXTURE_PRIMARY_001"],
            "sparse": ["EV_FIXTURE_PRIMARY_001"],
        },
    )

    with pytest.raises(BenchmarkExecutionError, match="candidate dense lane"):
        await RetrievalBenchmarkRunner(qdrant, backend).run(
            bundle, vectors, suite, mismatched
        )


async def test_benchmark_supports_insufficient_evidence_and_reports_uncertainty() -> None:
    bundle = fixture_bundle()
    backend = DeterministicHashingBackend(dense_dimension=32, sparse_dimension=256)
    vectors = await VectorBatchProducer(backend).produce(bundle)
    candidate = retrieval_candidate(vectors, candidate_limit=1, output_depth=1)
    suite = BenchmarkSuite.seal(
        BenchmarkSuiteContent(
            benchmark_id="synthetic-insufficient-v1",
            suite_partition=BenchmarkSuitePartition.SYNTHETIC,
            access_policy_sha256="1" * 64,
            adjudication_process_sha256="2" * 64,
            adjudication_record_sha256="3" * 64,
            threshold_policy_sha256="4" * 64,
            candidate_configuration_sha256=candidate.candidate_sha256,
            corpus_release_id=bundle.manifest.content.corpus_release_id,
            manifest_sha256=bundle.manifest.manifest_sha256,
            cases=(
                BenchmarkCase(
                    case_id="BQ_INSUFFICIENT_001",
                    question="What is the recommended fictional dose?",
                        evidence_expectation=EvidenceExpectation.INSUFFICIENT_EVIDENCE,
                        safety_topics=(ClinicalSafetyTopic.INSUFFICIENT_EVIDENCE,),
                ),
            ),
            top_k=1,
            candidate_limit=1,
            acceptance=BenchmarkAcceptance(
                maximum_p95_latency_ms=60_000,
                safety_strata=(
                    SafetyStratumThreshold(
                            stratum_key="safety_topic",
                            stratum_value=ClinicalSafetyTopic.INSUFFICIENT_EVIDENCE.value,
                        minimum_insufficient_evidence_accuracy=1.0,
                    ),
                ),
            ),
        )
    )
    qdrant = FakeBenchmarkQdrant(bundle, {"dense": [], "sparse": []})

    report = await RetrievalBenchmarkRunner(qdrant, backend).run(
        bundle, vectors, suite, candidate
    )

    assert report.content.outcome == "ACCEPTED"
    candidate = report.content.mode_summaries[-1]
    assert candidate.insufficient_evidence_accuracy == 1.0
    assert candidate.insufficient_evidence_confidence is not None
    assert candidate.insufficient_evidence_confidence.sample_count == 1
    assert report.content.stratum_summaries[0].insufficient_evidence_accuracy == 1.0


def test_clinical_suite_requires_adjudicator_provenance() -> None:
    bundle = fixture_bundle()
    with pytest.raises(ValueError, match="adjudication provenance"):
        BenchmarkSuiteContent(
            benchmark_id="development-clinical-v1",
            suite_partition=BenchmarkSuitePartition.DEVELOPMENT,
            access_policy_sha256="1" * 64,
            adjudication_process_sha256="2" * 64,
            adjudication_record_sha256="3" * 64,
            threshold_policy_sha256="4" * 64,
            candidate_configuration_sha256="5" * 64,
            corpus_release_id=bundle.manifest.content.corpus_release_id,
            manifest_sha256=bundle.manifest.manifest_sha256,
            cases=(
                BenchmarkCase(
                    case_id="BQ_CLINICAL_001",
                    question="What is recommended?",
                    gold_evidence=(
                        BenchmarkGoldEvidence(
                            evidence_id="EV_FIXTURE_PRIMARY_001"
                        ),
                    ),
                ),
            ),
        )


async def test_weighted_rrf_and_alternative_complete_evidence_sets_are_deterministic() -> None:
    bundle = fixture_bundle()
    backend = DeterministicHashingBackend(dense_dimension=32, sparse_dimension=256)
    vectors = await VectorBatchProducer(backend).produce(bundle)
    primary = "EV_FIXTURE_PRIMARY_001"
    applicability = "EV_FIXTURE_APPLICABILITY_001"
    candidate = retrieval_candidate(
        vectors,
        dense_weight=0.0,
        sparse_weight=2.0,
        candidate_limit=2,
        output_depth=1,
    )
    suite = BenchmarkSuite.seal(
        BenchmarkSuiteContent(
            benchmark_id="synthetic-weighted-v1",
            suite_partition=BenchmarkSuitePartition.SYNTHETIC,
            access_policy_sha256="1" * 64,
            adjudication_process_sha256="2" * 64,
            adjudication_record_sha256="3" * 64,
            threshold_policy_sha256="4" * 64,
            candidate_configuration_sha256=candidate.candidate_sha256,
            corpus_release_id=bundle.manifest.content.corpus_release_id,
            manifest_sha256=bundle.manifest.manifest_sha256,
            cases=(
                BenchmarkCase(
                    case_id="BQ_WEIGHTED_001",
                    question="Which evidence is primary?",
                    gold_evidence=(
                        BenchmarkGoldEvidence(evidence_id=applicability, required=False),
                        BenchmarkGoldEvidence(evidence_id=primary),
                    ),
                    minimum_complete_evidence_sets=(
                        MinimumCompleteEvidenceSet(
                            set_id="primary-alone", evidence_ids=(primary,)
                        ),
                    ),
                    required_evidence_roles=(EvidenceRole.PRIMARY_SUPPORT,),
                ),
            ),
            top_k=1,
            candidate_limit=2,
            rrf_weights=RRFWeights(dense=0.0, sparse=2.0),
            acceptance=BenchmarkAcceptance(
                minimum_mean_recall_at_k=0.5,
                maximum_p95_latency_ms=60_000,
            ),
        )
    )
    qdrant = FakeBenchmarkQdrant(
        bundle,
        {
            "dense": [applicability, primary],
            "sparse": [primary, applicability],
        },
    )

    report = await RetrievalBenchmarkRunner(qdrant, backend).run(
        bundle, vectors, suite, candidate
    )

    hybrid = next(
        item
        for item in report.content.case_results
        if item.mode is RetrievalMode.HYBRID
    )
    assert hybrid.retrieved[0].evidence_id == primary
    assert hybrid.metrics.complete_evidence_set_recalled is True
    assert report.content.rrf_weights == RRFWeights(dense=0.0, sparse=2.0)


async def test_hybrid_lane_failure_is_isolated_recorded_and_blocks_acceptance() -> None:
    bundle = fixture_bundle()
    backend = DeterministicHashingBackend(dense_dimension=32, sparse_dimension=256)
    vectors = await VectorBatchProducer(backend).produce(bundle)
    primary = "EV_FIXTURE_PRIMARY_001"

    class FailingDenseQdrant(FakeBenchmarkQdrant):
        async def query_points(
            self, collection: str, request: dict[str, Any]
        ) -> list[dict[str, Any]]:
            if request["using"] == "dense":
                raise TimeoutError("simulated dense timeout")
            return await super().query_points(collection, request)

    qdrant = FailingDenseQdrant(bundle, {"dense": [], "sparse": [primary]})
    candidate = retrieval_candidate(vectors, output_depth=1)
    suite = benchmark_suite(
        bundle,
        candidate_sha256=candidate.candidate_sha256,
        gold_ids=(primary,),
        top_k=1,
    )

    report = await RetrievalBenchmarkRunner(qdrant, backend).run(
        bundle, vectors, suite, candidate
    )

    hybrid = report.content.case_results[-1]
    assert hybrid.retrieved[0].evidence_id == primary
    assert hybrid.candidate_failures == ("DENSE_LANE_FAILURE:TimeoutError",)
    assert "CANDIDATE_FAILURE_CASES_EXCEED_THRESHOLD" in report.content.blockers


def test_checked_in_benchmark_schemas_match_versioned_contracts() -> None:
    assert json.loads(SUITE_SCHEMA_PATH.read_text(encoding="utf-8")) == (
        benchmark_suite_schema_document()
    )
    assert json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8")) == (
        benchmark_report_schema_document()
    )
    assert json.loads(CANDIDATE_SCHEMA_PATH.read_text(encoding="utf-8")) == (
        candidate_schema_document()
    )
    assert json.loads(
        BENCHMARK_ACCEPTANCE_SCHEMA_PATH.read_text(encoding="utf-8")
    ) == benchmark_acceptance_schema_document()


def test_synthetic_benchmark_suite_is_sealed_and_release_bound() -> None:
    bundle = fixture_bundle()
    content = BenchmarkSuiteContent.model_validate_json(
        SYNTHETIC_SUITE_CONTENT_PATH.read_text(encoding="utf-8")
    )
    sealed = BenchmarkSuite.model_validate_json(
        SYNTHETIC_SUITE_PATH.read_text(encoding="utf-8")
    )
    candidate = RetrievalCandidateManifest.model_validate_json(
        SYNTHETIC_CANDIDATE_PATH.read_text(encoding="utf-8")
    )

    assert sealed == BenchmarkSuite.seal(content)
    assert sealed.content.corpus_release_id == bundle.manifest.content.corpus_release_id
    assert sealed.content.manifest_sha256 == bundle.manifest.manifest_sha256
    assert sealed.content.candidate_configuration_sha256 == candidate.candidate_sha256
