"""Release-bound sparse, dense, and hybrid Qdrant retrieval benchmarks."""

from __future__ import annotations

import math
from collections.abc import Mapping
from statistics import fmean
from time import perf_counter
from typing import Any, Protocol

from app.corpus_steward.benchmark_schemas import (
    BenchmarkCase,
    BenchmarkCaseMetrics,
    BenchmarkCaseResult,
    BenchmarkModeSummary,
    BenchmarkProvenanceMode,
    BenchmarkProvenanceSummary,
    BenchmarkReport,
    BenchmarkReportContent,
    BenchmarkStratumSummary,
    BenchmarkSuite,
    BenchmarkSuiteContent,
    BenchmarkSuitePartition,
    EvidenceExpectation,
    MetricConfidenceInterval,
    RetrievalMode,
    RetrievedEvidence,
    RRFWeights,
    SafetyStratumThreshold,
)
from app.corpus_steward.candidate_schemas import (
    CandidateLaneKind,
    CandidateReranker,
    RetrievalCandidateManifest,
)
from app.corpus_steward.index_schemas import IndexVectorBatch, SparseVector
from app.corpus_steward.qdrant_index import candidate_qdrant_collection
from app.corpus_steward.query_expansion import (
    CONFLICT_AWARE_QUERY_REVISION,
    DeterministicQueryExpander,
    ExpandedQuery,
    QueryExpansionError,
    is_conflict_query,
)
from app.corpus_steward.reranking import RerankerBackend
from app.corpus_steward.vector_producer import EmbeddedText, EmbeddingBackend
from app.retrieval.pipeline import (
    Candidate as _Candidate,
)
from app.retrieval.pipeline import (
    HybridRetrieval,
    LaneSearcher,
    LaneWeights,
    RetrievalError,
    RetrievalFilter,
    hybrid_retrieve,
)
from app.schemas.corpus import CorpusEvidenceRecord, CorpusReleaseBundle
from app.schemas.domain import utc_now

BENCHMARK_RUNNER_VERSION = "1.6.0"


class BenchmarkExecutionError(RetrievalError):
    """Raised when inputs or Qdrant results violate the benchmark boundary."""


class BenchmarkQdrant(Protocol):
    async def query_points(
        self, collection: str, request: dict[str, Any]
    ) -> list[dict[str, Any]]: ...


def derive_development_suite_for_candidate(
    suite: BenchmarkSuite,
    candidate: RetrievalCandidateManifest,
) -> BenchmarkSuite:
    """Re-seal one development suite with a candidate's retrieval-depth contract."""

    content = suite.content
    if content.suite_partition is not BenchmarkSuitePartition.DEVELOPMENT:
        raise ValueError("only development suites may be derived for candidate comparison")
    if content.provenance_mode is not BenchmarkProvenanceMode.AUTOMATED_SOURCE_DERIVED:
        raise ValueError(
            "candidate comparison derivation requires an automated source-derived suite"
        )
    # Only the evaluation depth is stamped into the suite. Fusion parameters and pool
    # width stay on the candidate, so re-tuning them does not produce a new suite digest
    # and does not require regenerating the benchmark they are judged against.
    derived = content.model_copy(
        update={
            "candidate_configuration_sha256": candidate.candidate_sha256,
            "top_k": candidate.content.output_depth,
        }
    )
    return BenchmarkSuite.seal(BenchmarkSuiteContent.model_validate(derived))


class RetrievalBenchmarkRunner:
    def __init__(
        self,
        qdrant: BenchmarkQdrant,
        backend: EmbeddingBackend,
        *,
        reranker: RerankerBackend | None = None,
        clock=perf_counter,
        development_trace_case_ids: frozenset[str] = frozenset(),
        development_trace_depth: int = 100,
    ) -> None:
        self._qdrant = qdrant
        self._backend = backend
        self._reranker = reranker
        self._clock = clock
        if development_trace_depth <= 0:
            raise ValueError("development trace depth must be positive")
        self._development_trace_case_ids = development_trace_case_ids
        self._development_trace_depth = development_trace_depth
        self.development_traces: list[dict[str, Any]] = []

    async def run(
        self,
        bundle: CorpusReleaseBundle,
        vectors: IndexVectorBatch,
        suite: BenchmarkSuite,
        candidate: RetrievalCandidateManifest,
    ) -> BenchmarkReport:
        evidence = self._validate_inputs(bundle, vectors, suite, candidate)
        content = suite.content
        candidate_weights = RRFWeights(
            dense=candidate.content.lane(CandidateLaneKind.DENSE).weight,
            sparse=candidate.content.lane(CandidateLaneKind.BM25).weight,
        )
        if self._development_trace_case_ids:
            if content.suite_partition is not BenchmarkSuitePartition.DEVELOPMENT:
                raise BenchmarkExecutionError(
                    "retrieval traces are restricted to development suites"
                )
            suite_case_ids = {item.case_id for item in content.cases}
            if unknown := self._development_trace_case_ids - suite_case_ids:
                raise BenchmarkExecutionError(
                    "development trace requested unknown cases: "
                    + ", ".join(sorted(unknown))
                )
        self.development_traces.clear()
        collection = candidate_qdrant_collection(bundle, candidate)
        try:
            expander = DeterministicQueryExpander(
                terminology_revision=candidate.content.terminology_revision,
                safety_query_revision=candidate.content.safety_query_revision,
            )
        except QueryExpansionError as error:
            raise BenchmarkExecutionError(str(error)) from error
        case_results: list[BenchmarkCaseResult] = []
        for case in content.cases:
            embed_started = self._clock()
            embedded = await self._embed_question(case.question)
            original_embedding_ms = max(
                0.0, (self._clock() - embed_started) * 1_000.0
            )
            expansion_started = self._clock()
            expanded_queries = expander.expand(case.question)
            expanded_sparse = await self._embed_sparse_expansions(expanded_queries)
            expansion_embedding_ms = max(
                0.0, (self._clock() - expansion_started) * 1_000.0
            )
            for mode in content.modes:
                started = self._clock()
                candidates, candidate_failures = await self._retrieve(
                    collection,
                    vectors,
                    case,
                    mode,
                    embedded,
                    expanded_sparse,
                    evidence,
                    candidate_limit=candidate.content.candidate_limit,
                    output_depth=content.top_k,
                    rrf_k=candidate.content.rrf_k,
                    rrf_weights=candidate_weights,
                    reranker_config=candidate.content.reranker,
                    conflict_aware=(
                        candidate.content.safety_query_revision
                        == CONFLICT_AWARE_QUERY_REVISION
                    ),
                    trace=(
                        case.case_id in self._development_trace_case_ids
                        and mode is RetrievalMode.HYBRID
                    ),
                )
                elapsed_ms = (
                    original_embedding_ms
                    + max(0.0, (self._clock() - started) * 1_000.0)
                    + (
                        expansion_embedding_ms
                        if mode is RetrievalMode.HYBRID
                        else 0.0
                    )
                )
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
                            generation_context_budget=(
                                content.acceptance.generation_context_budget
                                or content.top_k
                            ),
                            latency_ms=elapsed_ms,
                        ),
                        candidate_failures=candidate_failures,
                    )
                )

        summaries = tuple(
            self._summarize(
                mode,
                [result for result in case_results if result.mode is mode],
                generation_context_budget=(
                    content.acceptance.generation_context_budget or content.top_k
                ),
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
            provenance_mode=content.provenance_mode,
            access_policy_sha256=content.access_policy_sha256,
            adjudication_process_sha256=content.adjudication_process_sha256,
            adjudication_record_sha256=content.adjudication_record_sha256,
            generation_policy_sha256=content.generation_policy_sha256,
            generation_record_sha256=content.generation_record_sha256,
            threshold_policy_sha256=content.threshold_policy_sha256,
            benchmark_suite_sha256=suite.suite_sha256,
            candidate_configuration_sha256=candidate.candidate_sha256,
            corpus_release_id=content.corpus_release_id,
            manifest_sha256=content.manifest_sha256,
            vector_batch_sha256=vectors.batch_sha256,
            qdrant_collection=collection,
            top_k=content.top_k,
            candidate_limit=candidate.content.candidate_limit,
            rrf_k=candidate.content.rrf_k,
            rrf_weights=candidate_weights,
            candidate_mode=content.candidate_mode,
            case_results=tuple(case_results),
            mode_summaries=summaries,
            stratum_summaries=strata,
            provenance_summary=self._provenance_summary(suite),
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
        if (
            suite.content.candidate_configuration_sha256 is not None
            and suite.content.candidate_configuration_sha256 != candidate.candidate_sha256
        ):
            raise BenchmarkExecutionError(
                "benchmark suite candidate configuration digest does not match"
            )
        reranker_config = candidate.content.reranker
        if reranker_config is None and self._reranker is not None:
            raise BenchmarkExecutionError(
                "a reranker backend was supplied for a candidate without a reranker"
            )
        if reranker_config is not None:
            if self._reranker is None:
                raise BenchmarkExecutionError(
                    "candidate configures a reranker but no verified backend was supplied"
                )
            if self._reranker.model_reference != reranker_config.model:
                raise BenchmarkExecutionError(
                    "candidate reranker model does not match the verified backend"
                )
            if self._reranker.adapter_identity != (
                reranker_config.adapter_id,
                reranker_config.adapter_revision,
            ):
                raise BenchmarkExecutionError(
                    "candidate reranker adapter does not match the verified backend"
                )
            if self._reranker.instruction_sha256 != reranker_config.instruction_sha256:
                raise BenchmarkExecutionError(
                    "candidate reranker instruction does not match the verified backend"
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
        # Only the evaluation depth is a property of the measurement; fusion parameters
        # and pool width are properties of the system under test. Requiring the suite to
        # pin them made every fusion change a suite regeneration, which left post-
        # retrieval selection as the only tunable surface and is how template-keyed
        # output budgeting got written. The candidate configuration is authoritative for
        # everything except the depth the metrics are defined at.
        if candidate.content.output_depth != suite.content.top_k:
            raise BenchmarkExecutionError(
                "candidate output depth does not match the suite evaluation depth"
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
            and (
                not filters.source_version_ids
                or item.source_version_id in filters.source_version_ids
            )
            and (
                not filters.lifecycle_statuses
                or item.lifecycle_status.value in filters.lifecycle_statuses
            )
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

    async def _embed_sparse_expansions(
        self,
        expansions: tuple[ExpandedQuery, ...],
    ) -> tuple[tuple[ExpandedQuery, SparseVector], ...]:
        if not expansions:
            return ()
        sparse_method = getattr(self._backend, "embed_sparse_queries", None)
        sparse: list[SparseVector] = []
        for expansion in expansions:
            if sparse_method is None:
                embedded = tuple(
                    await self._backend.embed_queries((expansion.text,))
                )
                sparse.extend(item.sparse for item in embedded)
            else:
                sparse.extend(await sparse_method((expansion.text,)))
        if len(sparse) != len(expansions):
            raise BenchmarkExecutionError(
                "embedding backend returned the wrong number of expanded sparse queries"
            )
        for vector in sparse:
            try:
                if vector.indices[-1] >= self._backend.sparse_definition.dimension:
                    raise BenchmarkExecutionError(
                        "expanded sparse query has the wrong dimension"
                    )
            except IndexError as error:
                raise BenchmarkExecutionError(
                    "expanded sparse query vector is empty"
                ) from error
        return tuple(zip(expansions, sparse, strict=True))

    async def _retrieve(
        self,
        collection: str,
        vectors: IndexVectorBatch,
        case: BenchmarkCase,
        mode: RetrievalMode,
        embedded: EmbeddedText,
        expanded_sparse: tuple[tuple[ExpandedQuery, SparseVector], ...],
        evidence: Mapping[str, CorpusEvidenceRecord],
        *,
        candidate_limit: int,
        output_depth: int,
        rrf_k: int,
        rrf_weights: RRFWeights,
        reranker_config: CandidateReranker | None,
        conflict_aware: bool = False,
        trace: bool = False,
    ) -> tuple[list[_Candidate], tuple[str, ...]]:
        """Run one benchmark case through the shared serving retrieval pipeline."""

        searcher = LaneSearcher(
            self._qdrant,
            evidence,
            collection=collection,
            corpus_release_id=vectors.content.corpus_release_id,
            retrieval_filter=self._retrieval_filter(case),
        )
        dense_query = list(embedded.dense)
        sparse_query: dict[str, list[int] | list[float]] = {
            "indices": list(embedded.sparse.indices),
            "values": list(embedded.sparse.values),
        }
        if mode is RetrievalMode.DENSE:
            return await searcher.safe_search(
                "dense", vectors.content.dense.name, dense_query, limit=candidate_limit
            )
        if mode is RetrievalMode.SPARSE:
            return await searcher.safe_search(
                "sparse", vectors.content.sparse.name, sparse_query, limit=candidate_limit
            )
        retrieval = await hybrid_retrieve(
            searcher,
            evidence,
            question=case.question,
            dense_vector_name=vectors.content.dense.name,
            sparse_vector_name=vectors.content.sparse.name,
            dense_query=dense_query,
            sparse_query=sparse_query,
            expanded_sparse=tuple(
                (
                    expansion,
                    {"indices": list(vector.indices), "values": list(vector.values)},
                )
                for expansion, vector in expanded_sparse
            ),
            candidate_limit=candidate_limit,
            output_depth=output_depth,
            rrf_k=rrf_k,
            lane_weights=LaneWeights(dense=rrf_weights.dense, sparse=rrf_weights.sparse),
            reranker=self._reranker,
            reranker_config=reranker_config,
            conflict_aware=conflict_aware,
            is_conflict_question=is_conflict_query(case.question),
            search_limit=self._development_trace_depth if trace else None,
        )
        reranker_failed = any(
            failure.startswith("RERANKER_STAGE_FAILURE") for failure in retrieval.failures
        )
        if trace and not reranker_failed:
            self.development_traces.append(
                self._development_trace(
                    case,
                    retrieval,
                    rrf_k=rrf_k,
                    candidate_limit=candidate_limit,
                    output_depth=output_depth,
                )
            )
        return retrieval.candidates, retrieval.failures

    @staticmethod
    def _retrieval_filter(case: BenchmarkCase) -> RetrievalFilter:
        filters = case.retrieval_filter
        return RetrievalFilter(
            jurisdictions=filters.jurisdictions,
            languages=filters.languages,
            publisher_ids=filters.publisher_ids,
            source_classes=filters.source_classes,
            source_version_ids=filters.source_version_ids,
            lifecycle_statuses=filters.lifecycle_statuses,
        )

    @staticmethod
    def _development_trace(
        case: BenchmarkCase,
        retrieval: HybridRetrieval,
        *,
        rrf_k: int,
        candidate_limit: int,
        output_depth: int,
    ) -> dict[str, Any]:
        fusion_rankings = retrieval.fusion_rankings
        preselection = retrieval.preselection
        selected = retrieval.candidates
        weights = retrieval.fusion_weights
        applied_rules = retrieval.applied_rules
        lanes = {
            "dense": retrieval.dense,
            "sparse": retrieval.sparse,
            **retrieval.expansion_rankings,
        }
        gold_ids = tuple(item.evidence_id for item in case.gold_evidence)
        selected_ranks = {
            item.evidence_id: rank for rank, item in enumerate(selected, start=1)
        }
        preselection_ranks = {
            item.evidence_id: rank for rank, item in enumerate(preselection, start=1)
        }
        lane_ranks = {
            lane: {
                item.evidence_id: rank for rank, item in enumerate(ranking, start=1)
            }
            for lane, ranking in lanes.items()
        }
        fusion_lane_ranks = {
            lane: {
                item.evidence_id: rank for rank, item in enumerate(ranking, start=1)
            }
            for lane, ranking in fusion_rankings.items()
        }
        gold = []
        for evidence_id in gold_ids:
            ranks = {
                lane: ranks.get(evidence_id) for lane, ranks in lane_ranks.items()
            }
            contributions = {}
            for lane, fusion_ranks in fusion_lane_ranks.items():
                rank = fusion_ranks.get(evidence_id)
                contributions[lane] = (
                    weights[lane] / (rrf_k + rank) if rank is not None else 0.0
                )
            gold.append(
                {
                    "evidence_id": evidence_id,
                    "lane_ranks_through_trace_depth": ranks,
                    "rrf_contributions": contributions,
                    "preselection_rank": preselection_ranks.get(evidence_id),
                    "selected_rank": selected_ranks.get(evidence_id),
                }
            )
        missing = [
            item
            for item in gold
            if item["preselection_rank"] is None
            or item["preselection_rank"] > output_depth
        ]
        generation_failure = any(
            all(rank is None for rank in item["lane_ranks_through_trace_depth"].values())
            for item in missing
        )
        return {
            "case_id": case.case_id,
            "classification": (
                "CANDIDATE_GENERATION_FAILURE"
                if generation_failure
                else "TOP_10_SELECTION_FAILURE"
                if missing
                else "NO_FAILURE"
            ),
            "trace_depth": max((len(ranking) for ranking in lanes.values()), default=0),
            "candidate_limit": candidate_limit,
            "output_depth": output_depth,
            "gold": gold,
            "applied_preservation_rules": list(applied_rules),
            "lane_rankings": {
                lane: [item.evidence_id for item in ranking] for lane, ranking in lanes.items()
            },
            "fusion_lane_rankings": {
                lane: [item.evidence_id for item in ranking]
                for lane, ranking in fusion_rankings.items()
            },
        }

    @staticmethod
    def _metrics(
        case: BenchmarkCase,
        retrieved: tuple[RetrievedEvidence, ...],
        evidence: Mapping[str, CorpusEvidenceRecord],
        *,
        top_k: int,
        generation_context_budget: int,
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
                context_precision_ceiling_at_k=1.0,
                r_precision=1.0 if correct else 0.0,
                complete_evidence_set_recalled=correct,
                complete_evidence_set_recalled_at_budget=correct,
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
        # Precision at a fixed depth is bounded by min(|gold|, k)/k. Recording the
        # ceiling next to the score keeps an unreachable acceptance threshold visible
        # instead of presenting an optimal run as a quality gap.
        precision_ceiling = min(len(grades), top_k) / top_k
        r_depth = min(len(grades), top_k)
        r_precision = (
            len({item for item in retrieved_ids[:r_depth] if item in grades}) / r_depth
            if r_depth
            else 0.0
        )
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
            context_precision_ceiling_at_k=precision_ceiling,
            r_precision=r_precision,
            complete_evidence_set_recalled=any(
                evidence_set.issubset(retrieved_ids) for evidence_set in complete_sets
            ),
            # Evaluated at the depth the rendering layer will actually pass on, so the
            # gate reflects what the downstream verifier receives rather than how deep
            # the benchmark happened to search.
            complete_evidence_set_recalled_at_budget=any(
                evidence_set.issubset(retrieved_ids[:generation_context_budget])
                for evidence_set in complete_sets
            ),
            required_role_recall=role_recall,
            forbidden_leakage_ids=forbidden,
            insufficient_evidence_correct=None,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _summarize(
        mode: RetrievalMode,
        results: list[BenchmarkCaseResult],
        *,
        generation_context_budget: int,
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
        # Insufficient-evidence cases are scored by abstention and cannot be improved by
        # retrieval quality, so they are also summarized separately. Reporting only the
        # blended mean lets a large negative partition mask answerable-case regressions.
        answerable = [
            item for item in metrics if item.insufficient_evidence_correct is None
        ]
        answerable_successes = sum(item.complete_evidence_set_recalled for item in answerable)
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
            mean_context_precision_ceiling_at_k=fmean(
                item.context_precision_ceiling_at_k for item in metrics
            ),
            mean_r_precision=fmean(item.r_precision for item in metrics),
            complete_evidence_set_rate=fmean(
                float(item.complete_evidence_set_recalled) for item in metrics
            ),
            generation_context_budget=generation_context_budget,
            complete_evidence_at_budget_rate=fmean(
                float(item.complete_evidence_set_recalled_at_budget) for item in metrics
            ),
            mean_required_role_recall=fmean(
                item.required_role_recall for item in metrics
            ),
            forbidden_leakage_case_count=sum(
                bool(item.forbidden_leakage_ids) for item in metrics
            ),
            candidate_failure_case_count=sum(bool(item.candidate_failures) for item in results),
            answerable_case_count=len(answerable),
            answerable_mean_recall_at_k=(
                fmean(item.recall_at_k for item in answerable) if answerable else None
            ),
            answerable_mean_ndcg_at_k=(
                fmean(item.ndcg_at_k for item in answerable) if answerable else None
            ),
            answerable_mean_reciprocal_rank=(
                fmean(item.reciprocal_rank for item in answerable) if answerable else None
            ),
            answerable_mean_context_precision_at_k=(
                fmean(item.context_precision_at_k for item in answerable)
                if answerable
                else None
            ),
            answerable_mean_r_precision=(
                fmean(item.r_precision for item in answerable) if answerable else None
            ),
            answerable_complete_evidence_set_rate=(
                fmean(float(item.complete_evidence_set_recalled) for item in answerable)
                if answerable
                else None
            ),
            answerable_complete_evidence_at_budget_rate=(
                fmean(
                    float(item.complete_evidence_set_recalled_at_budget)
                    for item in answerable
                )
                if answerable
                else None
            ),
            answerable_mean_required_role_recall=(
                fmean(item.required_role_recall for item in answerable)
                if answerable
                else None
            ),
            answerable_complete_evidence_set_confidence=(
                RetrievalBenchmarkRunner._wilson_interval(
                    answerable_successes, len(answerable)
                )
                if answerable
                else None
            ),
            answerable_complete_evidence_at_budget_confidence=(
                RetrievalBenchmarkRunner._wilson_interval(
                    sum(
                        item.complete_evidence_set_recalled_at_budget
                        for item in answerable
                    ),
                    len(answerable),
                )
                if answerable
                else None
            ),
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
    def _provenance_summary(suite: BenchmarkSuite) -> BenchmarkProvenanceSummary:
        adjudications = [
            case.adjudication
            for case in suite.content.cases
            if case.adjudication is not None
        ]
        disagreement_count = sum(
            item.disagreement_observed for item in adjudications
        )
        automated = [
            case.automated_provenance
            for case in suite.content.cases
            if case.automated_provenance is not None
        ]
        source_evidence_ids = {
            reference.evidence_id
            for provenance in automated
            for reference in provenance.source_evidence
        }
        return BenchmarkProvenanceSummary(
            provenance_mode=suite.content.provenance_mode,
            case_count=len(suite.content.cases),
            adjudicated_case_count=len(adjudications),
            automated_case_count=len(automated),
            source_evidence_count=len(source_evidence_ids),
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
                summary.mean_r_precision,
                acceptance.minimum_mean_r_precision,
                "MEAN_R_PRECISION_BELOW_THRESHOLD",
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
            summary.answerable_mean_recall_at_k is not None
            and summary.answerable_mean_recall_at_k
            < acceptance.minimum_answerable_mean_recall_at_k
        ):
            blockers.append("ANSWERABLE_MEAN_RECALL_AT_K_BELOW_THRESHOLD")
        # Completeness gates are the ones a safety argument rests on, so they are
        # evaluated against the Wilson lower bound rather than the point estimate: the
        # claim being made is about the population, not about these 300 draws.
        for observed, interval, required, code in (
            (
                summary.answerable_complete_evidence_set_rate,
                summary.answerable_complete_evidence_set_confidence,
                acceptance.minimum_answerable_complete_evidence_set_rate,
                "ANSWERABLE_COMPLETE_EVIDENCE_SET_RATE_BELOW_THRESHOLD",
            ),
            (
                summary.answerable_complete_evidence_at_budget_rate,
                summary.answerable_complete_evidence_at_budget_confidence,
                acceptance.minimum_answerable_complete_evidence_at_budget_rate,
                "ANSWERABLE_COMPLETE_EVIDENCE_AT_BUDGET_BELOW_THRESHOLD",
            ),
        ):
            if observed is None or not required:
                continue
            if acceptance.require_confidence_lower_bound:
                if interval is None or interval.lower < required:
                    blockers.append(f"{code}_LOWER_BOUND")
            elif observed < required:
                blockers.append(code)
        # Predicate-comparator floors: the candidate must at least match baselines that
        # were measured on this same suite, which is a claim that survives a change of
        # corpus in a way that an absolute score floor does not. Two floors are checked
        # because they fail for different reasons - the tuned comparator catches a
        # regression against the best thing already built, the naive one catches a suite
        # that has stopped separating retrieval quality from lexical overlap.
        for floor, margin, code in (
            (
                acceptance.comparator_answerable_complete_evidence_rate,
                acceptance.minimum_comparator_margin,
                "BELOW_COMPARATOR_COMPLETE_EVIDENCE_FLOOR",
            ),
            (
                acceptance.naive_comparator_answerable_complete_evidence_rate,
                acceptance.minimum_naive_comparator_margin,
                "BELOW_NAIVE_COMPARATOR_COMPLETE_EVIDENCE_FLOOR",
            ),
        ):
            if (
                floor is not None
                and summary.answerable_complete_evidence_set_rate is not None
                and summary.answerable_complete_evidence_set_rate < floor + margin
            ):
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
