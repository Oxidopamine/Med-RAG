"""Release-bound sparse, dense, and hybrid Qdrant retrieval benchmarks."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from statistics import fmean
from time import perf_counter
from typing import Any, Protocol

from app.corpus_steward.benchmark_schemas import (
    BenchmarkAdjudicationSummary,
    BenchmarkCase,
    BenchmarkCaseMetrics,
    BenchmarkCaseResult,
    BenchmarkModeSummary,
    BenchmarkReport,
    BenchmarkReportContent,
    BenchmarkStratumSummary,
    BenchmarkSuite,
    EvidenceExpectation,
    MetricConfidenceInterval,
    RetrievalMode,
    RetrievedEvidence,
    RRFWeights,
    SafetyStratumThreshold,
)
from app.corpus_steward.candidate_schemas import (
    CandidateLaneKind,
    RetrievalCandidateManifest,
)
from app.corpus_steward.index_schemas import IndexVectorBatch
from app.corpus_steward.qdrant_index import stable_qdrant_point_id
from app.corpus_steward.vector_producer import EmbeddedText, EmbeddingBackend
from app.schemas.corpus import CorpusEvidenceRecord, CorpusReleaseBundle
from app.schemas.domain import utc_now

BENCHMARK_RUNNER_VERSION = "1.2.0"
_PAYLOAD_FIELDS = [
    "approval_status",
    "corpus_release_id",
    "evidence_id",
    "evidence_roles",
    "evidence_sha256",
    "jurisdiction",
    "language",
    "publisher_id",
    "source_class",
]


class BenchmarkExecutionError(RuntimeError):
    """Raised when inputs or Qdrant results violate the benchmark boundary."""


class BenchmarkQdrant(Protocol):
    async def query_points(
        self, collection: str, request: dict[str, Any]
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class _Candidate:
    evidence_id: str
    score: float


def weighted_rrf(
    rankings: Mapping[str, list[_Candidate]],
    *,
    weights: Mapping[str, float],
    rrf_k: int,
    limit: int,
) -> list[_Candidate]:
    """Fuse independent rankings with sealed weights and evidence-ID tie breaking."""

    if rrf_k <= 0 or limit <= 0:
        raise ValueError("RRF k and limit must be positive")
    if set(rankings) != set(weights):
        raise ValueError("every RRF ranking must have exactly one weight")
    if any(not math.isfinite(weight) or weight < 0 for weight in weights.values()):
        raise ValueError("RRF weights must be finite and non-negative")
    if not any(weights.values()):
        raise ValueError("at least one RRF weight must be positive")
    scores: dict[str, float] = {}
    for lane, ranking in rankings.items():
        seen: set[str] = set()
        for rank, item in enumerate(ranking, start=1):
            if item.evidence_id in seen:
                raise ValueError(f"RRF lane repeats evidence: {lane}")
            seen.add(item.evidence_id)
            scores[item.evidence_id] = scores.get(item.evidence_id, 0.0) + (
                weights[lane] / (rrf_k + rank)
            )
    return [
        _Candidate(evidence_id=evidence_id, score=score)
        for evidence_id, score in sorted(
            scores.items(), key=lambda item: (-item[1], item[0])
        )[:limit]
    ]


class RetrievalBenchmarkRunner:
    def __init__(
        self,
        qdrant: BenchmarkQdrant,
        backend: EmbeddingBackend,
        *,
        clock=perf_counter,
    ) -> None:
        self._qdrant = qdrant
        self._backend = backend
        self._clock = clock

    async def run(
        self,
        bundle: CorpusReleaseBundle,
        vectors: IndexVectorBatch,
        suite: BenchmarkSuite,
        candidate: RetrievalCandidateManifest,
    ) -> BenchmarkReport:
        evidence = self._validate_inputs(bundle, vectors, suite, candidate)
        content = suite.content
        case_results: list[BenchmarkCaseResult] = []
        for mode in content.modes:
            for case in content.cases:
                started = self._clock()
                embedded = await self._embed_question(case.question)
                candidates, candidate_failures = await self._retrieve(
                    bundle.manifest.content.qdrant_collection,
                    vectors,
                    case,
                    mode,
                    embedded,
                    evidence,
                    candidate_limit=content.candidate_limit,
                    rrf_k=content.rrf_k,
                    rrf_weights=content.rrf_weights,
                )
                elapsed_ms = max(0.0, (self._clock() - started) * 1_000.0)
                retrieved = tuple(
                    RetrievedEvidence(
                        evidence_id=item.evidence_id,
                        rank=rank,
                        score=item.score,
                    )
                    for rank, item in enumerate(candidates[: content.top_k], start=1)
                )
                case_results.append(
                    BenchmarkCaseResult(
                        case_id=case.case_id,
                        mode=mode,
                        retrieved=retrieved,
                        metrics=self._metrics(
                            case,
                            retrieved,
                            evidence,
                            top_k=content.top_k,
                            latency_ms=elapsed_ms,
                        ),
                        candidate_failures=candidate_failures,
                    )
                )

        summaries = tuple(
            self._summarize(
                mode,
                [result for result in case_results if result.mode is mode],
            )
            for mode in content.modes
        )
        candidate_summary = next(
            summary for summary in summaries if summary.mode is content.candidate_mode
        )
        candidate_results = [
            result for result in case_results if result.mode is content.candidate_mode
        ]
        strata = self._summarize_strata(candidate_results, suite)
        blockers = self._acceptance_blockers(candidate_summary, strata, suite)
        report_content = BenchmarkReportContent(
            runner_version=BENCHMARK_RUNNER_VERSION,
            benchmark_id=content.benchmark_id,
            suite_partition=content.suite_partition,
            access_policy_sha256=content.access_policy_sha256,
            adjudication_process_sha256=content.adjudication_process_sha256,
            adjudication_record_sha256=content.adjudication_record_sha256,
            threshold_policy_sha256=content.threshold_policy_sha256,
            benchmark_suite_sha256=suite.suite_sha256,
            candidate_configuration_sha256=candidate.candidate_sha256,
            corpus_release_id=content.corpus_release_id,
            manifest_sha256=content.manifest_sha256,
            vector_batch_sha256=vectors.batch_sha256,
            qdrant_collection=bundle.manifest.content.qdrant_collection,
            top_k=content.top_k,
            candidate_limit=content.candidate_limit,
            rrf_k=content.rrf_k,
            rrf_weights=content.rrf_weights,
            candidate_mode=content.candidate_mode,
            case_results=tuple(case_results),
            mode_summaries=summaries,
            stratum_summaries=strata,
            adjudication_summary=self._adjudication_summary(suite),
            generated_at=utc_now(),
            outcome="REJECTED" if blockers else "ACCEPTED",
            blockers=blockers,
        )
        return BenchmarkReport.seal(report_content)

    def _validate_inputs(
        self,
        bundle: CorpusReleaseBundle,
        vectors: IndexVectorBatch,
        suite: BenchmarkSuite,
        candidate: RetrievalCandidateManifest,
    ) -> dict[str, CorpusEvidenceRecord]:
        release_id = bundle.manifest.content.corpus_release_id
        manifest_sha256 = bundle.manifest.manifest_sha256
        if suite.content.corpus_release_id != release_id:
            raise BenchmarkExecutionError("benchmark suite corpus release does not match bundle")
        if suite.content.manifest_sha256 != manifest_sha256:
            raise BenchmarkExecutionError("benchmark suite manifest digest does not match bundle")
        if vectors.content.corpus_release_id != release_id:
            raise BenchmarkExecutionError("vector batch corpus release does not match bundle")
        if vectors.content.manifest_sha256 != manifest_sha256:
            raise BenchmarkExecutionError("vector batch manifest digest does not match bundle")
        if self._backend.dense_definition != vectors.content.dense:
            raise BenchmarkExecutionError("benchmark dense model does not match vector batch pin")
        if self._backend.sparse_definition != vectors.content.sparse:
            raise BenchmarkExecutionError("benchmark sparse model does not match vector batch pin")
        if suite.content.candidate_configuration_sha256 != candidate.candidate_sha256:
            raise BenchmarkExecutionError(
                "benchmark suite candidate configuration digest does not match"
            )
        if candidate.content.reranker is not None:
            raise BenchmarkExecutionError(
                "this benchmark runner version does not execute configured rerankers"
            )
        dense_lane = candidate.content.lane(CandidateLaneKind.DENSE)
        sparse_lane = candidate.content.lane(CandidateLaneKind.BM25)
        if (
            dense_lane.vector_name != vectors.content.dense.name
            or dense_lane.model != vectors.content.dense.model
            or (dense_lane.adapter_id, dense_lane.adapter_revision)
            != self._backend.dense_adapter_identity
        ):
            raise BenchmarkExecutionError("candidate dense lane does not match vector batch")
        if (
            sparse_lane.vector_name != vectors.content.sparse.name
            or sparse_lane.model != vectors.content.sparse.model
            or (sparse_lane.adapter_id, sparse_lane.adapter_revision)
            != self._backend.sparse_adapter_identity
        ):
            raise BenchmarkExecutionError("candidate BM25 lane does not match vector batch")
        if (
            candidate.content.rrf_k != suite.content.rrf_k
            or candidate.content.candidate_limit != suite.content.candidate_limit
            or candidate.content.output_depth != suite.content.top_k
            or dense_lane.weight != suite.content.rrf_weights.dense
            or sparse_lane.weight != suite.content.rrf_weights.sparse
        ):
            raise BenchmarkExecutionError(
                "benchmark suite retrieval parameters do not match candidate configuration"
            )

        evidence = {item.evidence_id: item for item in bundle.evidence}
        vector_ids = {item.evidence_id for item in vectors.content.records}
        if vector_ids != set(evidence):
            raise BenchmarkExecutionError("vector batch evidence set does not match bundle")
        for case in suite.content.cases:
            referenced = {
                *(item.evidence_id for item in case.gold_evidence),
                *case.forbidden_evidence_ids,
            }
            if missing := referenced - set(evidence):
                raise BenchmarkExecutionError(
                    f"benchmark case {case.case_id} references unknown evidence: "
                    + ", ".join(sorted(missing))
                )
            gold_records = [evidence[item.evidence_id] for item in case.gold_evidence]
            if not self._records_match_filter(gold_records, case):
                raise BenchmarkExecutionError(
                    f"benchmark case {case.case_id} filters out its own gold evidence"
                )
            gold_roles = {
                role for record in gold_records for role in record.evidence_roles
            }
            if missing_roles := set(case.required_evidence_roles) - gold_roles:
                raise BenchmarkExecutionError(
                    f"benchmark case {case.case_id} gold set lacks required roles: "
                    + ", ".join(sorted(role.value for role in missing_roles))
                )
        for threshold in suite.content.acceptance.safety_strata:
            matching = [
                case
                for case in suite.content.cases
                if self._case_matches_threshold(case, threshold)
            ]
            if not matching:
                raise BenchmarkExecutionError(
                    "safety-stratum threshold has no matching cases: "
                    f"{threshold.stratum_key}={threshold.stratum_value}"
                )
        return evidence

    @staticmethod
    def _case_matches_threshold(
        case: BenchmarkCase, threshold: SafetyStratumThreshold
    ) -> bool:
        if threshold.stratum_key == "safety_topic":
            return threshold.stratum_value in {item.value for item in case.safety_topics}
        return case.strata.get(threshold.stratum_key) == threshold.stratum_value

    @staticmethod
    def _records_match_filter(
        records: list[CorpusEvidenceRecord], case: BenchmarkCase
    ) -> bool:
        filters = case.retrieval_filter
        return all(
            (not filters.jurisdictions or item.jurisdiction in filters.jurisdictions)
            and (not filters.languages or item.language in filters.languages)
            and (not filters.publisher_ids or item.publisher_id in filters.publisher_ids)
            for item in records
        )

    async def _embed_question(self, question: str) -> EmbeddedText:
        embedded = tuple(await self._backend.embed_queries((question,)))
        if len(embedded) != 1:
            raise BenchmarkExecutionError(
                "embedding backend did not return exactly one query vector"
            )
        try:
            if len(embedded[0].dense) != self._backend.dense_definition.dimension:
                raise BenchmarkExecutionError("query dense vector has the wrong dimension")
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in embedded[0].dense
            ):
                raise BenchmarkExecutionError("query dense vector contains invalid values")
            if embedded[0].sparse.indices[-1] >= self._backend.sparse_definition.dimension:
                raise BenchmarkExecutionError("query sparse vector has the wrong dimension")
        except IndexError as error:
            raise BenchmarkExecutionError("query sparse vector is empty") from error
        return embedded[0]

    async def _retrieve(
        self,
        collection: str,
        vectors: IndexVectorBatch,
        case: BenchmarkCase,
        mode: RetrievalMode,
        embedded: EmbeddedText,
        evidence: Mapping[str, CorpusEvidenceRecord],
        *,
        candidate_limit: int,
        rrf_k: int,
        rrf_weights: RRFWeights,
    ) -> tuple[list[_Candidate], tuple[str, ...]]:
        dense_query = list(embedded.dense)
        sparse_query: dict[str, list[int] | list[float]] = {
            "indices": list(embedded.sparse.indices),
            "values": list(embedded.sparse.values),
        }
        if mode is RetrievalMode.DENSE:
            return await self._safe_search(
                "dense",
                collection,
                vectors.content.corpus_release_id,
                vectors.content.dense.name,
                dense_query,
                case,
                evidence,
                limit=candidate_limit,
            )
        if mode is RetrievalMode.SPARSE:
            return await self._safe_search(
                "sparse",
                collection,
                vectors.content.corpus_release_id,
                vectors.content.sparse.name,
                sparse_query,
                case,
                evidence,
                limit=candidate_limit,
            )
        dense, dense_failures = await self._safe_search(
            "dense",
            collection,
            vectors.content.corpus_release_id,
            vectors.content.dense.name,
            dense_query,
            case,
            evidence,
            limit=candidate_limit,
        )
        sparse, sparse_failures = await self._safe_search(
            "sparse",
            collection,
            vectors.content.corpus_release_id,
            vectors.content.sparse.name,
            sparse_query,
            case,
            evidence,
            limit=candidate_limit,
        )
        fused = weighted_rrf(
            {"dense": dense, "sparse": sparse},
            weights={"dense": rrf_weights.dense, "sparse": rrf_weights.sparse},
            rrf_k=rrf_k,
            limit=candidate_limit,
        )
        return fused, dense_failures + sparse_failures

    async def _safe_search(
        self,
        lane: str,
        collection: str,
        corpus_release_id: str,
        vector_name: str,
        query: list[float] | dict[str, list[int] | list[float]],
        case: BenchmarkCase,
        evidence: Mapping[str, CorpusEvidenceRecord],
        *,
        limit: int,
    ) -> tuple[list[_Candidate], tuple[str, ...]]:
        try:
            candidates = await self._search(
                collection,
                corpus_release_id,
                vector_name,
                query,
                case,
                evidence,
                limit=limit,
            )
        except Exception as error:
            return [], (f"{lane.upper()}_LANE_FAILURE:{error.__class__.__name__}",)
        return candidates, ()

    async def _search(
        self,
        collection: str,
        corpus_release_id: str,
        vector_name: str,
        query: list[float] | dict[str, list[int] | list[float]],
        case: BenchmarkCase,
        evidence: Mapping[str, CorpusEvidenceRecord],
        *,
        limit: int,
    ) -> list[_Candidate]:
        request = {
            "query": query,
            "using": vector_name,
            "filter": self._filter_for_release(case, corpus_release_id),
            "limit": limit,
            "with_payload": _PAYLOAD_FIELDS,
            "with_vector": False,
        }
        raw = await self._qdrant.query_points(collection, request)
        if not isinstance(raw, list):
            raise BenchmarkExecutionError("Qdrant query result is not a list")
        candidates: list[_Candidate] = []
        seen: set[str] = set()
        for result in raw:
            payload = result.get("payload")
            evidence_id = payload.get("evidence_id") if isinstance(payload, dict) else None
            if not isinstance(evidence_id, str) or evidence_id not in evidence:
                raise BenchmarkExecutionError("Qdrant returned unknown or malformed evidence")
            if evidence_id in seen:
                raise BenchmarkExecutionError(
                    f"Qdrant repeated evidence in one ranking: {evidence_id}"
                )
            seen.add(evidence_id)
            record = evidence[evidence_id]
            if result.get("id") != stable_qdrant_point_id(evidence_id):
                raise BenchmarkExecutionError(
                    f"Qdrant point ID does not match evidence: {evidence_id}"
                )
            if (
                payload.get("corpus_release_id") != record.corpus_release_id
                or payload.get("approval_status") != "APPROVED"
                or payload.get("evidence_sha256") != record.sha256
                or payload.get("jurisdiction") != record.jurisdiction
                or payload.get("language") != record.language
                or payload.get("publisher_id") != record.publisher_id
                or payload.get("evidence_roles")
                != [role.value for role in record.evidence_roles]
            ):
                raise BenchmarkExecutionError(
                    f"Qdrant benchmark payload does not match evidence: {evidence_id}"
                )
            self._require_result_matches_filter(payload, case, evidence_id)
            score = result.get("score")
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
            ):
                raise BenchmarkExecutionError(
                    f"Qdrant score is missing or invalid: {evidence_id}"
                )
            candidates.append(_Candidate(evidence_id=evidence_id, score=float(score)))
        return candidates

    @staticmethod
    def _filter_for_release(
        case: BenchmarkCase, corpus_release_id: str
    ) -> dict[str, list[dict[str, Any]]]:
        must: list[dict[str, Any]] = [
            {"key": "corpus_release_id", "match": {"value": corpus_release_id}},
            {"key": "approval_status", "match": {"value": "APPROVED"}},
        ]
        filters = case.retrieval_filter
        for field_name, values in (
            ("jurisdiction", filters.jurisdictions),
            ("language", filters.languages),
            ("publisher_id", filters.publisher_ids),
            ("source_class", filters.source_classes),
        ):
            if values:
                must.append({"key": field_name, "match": {"any": list(values)}})
        return {"must": must}

    @staticmethod
    def _require_result_matches_filter(
        payload: dict[str, Any], case: BenchmarkCase, evidence_id: str
    ) -> None:
        filters = case.retrieval_filter
        for field_name, allowed in (
            ("jurisdiction", filters.jurisdictions),
            ("language", filters.languages),
            ("publisher_id", filters.publisher_ids),
            ("source_class", filters.source_classes),
        ):
            if allowed and payload.get(field_name) not in allowed:
                raise BenchmarkExecutionError(
                    f"Qdrant result escaped {field_name} filter: {evidence_id}"
                )

    @staticmethod
    def _metrics(
        case: BenchmarkCase,
        retrieved: tuple[RetrievedEvidence, ...],
        evidence: Mapping[str, CorpusEvidenceRecord],
        *,
        top_k: int,
        latency_ms: float,
    ) -> BenchmarkCaseMetrics:
        grades = {item.evidence_id: item.relevance_grade for item in case.gold_evidence}
        retrieved_ids = [item.evidence_id for item in retrieved]
        if case.evidence_expectation is EvidenceExpectation.INSUFFICIENT_EVIDENCE:
            correct = not retrieved_ids
            return BenchmarkCaseMetrics(
                recall_at_k=1.0 if correct else 0.0,
                ndcg_at_k=1.0 if correct else 0.0,
                reciprocal_rank=1.0 if correct else 0.0,
                context_precision_at_k=1.0 if correct else 0.0,
                complete_evidence_set_recalled=correct,
                required_role_recall=1.0,
                forbidden_leakage_ids=tuple(
                    sorted(set(retrieved_ids).intersection(case.forbidden_evidence_ids))
                ),
                insufficient_evidence_correct=correct,
                latency_ms=latency_ms,
            )
        relevant = [item for item in retrieved_ids if item in grades]
        recall = len(set(relevant)) / len(grades)
        precision = len(relevant) / len(retrieved) if retrieved else 0.0
        first_relevant = next(
            (rank for rank, item in enumerate(retrieved_ids, start=1) if item in grades),
            None,
        )
        reciprocal_rank = 1.0 / first_relevant if first_relevant is not None else 0.0
        dcg = sum(
            (2 ** grades.get(item, 0) - 1) / math.log2(rank + 1)
            for rank, item in enumerate(retrieved_ids, start=1)
        )
        ideal_grades = sorted(grades.values(), reverse=True)[:top_k]
        ideal_dcg = sum(
            (2**grade - 1) / math.log2(rank + 1)
            for rank, grade in enumerate(ideal_grades, start=1)
        )
        retrieved_gold_roles = {
            role
            for evidence_id in relevant
            for role in evidence[evidence_id].evidence_roles
        }
        role_recall = (
            len(set(case.required_evidence_roles).intersection(retrieved_gold_roles))
            / len(case.required_evidence_roles)
            if case.required_evidence_roles
            else 1.0
        )
        forbidden = tuple(sorted(set(retrieved_ids).intersection(case.forbidden_evidence_ids)))
        complete_sets = case.complete_evidence_sets()
        return BenchmarkCaseMetrics(
            recall_at_k=recall,
            ndcg_at_k=dcg / ideal_dcg if ideal_dcg else 0.0,
            reciprocal_rank=reciprocal_rank,
            context_precision_at_k=precision,
            complete_evidence_set_recalled=any(
                evidence_set.issubset(retrieved_ids) for evidence_set in complete_sets
            ),
            required_role_recall=role_recall,
            forbidden_leakage_ids=forbidden,
            insufficient_evidence_correct=None,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _summarize(
        mode: RetrievalMode, results: list[BenchmarkCaseResult]
    ) -> BenchmarkModeSummary:
        if not results:
            raise BenchmarkExecutionError(f"benchmark mode has no cases: {mode.value}")
        metrics = [item.metrics for item in results]
        latencies = sorted(item.latency_ms for item in metrics)
        p95_index = max(0, math.ceil(0.95 * len(latencies)) - 1)
        insufficient = [
            item.insufficient_evidence_correct
            for item in metrics
            if item.insufficient_evidence_correct is not None
        ]
        complete_successes = sum(item.complete_evidence_set_recalled for item in metrics)
        return BenchmarkModeSummary(
            mode=mode,
            case_count=len(results),
            mean_recall_at_k=fmean(item.recall_at_k for item in metrics),
            mean_ndcg_at_k=fmean(item.ndcg_at_k for item in metrics),
            mean_reciprocal_rank=fmean(item.reciprocal_rank for item in metrics),
            mean_context_precision_at_k=fmean(
                item.context_precision_at_k for item in metrics
            ),
            complete_evidence_set_rate=fmean(
                float(item.complete_evidence_set_recalled) for item in metrics
            ),
            mean_required_role_recall=fmean(
                item.required_role_recall for item in metrics
            ),
            forbidden_leakage_case_count=sum(
                bool(item.forbidden_leakage_ids) for item in metrics
            ),
            candidate_failure_case_count=sum(bool(item.candidate_failures) for item in results),
            insufficient_evidence_case_count=len(insufficient),
            insufficient_evidence_accuracy=(
                fmean(float(item) for item in insufficient) if insufficient else None
            ),
            complete_evidence_set_confidence=RetrievalBenchmarkRunner._wilson_interval(
                complete_successes, len(metrics)
            ),
            insufficient_evidence_confidence=(
                RetrievalBenchmarkRunner._wilson_interval(
                    sum(bool(item) for item in insufficient), len(insufficient)
                )
                if insufficient
                else None
            ),
            p95_latency_ms=latencies[p95_index],
        )

    @staticmethod
    def _wilson_interval(successes: int, sample_count: int) -> MetricConfidenceInterval:
        if sample_count == 0:
            return MetricConfidenceInterval(
                sample_count=0,
                successes=0,
                lower=0.0,
                upper=1.0,
            )
        z = 1.959963984540054
        proportion = successes / sample_count
        denominator = 1 + z * z / sample_count
        center = (proportion + z * z / (2 * sample_count)) / denominator
        margin = (
            z
            * math.sqrt(
                proportion * (1 - proportion) / sample_count
                + z * z / (4 * sample_count * sample_count)
            )
            / denominator
        )
        return MetricConfidenceInterval(
            sample_count=sample_count,
            successes=successes,
            lower=max(0.0, center - margin),
            upper=min(1.0, center + margin),
        )

    @staticmethod
    def _adjudication_summary(suite: BenchmarkSuite) -> BenchmarkAdjudicationSummary:
        adjudications = [
            case.adjudication
            for case in suite.content.cases
            if case.adjudication is not None
        ]
        disagreement_count = sum(
            item.disagreement_observed for item in adjudications
        )
        return BenchmarkAdjudicationSummary(
            case_count=len(suite.content.cases),
            adjudicated_case_count=len(adjudications),
            independent_review_count=sum(len(item.reviews) for item in adjudications),
            disagreement_case_count=disagreement_count,
            disagreement_rate=(
                disagreement_count / len(adjudications) if adjudications else None
            ),
        )

    @classmethod
    def _summarize_strata(
        cls,
        results: list[BenchmarkCaseResult],
        suite: BenchmarkSuite,
    ) -> tuple[BenchmarkStratumSummary, ...]:
        cases = {case.case_id: case for case in suite.content.cases}
        summaries: list[BenchmarkStratumSummary] = []
        for threshold in suite.content.acceptance.safety_strata:
            matched = [
                result
                for result in results
                if cls._case_matches_threshold(cases[result.case_id], threshold)
            ]
            metrics = [result.metrics for result in matched]
            insufficient = [
                item.insufficient_evidence_correct
                for item in metrics
                if item.insufficient_evidence_correct is not None
            ]
            complete_successes = sum(
                item.complete_evidence_set_recalled for item in metrics
            )
            summaries.append(
                BenchmarkStratumSummary(
                    stratum_key=threshold.stratum_key,
                    stratum_value=threshold.stratum_value,
                    case_count=len(metrics),
                    mean_recall_at_k=fmean(item.recall_at_k for item in metrics),
                    complete_evidence_set_rate=fmean(
                        float(item.complete_evidence_set_recalled) for item in metrics
                    ),
                    mean_required_role_recall=fmean(
                        item.required_role_recall for item in metrics
                    ),
                    forbidden_leakage_case_count=sum(
                        bool(item.forbidden_leakage_ids) for item in metrics
                    ),
                    insufficient_evidence_case_count=len(insufficient),
                    insufficient_evidence_accuracy=(
                        fmean(float(item) for item in insufficient)
                        if insufficient
                        else None
                    ),
                    complete_evidence_set_confidence=cls._wilson_interval(
                        complete_successes, len(metrics)
                    ),
                    insufficient_evidence_confidence=(
                        cls._wilson_interval(
                            sum(bool(item) for item in insufficient), len(insufficient)
                        )
                        if insufficient
                        else None
                    ),
                )
            )
        return tuple(summaries)

    @staticmethod
    def _acceptance_blockers(
        summary: BenchmarkModeSummary,
        strata: tuple[BenchmarkStratumSummary, ...],
        suite: BenchmarkSuite,
    ) -> tuple[str, ...]:
        acceptance = suite.content.acceptance
        blockers: list[str] = []
        if summary.case_count < acceptance.minimum_total_case_count:
            blockers.append("TOTAL_CASE_COUNT_BELOW_THRESHOLD")
        for actual, required, code in (
            (
                summary.mean_recall_at_k,
                acceptance.minimum_mean_recall_at_k,
                "MEAN_RECALL_AT_K_BELOW_THRESHOLD",
            ),
            (
                summary.mean_ndcg_at_k,
                acceptance.minimum_mean_ndcg_at_k,
                "MEAN_NDCG_AT_K_BELOW_THRESHOLD",
            ),
            (
                summary.mean_reciprocal_rank,
                acceptance.minimum_mean_reciprocal_rank,
                "MEAN_RECIPROCAL_RANK_BELOW_THRESHOLD",
            ),
            (
                summary.mean_context_precision_at_k,
                acceptance.minimum_mean_context_precision_at_k,
                "MEAN_CONTEXT_PRECISION_AT_K_BELOW_THRESHOLD",
            ),
            (
                summary.complete_evidence_set_rate,
                acceptance.minimum_complete_evidence_set_rate,
                "COMPLETE_EVIDENCE_SET_RATE_BELOW_THRESHOLD",
            ),
            (
                summary.mean_required_role_recall,
                acceptance.minimum_mean_required_role_recall,
                "REQUIRED_ROLE_RECALL_BELOW_THRESHOLD",
            ),
        ):
            if actual < required:
                blockers.append(code)
        if (
            summary.forbidden_leakage_case_count
            > acceptance.maximum_forbidden_leakage_cases
        ):
            blockers.append("FORBIDDEN_EVIDENCE_LEAKAGE_EXCEEDS_THRESHOLD")
        if summary.p95_latency_ms > acceptance.maximum_p95_latency_ms:
            blockers.append("P95_LATENCY_EXCEEDS_THRESHOLD")
        if summary.candidate_failure_case_count > acceptance.maximum_candidate_failure_cases:
            blockers.append("CANDIDATE_FAILURE_CASES_EXCEED_THRESHOLD")
        if (
            summary.insufficient_evidence_accuracy is not None
            and summary.insufficient_evidence_accuracy
            < acceptance.minimum_insufficient_evidence_accuracy
        ):
            blockers.append("INSUFFICIENT_EVIDENCE_ACCURACY_BELOW_THRESHOLD")
        summaries = {
            (item.stratum_key, item.stratum_value): item for item in strata
        }
        for threshold in acceptance.safety_strata:
            key = (threshold.stratum_key, threshold.stratum_value)
            stratum = summaries[key]
            prefix = f"STRATUM[{threshold.stratum_key}={threshold.stratum_value}]"
            if stratum.case_count < threshold.minimum_case_count:
                blockers.append(f"{prefix}:CASE_COUNT_BELOW_THRESHOLD")
            if stratum.mean_recall_at_k < threshold.minimum_mean_recall_at_k:
                blockers.append(f"{prefix}:MEAN_RECALL_AT_K_BELOW_THRESHOLD")
            if (
                stratum.complete_evidence_set_rate
                < threshold.minimum_complete_evidence_set_rate
            ):
                blockers.append(f"{prefix}:COMPLETE_EVIDENCE_SET_RATE_BELOW_THRESHOLD")
            if (
                stratum.mean_required_role_recall
                < threshold.minimum_mean_required_role_recall
            ):
                blockers.append(f"{prefix}:REQUIRED_ROLE_RECALL_BELOW_THRESHOLD")
            if (
                stratum.forbidden_leakage_case_count
                > threshold.maximum_forbidden_leakage_cases
            ):
                blockers.append(f"{prefix}:FORBIDDEN_EVIDENCE_LEAKAGE_EXCEEDS_THRESHOLD")
            if (
                stratum.insufficient_evidence_accuracy is not None
                and stratum.insufficient_evidence_accuracy
                < threshold.minimum_insufficient_evidence_accuracy
            ):
                blockers.append(
                    f"{prefix}:INSUFFICIENT_EVIDENCE_ACCURACY_BELOW_THRESHOLD"
                )
        return tuple(blockers)
