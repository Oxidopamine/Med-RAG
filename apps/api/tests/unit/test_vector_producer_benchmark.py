import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.corpus_steward.benchmark import (
    BenchmarkExecutionError,
    RetrievalBenchmarkRunner,
    derive_development_suite_for_candidate,
)
from app.retrieval.pipeline import Candidate as _Candidate
from app.retrieval.pipeline import (
    conflict_aware_selection,
    preserve_retrieval_floor,
)
from app.corpus_steward.benchmark_schemas import (
    BENCHMARK_CONTRACT_VERSION,
    BenchmarkAcceptance,
    BenchmarkCase,
    BenchmarkFilter,
    BenchmarkGoldEvidence,
    BenchmarkProvenanceMode,
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
    CandidateReranker,
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
from app.corpus_steward.query_expansion import (
    EXACT_TERMINOLOGY_REVISION,
    SAFETY_QUERY_REVISION,
)
from app.corpus_steward.vector_producer import (
    DeterministicHashingBackend,
    EmbeddedText,
    VectorBatchProducer,
    VectorProductionCheckpoint,
    VectorProductionCheckpointContent,
    VectorProductionError,
)
from app.schemas.corpus import CorpusReleaseBundle, EvidenceRole

FIXTURE_PATH = Path(__file__).parents[4] / "data" / "fixtures" / "corpus-release-v1.json"
SUITE_SCHEMA_PATH = (
    Path(__file__).parents[4]
    / "packages"
    / "schemas"
    / "retrieval-benchmark-suite-1.6.0.schema.json"
)
REPORT_SCHEMA_PATH = (
    Path(__file__).parents[4]
    / "packages"
    / "schemas"
    / "retrieval-benchmark-report-1.6.0.schema.json"
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
    / "signed-benchmark-acceptance-1.6.0.schema.json"
)
SYNTHETIC_CANDIDATE_PATH = (
    Path(__file__).parents[4] / "models" / "configs" / "synthetic-candidate-v1.json"
)
SOURCE_DERIVED_DEVELOPMENT_SUITE_PATH = (
    Path(__file__).parents[4]
    / "benchmarks"
    / "suites"
    / "who-smart-hiv-source-derived-development-v7.json"
)
QWEN_RERANK_POOL_100_CANDIDATE_PATH = (
    Path(__file__).parents[4]
    / "models"
    / "configs"
    / "who-smart-hiv-qwen3-0.6b-rerank-pool-100.json"
)
CONFLICT_AWARE_CANDIDATE_PATH = (
    Path(__file__).parents[4]
    / "models"
    / "configs"
    / "who-smart-hiv-qwen3-0.6b-conflict-aware.json"
)


def _requires_regenerated_artifact(path: Path) -> None:
    """Skip when a checked-in sealed artifact predates the current contract.

    These artifacts are outputs of the sealing pipeline, not fixtures: regenerating
    them needs the release database, signing keys, and Qdrant. Skipping keyed on the
    contract version means the check re-arms by itself once the artifact is rebuilt,
    instead of being silently deleted or hand-edited back into agreement.
    """

    version = json.loads(path.read_text(encoding="utf-8"))["content"]["schema_version"]
    if version != BENCHMARK_CONTRACT_VERSION:
        pytest.skip(
            f"{path.name} is sealed at contract {version}; "
            f"regenerate at {BENCHMARK_CONTRACT_VERSION} "
            "(corpus-steward benchmark-generate-source-derived / benchmark-seal-suite)"
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


async def test_vector_producer_resumes_digest_sealed_release_prefix() -> None:
    bundle = fixture_bundle()
    backend = DeterministicHashingBackend(dense_dimension=16, sparse_dimension=64)
    checkpoints: list[VectorProductionCheckpoint] = []
    complete = await VectorBatchProducer(backend, batch_size=1).produce(
        bundle,
        generated_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
        progress_callback=checkpoints.append,
    )

    class CountingBackend(DeterministicHashingBackend):
        def __init__(self) -> None:
            super().__init__(dense_dimension=16, sparse_dimension=64)
            self.document_count = 0

        async def embed_documents(self, texts: Sequence[str]):
            self.document_count += len(texts)
            return await super().embed_documents(texts)

    resumed_backend = CountingBackend()
    resumed = await VectorBatchProducer(resumed_backend, batch_size=1).produce(
        bundle,
        checkpoint=checkpoints[0],
    )

    assert resumed == complete
    assert resumed_backend.document_count == len(bundle.evidence) - 1

    drifted_record = checkpoints[0].content.records[0].model_copy(
        update={"evidence_sha256": "0" * 64}
    )
    drifted = VectorProductionCheckpoint.seal(
        VectorProductionCheckpointContent(
            **checkpoints[0].content.model_dump(exclude={"records"}),
            records=(drifted_record,),
        )
    )
    with pytest.raises(VectorProductionError, match="evidence digest mismatch"):
        await VectorBatchProducer(backend, batch_size=1).produce(
            bundle,
            checkpoint=drifted,
        )


async def test_vector_checkpoint_cadence_always_includes_final_batch() -> None:
    bundle = fixture_bundle()
    backend = DeterministicHashingBackend(dense_dimension=16, sparse_dimension=64)
    checkpoints: list[VectorProductionCheckpoint] = []

    await VectorBatchProducer(backend, batch_size=1).produce(
        bundle,
        progress_callback=checkpoints.append,
        checkpoint_interval_batches=2,
    )

    assert [len(item.content.records) for item in checkpoints] == [2, 3]
    with pytest.raises(ValueError, match="checkpoint interval"):
        await VectorBatchProducer(backend).produce(
            bundle,
            checkpoint_interval_batches=0,
        )


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
                "source_version_id": evidence.source_version_id,
                "lifecycle_status": evidence.lifecycle_status.value,
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
            provenance_mode=BenchmarkProvenanceMode.SYNTHETIC,
            access_policy_sha256="1" * 64,
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
    terminology_revision: str | None = None,
    safety_query_revision: str | None = None,
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
            terminology_revision=terminology_revision,
            safety_query_revision=safety_query_revision,
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


async def test_query_expansion_lanes_only_change_hybrid_ablation() -> None:
    bundle = fixture_bundle()
    backend = DeterministicHashingBackend(dense_dimension=32, sparse_dimension=256)
    vectors = await VectorBatchProducer(backend).produce(bundle)
    primary = "EV_FIXTURE_PRIMARY_001"
    qdrant = FakeBenchmarkQdrant(
        bundle,
        {
            "dense": [primary],
            "sparse": [primary],
        },
    )
    candidate = retrieval_candidate(
        vectors,
        output_depth=1,
        terminology_revision=EXACT_TERMINOLOGY_REVISION,
        safety_query_revision=SAFETY_QUERY_REVISION,
    )
    suite = benchmark_suite(
        bundle,
        candidate_sha256=candidate.candidate_sha256,
        gold_ids=(primary,),
        top_k=1,
    )
    question = suite.content.cases[0].question
    assert "Intervention" in question

    await RetrievalBenchmarkRunner(qdrant, backend).run(
        bundle, vectors, suite, candidate
    )

    dense_requests = [item for item in qdrant.requests if item["using"] == "dense"]
    sparse_requests = [item for item in qdrant.requests if item["using"] == "sparse"]
    assert len(dense_requests) == 2
    assert len(sparse_requests) > 2


class _FakeReranker:
    def __init__(self, model, scores: tuple[float, ...]) -> None:
        self.model_reference = model
        self.adapter_identity = ("med-rag/qwen3-reranker-transformers", "1.0.0")
        self.instruction_sha256 = "f" * 64
        self._scores = scores
        self.pool_sizes: list[int] = []

    async def score(
        self, query: str, documents: Sequence[str]
    ) -> Sequence[float]:
        self.pool_sizes.append(len(documents))
        return self._scores[: len(documents)]


@pytest.mark.parametrize("pool_size", [20, 50, 100])
async def test_qwen_reranker_pool_sizes_and_retrieval_floor_are_enforced(
    pool_size: int,
) -> None:
    bundle = fixture_bundle()
    backend = DeterministicHashingBackend(dense_dimension=32, sparse_dimension=256)
    vectors = await VectorBatchProducer(backend).produce(bundle)
    primary = "EV_FIXTURE_PRIMARY_001"
    applicability = "EV_FIXTURE_APPLICABILITY_001"
    exception = "EV_FIXTURE_EXCEPTION_001"
    base = retrieval_candidate(
        vectors,
        candidate_limit=pool_size,
        output_depth=2,
    )
    reranker_config = CandidateReranker(
        model=vectors.content.dense.model.model_copy(
            update={
                "model_id": "Qwen/Qwen3-Reranker-0.6B",
                "artifact_sha256": "9" * 64,
            }
        ),
        adapter_id="med-rag/qwen3-reranker-transformers",
        adapter_revision="1.0.0",
        instruction_sha256="f" * 64,
        candidate_pool=pool_size,
        output_depth=2,
    )
    candidate = RetrievalCandidateManifest.seal(
        base.content.model_copy(update={"reranker": reranker_config})
    )
    suite = benchmark_suite(
        bundle,
        candidate_sha256=candidate.candidate_sha256,
        gold_ids=(primary,),
        top_k=2,
    )
    qdrant = FakeBenchmarkQdrant(
        bundle,
        {
            "dense": [primary, applicability, exception],
            "sparse": [primary, applicability, exception],
        },
    )
    reranker = _FakeReranker(reranker_config.model, (0.01, 0.2, 0.99))

    report = await RetrievalBenchmarkRunner(
        qdrant, backend, reranker=reranker
    ).run(bundle, vectors, suite, candidate)

    hybrid = next(
        item
        for item in report.content.case_results
        if item.mode is RetrievalMode.HYBRID
    )
    assert primary in {item.evidence_id for item in hybrid.retrieved}
    assert reranker.pool_sizes == [3]
    assert all(request["limit"] == pool_size for request in qdrant.requests)


def test_retrieval_floor_replacement_never_expands_sealed_candidate_pool() -> None:
    bundle = fixture_bundle()
    evidence = {item.evidence_id: item for item in bundle.evidence}
    primary = "EV_FIXTURE_PRIMARY_001"
    applicability = "EV_FIXTURE_APPLICABILITY_001"
    exception = "EV_FIXTURE_EXCEPTION_001"

    preserved = preserve_retrieval_floor(
        [_Candidate(applicability, 0.9), _Candidate(exception, 0.8)],
        [_Candidate(primary, 1.0), _Candidate(applicability, 0.9)],
        evidence,
        output_depth=2,
        limit=2,
    )

    assert len(preserved) == 2
    assert {item.evidence_id for item in preserved} == {primary, applicability}


def test_conflict_selection_pairs_sides_and_preserves_required_roles() -> None:
    bundle = fixture_bundle()
    evidence = {item.evidence_id: item for item in bundle.evidence}
    primary = "EV_FIXTURE_PRIMARY_001"
    applicability = "EV_FIXTURE_APPLICABILITY_001"
    exception = "EV_FIXTURE_EXCEPTION_001"
    fused = [
        _Candidate(primary, 0.9),
        _Candidate(applicability, 0.8),
        _Candidate(exception, 0.7),
    ]

    selected, rules = conflict_aware_selection(
        fused,
        {
            "safety-query-1": [_Candidate(primary, 1.0)],
            "safety-query-2": [_Candidate(exception, 1.0)],
        },
        evidence,
        conflict_side_lanes=("safety-query-1", "safety-query-2"),
        output_depth=3,
        limit=3,
    )

    assert [item.evidence_id for item in selected[:2]] == [primary, exception]
    assert {item.evidence_id for item in selected} == {
        primary,
        applicability,
        exception,
    }
    assert rules[:2] == (
        f"CONFLICT_SIDE[safety-query-1]:{primary}",
        f"CONFLICT_SIDE[safety-query-2]:{exception}",
    )


def test_development_suite_derivation_binds_candidate_pool_without_opening_holdout() -> None:
    _requires_regenerated_artifact(SOURCE_DERIVED_DEVELOPMENT_SUITE_PATH)
    suite = BenchmarkSuite.model_validate_json(
        SOURCE_DERIVED_DEVELOPMENT_SUITE_PATH.read_text(encoding="utf-8")
    )
    candidate = RetrievalCandidateManifest.model_validate_json(
        QWEN_RERANK_POOL_100_CANDIDATE_PATH.read_text(encoding="utf-8")
    )

    derived = derive_development_suite_for_candidate(suite, candidate)

    assert derived.content.cases == suite.content.cases
    assert derived.content.acceptance == suite.content.acceptance
    # The pool width lives on the candidate and is deliberately absent from the
    # suite: re-tuning it must not mint a new benchmark.
    assert candidate.content.candidate_limit == 100
    assert not hasattr(derived.content, "candidate_limit")
    assert derived.content.top_k == 10
    assert derived.content.candidate_configuration_sha256 == candidate.candidate_sha256
    assert derived.suite_sha256 != suite.suite_sha256

    holdout = BenchmarkSuite.seal(
        suite.content.model_copy(
            update={"suite_partition": BenchmarkSuitePartition.SEALED_HOLDOUT}
        )
    )
    with pytest.raises(ValueError, match="only development suites"):
        derive_development_suite_for_candidate(holdout, candidate)


def test_checked_in_conflict_aware_candidate_is_exactly_sealed() -> None:
    candidate = RetrievalCandidateManifest.model_validate_json(
        CONFLICT_AWARE_CANDIDATE_PATH.read_text(encoding="utf-8")
    )

    assert candidate == RetrievalCandidateManifest.seal(candidate.content)
    assert candidate.content.reranker is None
    assert candidate.content.safety_query_revision == "clinical-safety-query-v2"
    assert candidate.candidate_sha256 == (
        "b1a3342b82fc1f302a631e24f3e471e12b0955133924cdca1cbfe3164d294b80"
    )


@pytest.mark.parametrize("pool_size", [20, 50, 100])
def test_checked_in_qwen_reranker_pool_candidates_are_exactly_sealed(
    pool_size: int,
) -> None:
    root = Path(__file__).parents[4] / "models" / "configs"
    content = RetrievalCandidateContent.model_validate_json(
        (root / f"who-smart-hiv-qwen3-0.6b-rerank-pool-{pool_size}.content.json")
        .read_text(encoding="utf-8")
    )
    sealed = RetrievalCandidateManifest.model_validate_json(
        (root / f"who-smart-hiv-qwen3-0.6b-rerank-pool-{pool_size}.json").read_text(
            encoding="utf-8"
        )
    )

    assert sealed == RetrievalCandidateManifest.seal(content)
    assert sealed.content.candidate_limit == pool_size
    assert sealed.content.reranker is not None
    assert sealed.content.reranker.candidate_pool == pool_size
    assert sealed.content.reranker.model.artifact_sha256 == (
        "815e08f75f2b1833b25d5aca1982dfe9c48b281a183fdadf09eddd908a443b61"
    )


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
            provenance_mode=BenchmarkProvenanceMode.SYNTHETIC,
            access_policy_sha256="1" * 64,
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
            provenance_mode=BenchmarkProvenanceMode.INDEPENDENT_REVIEWED,
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
            provenance_mode=BenchmarkProvenanceMode.SYNTHETIC,
            access_policy_sha256="1" * 64,
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
    _requires_regenerated_artifact(SYNTHETIC_SUITE_PATH)
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
