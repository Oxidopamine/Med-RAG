"""Retrieval mechanics shared by the benchmark runner and the serving API.

The benchmark exists to predict how serving behaves. That prediction is only worth
something if both run the same code, so lane search, fusion, floor preservation,
conflict-side selection, and reranking live here and have exactly one implementation.
A serving path that re-derived any of them would measure one system and ship another.

Everything in this module is driven by the sealed retrieval candidate: lane vector
names, fusion weights, ``rrf_k``, candidate pool width, and output depth are inputs,
never local defaults. The only knowledge the module keeps for itself is how a lane is
executed and how rankings are combined.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from app.corpus_steward.candidate_schemas import CandidateReranker
from app.corpus_steward.qdrant_index import stable_qdrant_point_id
from app.corpus_steward.query_expansion import (
    ExpandedQuery,
    QueryExpansionKind,
    QueryExpansionOrigin,
)
from app.corpus_steward.reranking import RerankerBackend
from app.schemas.corpus import CorpusEvidenceRecord, EvidenceRole

RETRIEVAL_PIPELINE_VERSION = "1.0.0"

PAYLOAD_FIELDS = [
    "approval_status",
    "corpus_release_id",
    "evidence_id",
    "evidence_roles",
    "evidence_sha256",
    "jurisdiction",
    "language",
    "publisher_id",
    "source_class",
    "source_version_id",
    "lifecycle_status",
]
SAFETY_PRESERVATION_ROLES = (
    EvidenceRole.EXCEPTION_OR_CONTRAINDICATION,
    EvidenceRole.APPLICABILITY,
    EvidenceRole.DOSE_OR_THRESHOLD,
    EvidenceRole.MONITORING,
)
QUERY_EXPANSION_RRF_WEIGHTS = {
    QueryExpansionKind.EXACT_TERMINOLOGY: 0.5,
    QueryExpansionKind.SAFETY_QUERY: 1.0,
}


class RetrievalError(RuntimeError):
    """Raised when inputs or Qdrant results violate the retrieval boundary."""


class RetrievalStage(str, Enum):
    """Stages a caller may report while a single question is retrieved."""

    LANE_SEARCH = "LANE_SEARCH"
    EXPANSION_SEARCH = "EXPANSION_SEARCH"
    RERANK = "RERANK"


StageReporter = Callable[[RetrievalStage], Any]


class QueryPointsGateway(Protocol):
    async def query_points(
        self, collection: str, request: dict[str, Any]
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class Candidate:
    evidence_id: str
    score: float


@dataclass(frozen=True)
class LaneWeights:
    dense: float
    sparse: float


@dataclass(frozen=True)
class RetrievalFilter:
    """Payload constraints applied to every lane of one retrieval.

    Release and approval are not fields here because they are not negotiable per
    request: ``qdrant_filter`` adds them from the release the search is pinned to.
    """

    jurisdictions: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    publisher_ids: tuple[str, ...] = ()
    source_classes: tuple[str, ...] = ()
    source_version_ids: tuple[str, ...] = ()
    lifecycle_statuses: tuple[str, ...] = ()

    def _terms(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return (
            ("jurisdiction", self.jurisdictions),
            ("language", self.languages),
            ("publisher_id", self.publisher_ids),
            ("source_class", self.source_classes),
            ("source_version_id", self.source_version_ids),
            ("lifecycle_status", self.lifecycle_statuses),
        )

    def qdrant_filter(self, corpus_release_id: str) -> dict[str, list[dict[str, Any]]]:
        must: list[dict[str, Any]] = [
            {"key": "corpus_release_id", "match": {"value": corpus_release_id}},
            {"key": "approval_status", "match": {"value": "APPROVED"}},
        ]
        for field_name, values in self._terms():
            if values:
                must.append({"key": field_name, "match": {"any": list(values)}})
        return {"must": must}

    def escaped_field(self, payload: Mapping[str, Any]) -> str | None:
        """Return the first filter field a result violated, or None."""

        for field_name, allowed in self._terms():
            if allowed and payload.get(field_name) not in allowed:
                return field_name
        return None


class LaneSearcher:
    """Executes one vector lane against one release-pinned collection.

    Every returned point is checked against the canonical evidence record before it is
    allowed to become a candidate. A payload that disagrees with the release is not a
    ranking problem to be down-weighted; it means the index and the corpus disagree,
    and the lane fails closed.
    """

    def __init__(
        self,
        qdrant: QueryPointsGateway,
        evidence: Mapping[str, CorpusEvidenceRecord],
        *,
        collection: str,
        corpus_release_id: str,
        retrieval_filter: RetrievalFilter,
    ) -> None:
        self._qdrant = qdrant
        self._evidence = evidence
        self._collection = collection
        self._corpus_release_id = corpus_release_id
        self._filter = retrieval_filter

    @property
    def collection(self) -> str:
        return self._collection

    async def safe_search(
        self,
        lane: str,
        vector_name: str,
        query: list[float] | dict[str, list[int] | list[float]],
        *,
        limit: int,
    ) -> tuple[list[Candidate], tuple[str, ...]]:
        """Run one lane, converting its failure into an isolated, reported failure."""

        try:
            return await self.search(lane, vector_name, query, limit=limit), ()
        except Exception as error:
            return [], (f"{lane.upper()}_LANE_FAILURE:{error.__class__.__name__}",)

    async def search(
        self,
        lane: str,
        vector_name: str,
        query: list[float] | dict[str, list[int] | list[float]],
        *,
        limit: int,
    ) -> list[Candidate]:
        request = {
            "query": query,
            "using": vector_name,
            "filter": self._filter.qdrant_filter(self._corpus_release_id),
            "limit": limit,
            "with_payload": PAYLOAD_FIELDS,
            "with_vector": False,
        }
        raw = await self._qdrant.query_points(self._collection, request)
        if not isinstance(raw, list):
            raise RetrievalError("Qdrant query result is not a list")
        candidates: list[Candidate] = []
        seen: set[str] = set()
        for result in raw:
            payload = result.get("payload")
            evidence_id = payload.get("evidence_id") if isinstance(payload, dict) else None
            if not isinstance(evidence_id, str) or evidence_id not in self._evidence:
                raise RetrievalError("Qdrant returned unknown or malformed evidence")
            if evidence_id in seen:
                raise RetrievalError(f"Qdrant repeated evidence in one ranking: {evidence_id}")
            seen.add(evidence_id)
            record = self._evidence[evidence_id]
            if result.get("id") != stable_qdrant_point_id(evidence_id):
                raise RetrievalError(f"Qdrant point ID does not match evidence: {evidence_id}")
            if (
                payload.get("corpus_release_id") != record.corpus_release_id
                or payload.get("approval_status") != "APPROVED"
                or payload.get("evidence_sha256") != record.sha256
                or payload.get("jurisdiction") != record.jurisdiction
                or payload.get("language") != record.language
                or payload.get("publisher_id") != record.publisher_id
                or payload.get("source_version_id") != record.source_version_id
                or payload.get("lifecycle_status") != record.lifecycle_status.value
                or payload.get("evidence_roles")
                != [role.value for role in record.evidence_roles]
            ):
                raise RetrievalError(f"Qdrant payload does not match evidence: {evidence_id}")
            if escaped := self._filter.escaped_field(payload):
                raise RetrievalError(f"Qdrant result escaped {escaped} filter: {evidence_id}")
            score = result.get("score")
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
            ):
                raise RetrievalError(f"Qdrant score is missing or invalid: {evidence_id}")
            candidates.append(Candidate(evidence_id=evidence_id, score=float(score)))
        return candidates


def weighted_rrf(
    rankings: Mapping[str, list[Candidate]],
    *,
    weights: Mapping[str, float],
    rrf_k: int,
    limit: int,
) -> list[Candidate]:
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
        Candidate(evidence_id=evidence_id, score=score)
        for evidence_id, score in sorted(
            scores.items(), key=lambda item: (-item[1], item[0])
        )[:limit]
    ]


def lexically_redundant(left: str, right: str) -> bool:
    tokens_left = set(re.findall(r"[\w.-]+", left.casefold()))
    tokens_right = set(re.findall(r"[\w.-]+", right.casefold()))
    if not tokens_left or not tokens_right:
        return False
    return len(tokens_left & tokens_right) / min(len(tokens_left), len(tokens_right)) >= 0.85


def preserve_retrieval_floor(
    expanded: list[Candidate],
    baseline: list[Candidate],
    evidence: Mapping[str, CorpusEvidenceRecord],
    *,
    output_depth: int,
    limit: int,
) -> list[Candidate]:
    """Keep a bounded baseline floor while allowing expansion candidates to enter."""

    mandatory: list[str] = []
    retrieval_floor = min(output_depth, max(2, output_depth // 2))
    mandatory.extend(item.evidence_id for item in baseline[:retrieval_floor])
    baseline_output = baseline[:output_depth]
    for role in SAFETY_PRESERVATION_ROLES:
        representative = next(
            (
                item.evidence_id
                for item in baseline_output
                if role in evidence[item.evidence_id].evidence_roles
            ),
            None,
        )
        if representative is not None and representative not in mandatory:
            mandatory.append(representative)
    mandatory = mandatory[:output_depth]
    selected = set(mandatory)
    for item in expanded:
        if len(selected) >= output_depth:
            break
        selected.add(item.evidence_id)
    head = [item for item in expanded if item.evidence_id in selected]
    head_ids = {item.evidence_id for item in head}
    score_by_id = {item.evidence_id: item.score for item in baseline}
    for evidence_id in mandatory:
        if evidence_id not in head_ids:
            head.append(Candidate(evidence_id=evidence_id, score=score_by_id[evidence_id]))
    return (head + [item for item in expanded if item.evidence_id not in selected])[:limit]


def conflict_aware_selection(
    fused: list[Candidate],
    expansion_rankings: Mapping[str, list[Candidate]],
    evidence: Mapping[str, CorpusEvidenceRecord],
    *,
    conflict_side_lanes: tuple[str, ...],
    output_depth: int,
    limit: int,
) -> tuple[list[Candidate], tuple[str, ...]]:
    """Select a deterministic conflict-complete, diverse, non-redundant head."""

    selected: list[Candidate] = []
    selected_ids: set[str] = set()
    rules: list[str] = []

    def add(item: Candidate, rule: str) -> None:
        if len(selected) < output_depth and item.evidence_id not in selected_ids:
            selected.append(item)
            selected_ids.add(item.evidence_id)
            rules.append(f"{rule}:{item.evidence_id}")

    # Each side of an explicit conflict query needs its own representatives, so both
    # halves of the disagreement survive selection. Lanes are identified by the
    # expander's declared origin rather than by position or lane-name parsing.
    for lane in conflict_side_lanes:
        if ranking := expansion_rankings.get(lane):
            for item in ranking[:2]:
                add(item, f"CONFLICT_SIDE[{lane}]")

    for role in (EvidenceRole.PRIMARY_SUPPORT, *SAFETY_PRESERVATION_ROLES):
        representative = next(
            (
                item
                for item in fused[:output_depth]
                if role in evidence[item.evidence_id].evidence_roles
            ),
            None,
        )
        if representative is not None:
            add(representative, f"ROLE[{role.value}]")

    # Prefer source/version diversity before filling from the fused order.
    seen_versions = {evidence[item.evidence_id].source_version_id for item in selected}
    for item in fused:
        if len(seen_versions) >= min(3, output_depth):
            break
        version = evidence[item.evidence_id].source_version_id
        if version not in seen_versions:
            add(item, f"SOURCE_VERSION[{version}]")
            seen_versions.add(version)

    deferred: list[Candidate] = []
    for item in fused:
        if item.evidence_id in selected_ids:
            continue
        if any(
            lexically_redundant(
                evidence[item.evidence_id].content_search,
                evidence[kept.evidence_id].content_search,
            )
            for kept in selected
        ):
            deferred.append(item)
            continue
        add(item, "NON_REDUNDANT_FUSED")
    for item in deferred:
        add(item, "REDUNDANT_BACKFILL")

    tail = [item for item in fused if item.evidence_id not in selected_ids]
    return (selected + tail)[:limit], tuple(rules)


async def rerank_candidates(
    reranker: RerankerBackend,
    question: str,
    candidates: list[Candidate],
    evidence: Mapping[str, CorpusEvidenceRecord],
    *,
    output_depth: int,
    preserve_safety_roles: bool,
) -> list[Candidate]:
    scores = tuple(
        await reranker.score(
            question,
            tuple(evidence[item.evidence_id].content_search for item in candidates),
        )
    )
    if len(scores) != len(candidates):
        raise RetrievalError("reranker returned a different number of scores than candidates")
    reranked: list[Candidate] = []
    for item, score in zip(candidates, scores, strict=True):
        converted = float(score)
        if not math.isfinite(converted):
            raise RetrievalError("reranker returned a non-finite score")
        reranked.append(Candidate(evidence_id=item.evidence_id, score=converted))
    reranked.sort(key=lambda item: (-item.score, item.evidence_id))
    if not preserve_safety_roles:
        return reranked[:output_depth]

    reserved: list[str] = []
    retrieval_floor = min(output_depth, max(2, output_depth // 2))
    for item in candidates[:retrieval_floor]:
        if item.evidence_id not in reserved:
            reserved.append(item.evidence_id)
    for role in SAFETY_PRESERVATION_ROLES:
        representative = next(
            (
                item.evidence_id
                for item in candidates
                if role in evidence[item.evidence_id].evidence_roles
            ),
            None,
        )
        if representative is not None and representative not in reserved:
            reserved.append(representative)
    selected = set(reserved[:output_depth])
    for item in reranked:
        if len(selected) >= output_depth:
            break
        selected.add(item.evidence_id)
    return [item for item in reranked if item.evidence_id in selected]


@dataclass(frozen=True)
class HybridRetrieval:
    """One hybrid retrieval, with the intermediate rankings kept for tracing."""

    candidates: list[Candidate]
    failures: tuple[str, ...]
    applied_rules: tuple[str, ...] = ()
    reranked: bool = False
    dense: list[Candidate] = field(default_factory=list)
    sparse: list[Candidate] = field(default_factory=list)
    expansion_rankings: dict[str, list[Candidate]] = field(default_factory=dict)
    fusion_rankings: dict[str, list[Candidate]] = field(default_factory=dict)
    fusion_weights: dict[str, float] = field(default_factory=dict)
    preselection: list[Candidate] = field(default_factory=list)


async def hybrid_retrieve(
    searcher: LaneSearcher,
    evidence: Mapping[str, CorpusEvidenceRecord],
    *,
    question: str,
    dense_vector_name: str,
    sparse_vector_name: str,
    dense_query: list[float],
    sparse_query: dict[str, list[int] | list[float]],
    expanded_sparse: Sequence[tuple[ExpandedQuery, dict[str, list[int] | list[float]]]],
    candidate_limit: int,
    output_depth: int,
    rrf_k: int,
    lane_weights: LaneWeights,
    reranker: RerankerBackend | None = None,
    reranker_config: CandidateReranker | None = None,
    conflict_aware: bool = False,
    is_conflict_question: bool = False,
    search_limit: int | None = None,
    stage: StageReporter | None = None,
) -> HybridRetrieval:
    """Run every lane of one sealed candidate and fuse them into one ranking."""

    lane_limit = max(candidate_limit, search_limit or 0)
    await _report(stage, RetrievalStage.LANE_SEARCH)
    dense_full, dense_failures = await searcher.safe_search(
        "dense", dense_vector_name, dense_query, limit=lane_limit
    )
    sparse_full, sparse_failures = await searcher.safe_search(
        "sparse", sparse_vector_name, sparse_query, limit=lane_limit
    )
    dense = dense_full[:candidate_limit]
    sparse = sparse_full[:candidate_limit]

    expansion_rankings: dict[str, list[Candidate]] = {}
    expansion_rankings_full: dict[str, list[Candidate]] = {}
    lexical_failures = sparse_failures
    if expanded_sparse:
        await _report(stage, RetrievalStage.EXPANSION_SEARCH)
    for expansion, vector in expanded_sparse:
        expanded, failures = await searcher.safe_search(
            expansion.lane_id, sparse_vector_name, vector, limit=lane_limit
        )
        expansion_rankings_full[expansion.lane_id] = expanded
        expansion_rankings[expansion.lane_id] = expanded[:candidate_limit]
        lexical_failures += failures

    base_fused = weighted_rrf(
        {"dense": dense, "sparse": sparse},
        weights={"dense": lane_weights.dense, "sparse": lane_weights.sparse},
        rrf_k=rrf_k,
        limit=candidate_limit,
    )
    fusion_rankings: dict[str, list[Candidate]] = {"dense": dense, "sparse": sparse}
    fusion_weights = {"dense": lane_weights.dense, "sparse": lane_weights.sparse}
    for kind, expansion_weight in QUERY_EXPANSION_RRF_WEIGHTS.items():
        kind_rankings = {
            expansion.lane_id: expansion_rankings[expansion.lane_id]
            for expansion, _ in expanded_sparse
            if expansion.kind is kind and expansion.lane_id in expansion_rankings
        }
        if not kind_rankings:
            continue
        expanded = weighted_rrf(
            kind_rankings,
            weights={lane: 1.0 for lane in kind_rankings},
            rrf_k=rrf_k,
            limit=candidate_limit,
        )
        lane = f"query-expansion-{kind.value.lower()}"
        fusion_rankings[lane] = expanded
        fusion_weights[lane] = lane_weights.sparse * expansion_weight
    fused = weighted_rrf(
        fusion_rankings, weights=fusion_weights, rrf_k=rrf_k, limit=candidate_limit
    )

    applied_rules: tuple[str, ...] = ()
    if expansion_rankings:
        fused = preserve_retrieval_floor(
            fused, base_fused, evidence, output_depth=output_depth, limit=candidate_limit
        )
        applied_rules = ("BASE_RETRIEVAL_FLOOR_AND_SAFETY_ROLES",)
    preselection = list(fused)
    conflict_side_lanes = tuple(
        expansion.lane_id
        for expansion, _ in expanded_sparse
        if expansion.origin is QueryExpansionOrigin.CONFLICT_SIDE
    )
    if (
        conflict_aware
        and is_conflict_question
        and candidate_limit
        and reranker_config is None
        and conflict_side_lanes
    ):
        fused, conflict_rules = conflict_aware_selection(
            fused,
            expansion_rankings,
            evidence,
            conflict_side_lanes=conflict_side_lanes,
            output_depth=output_depth,
            limit=candidate_limit,
        )
        applied_rules += conflict_rules

    reranked = False
    if reranker_config is not None:
        if reranker is None:
            raise RetrievalError("the sealed candidate configures a reranker that is not loaded")
        await _report(stage, RetrievalStage.RERANK)
        try:
            fused = await rerank_candidates(
                reranker,
                question,
                fused,
                evidence,
                output_depth=reranker_config.output_depth,
                preserve_safety_roles=reranker_config.safety_role_preservation,
            )
            reranked = True
        except Exception as error:
            return HybridRetrieval(
                candidates=fused,
                failures=(
                    dense_failures
                    + lexical_failures
                    + (f"RERANKER_STAGE_FAILURE:{error.__class__.__name__}",)
                ),
                applied_rules=applied_rules,
                dense=dense_full,
                sparse=sparse_full,
                expansion_rankings=expansion_rankings_full,
                fusion_rankings=fusion_rankings,
                fusion_weights=fusion_weights,
                preselection=preselection,
            )

    return HybridRetrieval(
        candidates=fused,
        failures=dense_failures + lexical_failures,
        applied_rules=applied_rules,
        reranked=reranked,
        dense=dense_full,
        sparse=sparse_full,
        expansion_rankings=expansion_rankings_full,
        fusion_rankings=fusion_rankings,
        fusion_weights=fusion_weights,
        preselection=preselection,
    )


async def _report(stage: StageReporter | None, value: RetrievalStage) -> None:
    if stage is None:
        return
    result = stage(value)
    if result is not None and hasattr(result, "__await__"):
        await result
