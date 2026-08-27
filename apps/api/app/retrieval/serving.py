"""The serving query path: active release in, grounded passages out.

This is the seam the API has never had. Everything downstream of it - the grounded
answer lane, claim verification, evidence rendering - already assumed a caller who
could hand it real retrieved passages from the active release, and there was no such
caller. The five progress statuses were timers.

Three properties are load-bearing here:

* Serving runs the sealed candidate the acceptance names, or it does not serve. The
  candidate digest, the collection identity derived from the release manifest, and the
  model artifacts behind each lane are all checked against the active release before a
  question reaches Qdrant. A process pointed at a different candidate, a different
  collection, or different weights is not a degraded system, it is an unmeasured one.
* Retrieval is release-scoped and approval-scoped in the filter, and every returned
  point is re-checked against the canonical record. The filter is the primary control;
  the payload check exists because an index and a corpus can disagree.
* Every reduction of the retrieved set is counted and reported. Restricted evidence is
  withheld from the model, unresolved evidence aborts the answer, and a failed lane
  abstains rather than answering from a pipeline nobody measured.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol

from app.corpus_steward.candidate_schemas import CandidateLaneKind, RetrievalCandidateManifest
from app.corpus_steward.qdrant_index import candidate_qdrant_collection_for_manifest
from app.corpus_steward.query_expansion import (
    CONFLICT_AWARE_QUERY_REVISION,
    DeterministicQueryExpander,
    QueryExpansionError,
    is_conflict_query,
)
from app.corpus_steward.reranking import RerankerBackend
from app.corpus_steward.vector_producer import EmbeddedText, EmbeddingBackend
from app.reasoning.answer_service import RetrievedPassage
from app.retrieval.pipeline import (
    Candidate,
    LaneSearcher,
    LaneWeights,
    QueryPointsGateway,
    RetrievalError,
    RetrievalFilter,
    RetrievalStage,
    StageReporter,
    hybrid_retrieve,
)
from app.schemas.corpus import CorpusEvidenceRecord, ServingReleaseBinding
from app.schemas.domain import utc_now
from app.schemas.questions import EvidenceDetail, SourceFilters

SERVING_RETRIEVAL_VERSION = "1.0.0"


class ServingAbstention(str, Enum):
    """Why a question never reached, or never survived, retrieval.

    These sit alongside the generation lane abstention reasons rather than inside them:
    a reader needs to know whether nothing was retrievable, nothing was configured, or
    the configuration disagreed with the release it claims to serve.
    """

    NO_ACTIVE_RELEASE = "NO_ACTIVE_RELEASE"
    RETRIEVAL_PIPELINE_NOT_CONFIGURED = "RETRIEVAL_PIPELINE_NOT_CONFIGURED"
    SERVING_BINDING_MISMATCH = "SERVING_BINDING_MISMATCH"
    BENCHMARK_ACCEPTANCE_STALE = "BENCHMARK_ACCEPTANCE_STALE"
    RELEASE_EVIDENCE_UNAVAILABLE = "RELEASE_EVIDENCE_UNAVAILABLE"
    UNKNOWN_SOURCE_ORGANIZATION = "UNKNOWN_SOURCE_ORGANIZATION"
    QUERY_ENCODING_FAILED = "QUERY_ENCODING_FAILED"
    RETRIEVAL_LANE_FAILURE = "RETRIEVAL_LANE_FAILURE"
    NO_EVIDENCE_RETRIEVED = "NO_EVIDENCE_RETRIEVED"
    EVIDENCE_DETAILS_UNAVAILABLE = "EVIDENCE_DETAILS_UNAVAILABLE"
    NO_RENDERABLE_EVIDENCE = "NO_RENDERABLE_EVIDENCE"


class ServingConfigurationError(RuntimeError):
    """Raised when a serving stack cannot be built from what is configured."""


@dataclass(frozen=True)
class ReleaseEvidence:
    """One release worth of canonical evidence, loaded once and reused."""

    corpus_release_id: str
    manifest_sha256: str
    records: Mapping[str, CorpusEvidenceRecord]
    publisher_names: Mapping[str, str]
    rejected_evidence_ids: tuple[str, ...] = ()


class ReleaseReader(Protocol):
    async def active_serving_binding(self) -> ServingReleaseBinding | None: ...

    async def release_evidence(self, corpus_release_id: str): ...

    async def evidence_details(
        self, corpus_release_id: str, evidence_ids: Sequence[str]
    ) -> list[EvidenceDetail]: ...


@dataclass(frozen=True)
class RankedEvidence:
    evidence_id: str
    rank: int
    score: float


@dataclass(frozen=True)
class ServingRetrieval:
    """What one question retrieved, including everything that was taken away."""

    release: ServingReleaseBinding | None = None
    ranked: tuple[RankedEvidence, ...] = ()
    passages: tuple[RetrievedPassage, ...] = ()
    details: tuple[EvidenceDetail, ...] = ()
    lane_failures: tuple[str, ...] = ()
    withheld_restricted_evidence_ids: tuple[str, ...] = ()
    reranked: bool = False
    abstention: ServingAbstention | None = None
    abstention_message: str | None = None

    @property
    def retrieved(self) -> bool:
        return self.abstention is None


_ABSTENTION_MESSAGES = {
    ServingAbstention.NO_ACTIVE_RELEASE: (
        "No corpus release is currently active, so no evidence can be cited."
    ),
    ServingAbstention.RETRIEVAL_PIPELINE_NOT_CONFIGURED: (
        "This deployment has no sealed retrieval candidate configured, so no approved "
        "evidence can be searched."
    ),
    ServingAbstention.SERVING_BINDING_MISMATCH: (
        "The configured retrieval candidate is not the one the active release was "
        "accepted with, so this deployment cannot answer from it."
    ),
    ServingAbstention.BENCHMARK_ACCEPTANCE_STALE: (
        "The benchmark acceptance behind the active release has expired."
    ),
    ServingAbstention.RELEASE_EVIDENCE_UNAVAILABLE: (
        "The active release did not yield any canonical evidence to search."
    ),
    ServingAbstention.UNKNOWN_SOURCE_ORGANIZATION: (
        "A requested organization filter matches no publisher in the active release."
    ),
    ServingAbstention.QUERY_ENCODING_FAILED: (
        "The question could not be encoded with the sealed candidate configuration."
    ),
    ServingAbstention.RETRIEVAL_LANE_FAILURE: (
        "A retrieval lane of the accepted configuration failed, so the ranking it was "
        "measured with was not produced."
    ),
    ServingAbstention.NO_EVIDENCE_RETRIEVED: (
        "No approved guideline evidence matched this question under the active release."
    ),
    ServingAbstention.EVIDENCE_DETAILS_UNAVAILABLE: (
        "Retrieved evidence could not be resolved to canonical, citable records."
    ),
    ServingAbstention.NO_RENDERABLE_EVIDENCE: (
        "The evidence retrieved for this question is licence-restricted and cannot be "
        "quoted or rendered."
    ),
}


def _abstain(reason: ServingAbstention, *, detail: str | None = None) -> ServingRetrieval:
    message = _ABSTENTION_MESSAGES[reason]
    return ServingRetrieval(
        abstention=reason,
        abstention_message=f"{message} ({detail})" if detail else message,
    )


class ServingRetrievalEngine:
    """Answers one question from the active release with the accepted candidate."""

    def __init__(
        self,
        qdrant: QueryPointsGateway,
        backend: EmbeddingBackend,
        candidate: RetrievalCandidateManifest,
        releases: ReleaseReader,
        *,
        reranker: RerankerBackend | None = None,
        clock=utc_now,
    ) -> None:
        content = candidate.content
        dense_lane = content.lane(CandidateLaneKind.DENSE)
        sparse_lane = content.lane(CandidateLaneKind.BM25)
        if content.reranker is not None and reranker is None:
            raise ServingConfigurationError(
                "the sealed candidate configures a reranker that is not loaded"
            )
        self._qdrant = qdrant
        self._backend = backend
        self._candidate = candidate
        self._releases = releases
        self._reranker = reranker
        self._clock = clock
        self._dense_lane = dense_lane
        self._sparse_lane = sparse_lane
        self._verify_backend_matches_candidate()
        try:
            self._expander = DeterministicQueryExpander(
                terminology_revision=content.terminology_revision,
                safety_query_revision=content.safety_query_revision,
            )
        except QueryExpansionError as error:
            raise ServingConfigurationError(str(error)) from error
        self._evidence: ReleaseEvidence | None = None

    @property
    def candidate(self) -> RetrievalCandidateManifest:
        return self._candidate

    def _verify_backend_matches_candidate(self) -> None:
        """Refuse a backend whose artifacts are not the ones the candidate pins."""

        dense = self._backend.dense_definition
        sparse = self._backend.sparse_definition
        problems: list[str] = []
        if dense.model != self._dense_lane.model:
            problems.append("dense model artifact")
        if sparse.model != self._sparse_lane.model:
            problems.append("sparse model artifact")
        if self._backend.dense_adapter_identity != (
            self._dense_lane.adapter_id,
            self._dense_lane.adapter_revision,
        ):
            problems.append("dense adapter identity")
        if self._backend.sparse_adapter_identity != (
            self._sparse_lane.adapter_id,
            self._sparse_lane.adapter_revision,
        ):
            problems.append("sparse adapter identity")
        if problems:
            raise ServingConfigurationError(
                "loaded embedding backend does not match the sealed candidate: "
                + ", ".join(problems)
            )

    def binding_mismatch(self, binding: ServingReleaseBinding) -> str | None:
        """Return why this stack cannot serve the given release, or None."""

        if binding.candidate_configuration_sha256 != self._candidate.candidate_sha256:
            return (
                "release accepted candidate "
                f"{binding.candidate_configuration_sha256[:12]}, this process loaded "
                f"{self._candidate.candidate_sha256[:12]}"
            )
        expected = candidate_qdrant_collection_for_manifest(binding.manifest, self._candidate)
        if expected != binding.qdrant_collection:
            return (
                "candidate vector profile resolves to collection "
                f"{expected}, release serves {binding.qdrant_collection}"
            )
        return None

    async def retrieve(
        self,
        question: str,
        filters: SourceFilters,
        *,
        stage: StageReporter | None = None,
    ) -> ServingRetrieval:
        binding = await self._releases.active_serving_binding()
        if binding is None:
            return _abstain(ServingAbstention.NO_ACTIVE_RELEASE)
        if mismatch := self.binding_mismatch(binding):
            return _abstain(ServingAbstention.SERVING_BINDING_MISMATCH, detail=mismatch)
        if binding.benchmark_valid_until <= _as_utc(self._clock()):
            return _abstain(
                ServingAbstention.BENCHMARK_ACCEPTANCE_STALE,
                detail=f"expired {binding.benchmark_valid_until.isoformat()}",
            )

        evidence = await self._release_evidence(binding)
        if not evidence.records:
            return _abstain(ServingAbstention.RELEASE_EVIDENCE_UNAVAILABLE)

        try:
            retrieval_filter = self._retrieval_filter(filters, evidence)
        except LookupError as error:
            return _abstain(
                ServingAbstention.UNKNOWN_SOURCE_ORGANIZATION, detail=str(error)
            )

        try:
            embedded = await self._embed_question(question)
            expanded = await self._embed_expansions(question)
        except RetrievalError as error:
            return _abstain(ServingAbstention.QUERY_ENCODING_FAILED, detail=str(error))
        except Exception as error:  # noqa: BLE001 - encoding failure is an abstention
            return _abstain(
                ServingAbstention.QUERY_ENCODING_FAILED,
                detail=error.__class__.__name__,
            )

        content = self._candidate.content
        searcher = LaneSearcher(
            self._qdrant,
            evidence.records,
            collection=binding.qdrant_collection,
            corpus_release_id=binding.corpus_release_id,
            retrieval_filter=retrieval_filter,
        )
        result = await hybrid_retrieve(
            searcher,
            evidence.records,
            question=question,
            dense_vector_name=self._dense_lane.vector_name,
            sparse_vector_name=self._sparse_lane.vector_name,
            dense_query=list(embedded.dense),
            sparse_query={
                "indices": list(embedded.sparse.indices),
                "values": list(embedded.sparse.values),
            },
            expanded_sparse=expanded,
            candidate_limit=content.candidate_limit,
            output_depth=content.output_depth,
            rrf_k=content.rrf_k,
            lane_weights=LaneWeights(
                dense=self._dense_lane.weight, sparse=self._sparse_lane.weight
            ),
            reranker=self._reranker,
            reranker_config=content.reranker,
            conflict_aware=content.safety_query_revision == CONFLICT_AWARE_QUERY_REVISION,
            is_conflict_question=is_conflict_query(question),
            stage=stage,
        )
        if result.failures:
            # The benchmark isolates a failed lane so a run can continue and report it.
            # Serving cannot: the accepted scores describe the fused configuration, and
            # a partial fusion is a system nobody measured.
            return ServingRetrieval(
                release=binding,
                lane_failures=result.failures,
                abstention=ServingAbstention.RETRIEVAL_LANE_FAILURE,
                abstention_message=(
                    f"{_ABSTENTION_MESSAGES[ServingAbstention.RETRIEVAL_LANE_FAILURE]} "
                    f"({', '.join(result.failures)})"
                ),
            )

        selected = result.candidates[: content.output_depth]
        if not selected:
            return ServingRetrieval(
                release=binding,
                abstention=ServingAbstention.NO_EVIDENCE_RETRIEVED,
                abstention_message=_ABSTENTION_MESSAGES[
                    ServingAbstention.NO_EVIDENCE_RETRIEVED
                ],
            )
        return await self._materialize(binding, selected, reranked=result.reranked)

    async def _materialize(
        self,
        binding: ServingReleaseBinding,
        selected: list[Candidate],
        *,
        reranked: bool,
    ) -> ServingRetrieval:
        """Turn a ranking into citable details and model-visible passages."""

        ranked = tuple(
            RankedEvidence(evidence_id=item.evidence_id, rank=rank, score=item.score)
            for rank, item in enumerate(selected, start=1)
        )
        details = await self._releases.evidence_details(
            binding.corpus_release_id, [item.evidence_id for item in ranked]
        )
        by_id = {detail.evidence_id: detail for detail in details}
        missing = [item.evidence_id for item in ranked if item.evidence_id not in by_id]
        if missing:
            # Retrieval and citation read the same release. If they disagree, the
            # disagreement is the finding; answering from the half that resolved would
            # hide it behind a shorter citation list.
            return ServingRetrieval(
                release=binding,
                ranked=ranked,
                abstention=ServingAbstention.EVIDENCE_DETAILS_UNAVAILABLE,
                abstention_message=(
                    f"{_ABSTENTION_MESSAGES[ServingAbstention.EVIDENCE_DETAILS_UNAVAILABLE]} "
                    f"({len(missing)} of {len(ranked)} unresolved)"
                ),
            )

        passages: list[RetrievedPassage] = []
        withheld: list[str] = []
        for item in ranked:
            detail = by_id[item.evidence_id]
            # Licence-restricted evidence is still approved evidence and still ranks.
            # What it does not get is a copy of its exact text leaving this process,
            # which rules out handing it to a generation provider.
            if not detail.render_allowed or not detail.exact_text:
                withheld.append(item.evidence_id)
                continue
            passages.append(
                RetrievedPassage(
                    evidence_id=detail.evidence_id,
                    text=detail.exact_text,
                    evidence_roles=tuple(detail.evidence_roles),
                    source_title=detail.source_title,
                    source_version_label=detail.source_version_label,
                )
            )
        if not passages:
            return ServingRetrieval(
                release=binding,
                ranked=ranked,
                details=tuple(details),
                withheld_restricted_evidence_ids=tuple(withheld),
                abstention=ServingAbstention.NO_RENDERABLE_EVIDENCE,
                abstention_message=_ABSTENTION_MESSAGES[
                    ServingAbstention.NO_RENDERABLE_EVIDENCE
                ],
            )
        return ServingRetrieval(
            release=binding,
            ranked=ranked,
            passages=tuple(passages),
            details=tuple(details),
            withheld_restricted_evidence_ids=tuple(withheld),
            reranked=reranked,
        )

    async def _release_evidence(self, binding: ServingReleaseBinding) -> ReleaseEvidence:
        cached = self._evidence
        if (
            cached is not None
            and cached.corpus_release_id == binding.corpus_release_id
            and cached.manifest_sha256 == binding.manifest_sha256
        ):
            return cached
        loaded = await self._releases.release_evidence(binding.corpus_release_id)
        evidence = ReleaseEvidence(
            corpus_release_id=binding.corpus_release_id,
            manifest_sha256=binding.manifest_sha256,
            records=dict(loaded.records),
            publisher_names=dict(loaded.publisher_names),
            rejected_evidence_ids=tuple(loaded.rejected_evidence_ids),
        )
        self._evidence = evidence
        return evidence

    def _retrieval_filter(
        self, filters: SourceFilters, evidence: ReleaseEvidence
    ) -> RetrievalFilter:
        """Translate the request filter into release-scoped payload constraints.

        Organization tokens are free text typed by a reader, so they are resolved
        against the publishers this release actually contains. An unresolvable token
        raises rather than being dropped: a reader who restricted their sources and got
        an unrestricted answer has been told something untrue about the answer.
        """

        publisher_ids: list[str] = []
        for organization in filters.organizations:
            token = organization.strip().casefold()
            matches = {
                publisher_id
                for publisher_id, name in evidence.publisher_names.items()
                if token in (publisher_id.casefold(), name.casefold())
            }
            if not matches:
                raise LookupError(organization)
            publisher_ids.extend(sorted(matches))
        return RetrievalFilter(
            jurisdictions=tuple(sorted(set(filters.jurisdictions))),
            publisher_ids=tuple(sorted(set(publisher_ids))),
        )

    async def _embed_question(self, question: str) -> EmbeddedText:
        embedded = tuple(await self._backend.embed_queries((question,)))
        if len(embedded) != 1:
            raise RetrievalError("embedding backend did not return exactly one query vector")
        vector = embedded[0]
        if len(vector.dense) != self._backend.dense_definition.dimension:
            raise RetrievalError("query dense vector has the wrong dimension")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in vector.dense
        ):
            raise RetrievalError("query dense vector contains invalid values")
        if not vector.sparse.indices:
            raise RetrievalError("query sparse vector is empty")
        if vector.sparse.indices[-1] >= self._backend.sparse_definition.dimension:
            raise RetrievalError("query sparse vector has the wrong dimension")
        return vector

    async def _embed_expansions(self, question: str):
        expansions = self._expander.expand(question)
        if not expansions:
            return ()
        sparse_method = getattr(self._backend, "embed_sparse_queries", None)
        encoded = []
        for expansion in expansions:
            if sparse_method is None:
                vectors = tuple(await self._backend.embed_queries((expansion.text,)))
                sparse = vectors[0].sparse
            else:
                sparse = tuple(await sparse_method((expansion.text,)))[0]
            if not sparse.indices:
                raise RetrievalError("expanded sparse query vector is empty")
            if sparse.indices[-1] >= self._backend.sparse_definition.dimension:
                raise RetrievalError("expanded sparse query has the wrong dimension")
            encoded.append(
                (
                    expansion,
                    {"indices": list(sparse.indices), "values": list(sparse.values)},
                )
            )
        return tuple(encoded)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


__all__ = [
    "RankedEvidence",
    "ReleaseEvidence",
    "ReleaseReader",
    "RetrievalStage",
    "SERVING_RETRIEVAL_VERSION",
    "ServingAbstention",
    "ServingConfigurationError",
    "ServingRetrieval",
    "ServingRetrievalEngine",
]
