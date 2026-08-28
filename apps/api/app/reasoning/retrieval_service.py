"""Release-scoped retrieval for the serving path.

This is deliberately *not* the benchmark runner. The two do similar searches for
different reasons, and merging them would be a mistake in both directions:

* ``RetrievalBenchmarkRunner`` is the measured system. The accepted development
  report and the frozen comparator floor were produced by that exact code path, so
  changing it to serve free-text questions would silently break comparability with
  every prior report.
* Serving has no gold set and no strata. It cannot score itself, it needs a latency
  budget, and evidence-role completeness is a *decision that gates abstention* here
  rather than a number reported afterwards.

What the two must share is the safety behaviour, so the payload-integrity checks are
reproduced rather than skipped: a point whose payload disagrees with the canonical
evidence record is a corrupted index, and serving must fail closed on it exactly as
the benchmark does.

Passage text never comes from Qdrant. The index carries only filterable metadata;
``content_exact`` is read from the canonical evidence records, because Qdrant is a
rebuildable projection and never the source of truth.

What a passage *reads* as is a separate question from what it *is*. ``app.reasoning
.presentation`` renders each record for a reader and for the generation lane, and this
module consumes three things from that rendering, none of which alters a record:

* the rendered text, so a spreadsheet row reaches the model as labelled facts rather
  than as ``A145=HIV.D8 ...``;
* a fingerprint over the substantive cells, used to suppress duplicates *before* top-k.
  The WHO ``all`` worksheet copies all thirteen topic worksheets verbatim and adds one
  column naming the tab a row came from, so 2,080 of the release's 5,145 approved
  records are second copies. QA's exact-content deduplication could not see it - the
  extra column makes ``content_search`` differ - and without suppression the copies eat
  output slots that should hold different evidence;
* whether a passage is recommendation-bearing at all, which the role gate below needs.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from app.corpus_steward.qdrant_index import stable_qdrant_point_id
from app.corpus_steward.query_expansion import ExpandedQuery
from app.reasoning.ablation import PRODUCTION, AblationProfile
from app.reasoning.presentation import PassageKind, PassagePresenter, RenderedPassage
from app.schemas.corpus import CorpusEvidenceRecord, EvidenceRole

# Re-exported so callers of the serving path read the constraint from the module
# that enforces it; the definition is shared with evidence-detail resolution.
from app.schemas.domain import SERVABLE_LIFECYCLE_STATES

# Metadata the serving filter and the integrity check need. Deliberately identical to
# the benchmark's projection: a divergence here would mean the two paths validate
# different things about the same index.
SERVING_PAYLOAD_FIELDS = [
    "approval_status",
    "corpus_release_id",
    "evidence_id",
    "evidence_roles",
    "evidence_sha256",
    "jurisdiction",
    "language",
    "lifecycle_status",
    "publisher_id",
    "source_class",
    "source_version_id",
]


# Roles that must be present before a clinical answer may be composed. This is the
# serving-side reading of the minimum complete evidence set: a recommendation with no
# applicability statement, or with its exceptions missing, is not a safe answer even
# when the retrieval scores are high.
#
# KNOWN LIMIT - the gate proves completeness of *kind*, never of *subject*. It asks
# whether the retrieved set contains a passage of each required role; it has no notion of
# whether those passages are about the question. Measured: asked for dolutegravir
# contraindications, the set was reported answerable when its only countable
# PRIMARY_SUPPORT was a contraindication for tenofovir - a different drug. The gate did
# exactly what it claims and the claim is narrower than it reads.
#
# Nothing here can close that. Topical relevance is a ranking property, and a gate built
# from role labels cannot become a relevance judgement without becoming the very thing
# this layer refuses to be - a model deciding what evidence means. Closing it needs a
# separate relevance floor, or the claim-level grounding in `answer_service` to reject
# claims whose cited passages do not bear on the question. Until one of those exists,
# `is_answerable` means "a complete set of role kinds was retrieved", and the answer lane
# behind it is what actually protects the reader: a claim citing an off-topic passage is
# discarded whole at grounding.
REQUIRED_ANSWER_ROLES: tuple[EvidenceRole, ...] = (
    EvidenceRole.PRIMARY_SUPPORT,
    EvidenceRole.APPLICABILITY,
)

# Roles a passage may only claim if it is the kind of thing that can bear a clinical
# recommendation at all.
#
# The corpus labels roles by *source asset*: `qa_classification._base_roles` gives every
# Annex A row PRIMARY_SUPPORT because Annex A is the data dictionary, and every Annex B
# row PRIMARY_SUPPORT because Annex B is decision support. So a code-list entry defining
# `DTG` as an input option, and a row of column headings, both arrive labelled
# PRIMARY_SUPPORT, and the gate that exists to stop an answer being composed from an
# incomplete evidence set is satisfied by evidence that supports no clinical claim.
#
# The durable fix is the classifier, not this check - the label is wrong at the point it
# is written, and re-deciding it here cannot repair the release, the benchmark run, or
# anything else that read it. What this check *can* do is stop the abstention gate
# resting on a claim it never verified, which is the same argument as reproducing the
# payload-integrity checks above rather than trusting the index. It is deliberately
# narrow: only the role whose meaning is "this states the recommendation" is qualified,
# and qualification is on form - see `presentation.RECOMMENDATION_BEARING_KINDS` - never
# on a reading of what a passage says.
ROLES_REQUIRING_RECOMMENDATION: frozenset[EvidenceRole] = frozenset(
    {EvidenceRole.PRIMARY_SUPPORT}
)


class ServingRetrievalError(RuntimeError):
    """Raised when the index contradicts canonical evidence. Never recoverable here."""


@dataclass(frozen=True)
class ServingPassage:
    evidence_id: str
    content_exact: str
    evidence_roles: tuple[EvidenceRole, ...]
    source_version_id: str
    publisher_id: str
    jurisdiction: str
    language: str
    render_allowed: bool
    fused_score: float
    lanes: tuple[str, ...]
    # The presentation view. `content_exact` above is untouched and stays the citable
    # content; `rendered_text` is what a reader and the model are shown.
    rendered_text: str = ""
    kind: PassageKind = PassageKind.NARRATIVE
    is_recommendation_bearing: bool = True
    # Records suppressed as duplicates of this passage, in the order they were ranked.
    # Carried so a suppressed record is accounted for rather than silently dropped.
    duplicates_suppressed: tuple[str, ...] = ()

    @property
    def qualified_roles(self) -> tuple[EvidenceRole, ...]:
        """The roles this passage may count towards a complete evidence set."""

        if self.is_recommendation_bearing:
            return self.evidence_roles
        return tuple(
            role for role in self.evidence_roles if role not in ROLES_REQUIRING_RECOMMENDATION
        )


@dataclass(frozen=True)
class ServingRetrievalResult:
    passages: tuple[ServingPassage, ...]
    lane_failures: tuple[str, ...]
    missing_required_roles: tuple[EvidenceRole, ...]
    latency_ms: float
    expansions_used: tuple[str, ...] = ()
    # Total records dropped as duplicates of a higher-ranked passage.
    suppressed_duplicate_count: int = 0
    # Passages whose declared roles were not counted in full because the passage is not
    # recommendation-bearing, as `(evidence_id, role)` pairs. Reported rather than
    # hidden: this is the corpus's role labelling being wrong, and it should be visible
    # wherever an answer was withheld because of it.
    disqualified_role_claims: tuple[tuple[str, EvidenceRole], ...] = ()

    @property
    def is_answerable(self) -> bool:
        """Whether an answer may be attempted at all.

        A lane failure does not by itself block an answer - the surviving lanes may
        still have found a complete evidence set - but a missing required role does.
        """
        return bool(self.passages) and not self.missing_required_roles


@dataclass
class _Ranked:
    evidence_id: str
    lanes: dict[str, int] = field(default_factory=dict)


class ServingRetrievalService:
    """Hybrid dense/sparse retrieval against one immutable release collection."""

    def __init__(
        self,
        qdrant: Any,
        embedding_backend: Any,
        *,
        reranker: Any | None = None,
        presenter: PassagePresenter | None = None,
        candidate_limit: int = 100,
        top_k: int = 10,
        rrf_k: int = 60,
        dense_weight: float = 1.0,
        sparse_weight: float = 1.0,
        ablation: AblationProfile = PRODUCTION,
    ) -> None:
        self._qdrant = qdrant
        self._embedding = embedding_backend
        self._reranker = reranker
        # Defaults to PRODUCTION, so a caller that does not ask for an ablation gets the
        # exact path every prior report was measured on.
        self._ablation = ablation
        self._candidate_limit = candidate_limit
        self._top_k = top_k
        self._rrf_k = rrf_k
        self._dense_weight = dense_weight
        self._sparse_weight = sparse_weight
        # A presenter reads decision-table column labels out of the release it will
        # serve, so it is built per release and reused. Building it scans the release -
        # about 150 ms over 5,145 records - which is release-load work; pass one in so it
        # is not charged to the first question's latency. The lazy path below is the
        # correct fallback, not the intended one.
        self._presenter = presenter
        self._presenter_is_fixed = presenter is not None
        self._presenter_release: str | None = None

    def _presenter_for(
        self, corpus_release_id: str, evidence: Mapping[str, CorpusEvidenceRecord]
    ) -> PassagePresenter:
        if self._presenter_is_fixed:
            assert self._presenter is not None
            return self._presenter
        if self._presenter is None or self._presenter_release != corpus_release_id:
            self._presenter = PassagePresenter.for_release(evidence.values())
            self._presenter_release = corpus_release_id
        return self._presenter

    async def retrieve(
        self,
        question: str,
        *,
        collection: str,
        corpus_release_id: str,
        dense_vector_name: str,
        sparse_vector_name: str,
        evidence: Mapping[str, CorpusEvidenceRecord],
        jurisdictions: Sequence[str] = (),
        languages: Sequence[str] = (),
        expansions: Sequence[ExpandedQuery] = (),
    ) -> ServingRetrievalResult:
        started = time.perf_counter()
        lane_failures: list[str] = []
        rankings: dict[str, _Ranked] = {}

        embedded = (await self._embedding.embed_queries([question]))[0]
        query_filter = self._release_filter(
            corpus_release_id, jurisdictions=jurisdictions, languages=languages
        )

        dense_hits, dense_failure = await self._safe_search(
            "dense",
            collection,
            dense_vector_name,
            list(embedded.dense),
            query_filter,
            evidence,
        )
        if dense_failure:
            lane_failures.append(dense_failure)
        self._merge(rankings, "dense", dense_hits)

        sparse_query: dict[str, list[int] | list[float]] = {
            "indices": list(embedded.sparse.indices),
            "values": list(embedded.sparse.values),
        }
        sparse_hits, sparse_failure = await self._safe_search(
            "sparse",
            collection,
            sparse_vector_name,
            sparse_query,
            query_filter,
            evidence,
        )
        if sparse_failure:
            lane_failures.append(sparse_failure)
        self._merge(rankings, "sparse", sparse_hits)

        # Expansions are deterministic and bounded. A variant that reduces to
        # something a lexical backend rejects is dropped at this boundary rather
        # than aborting the whole question - the same defect class that would have
        # consumed a one-time holdout claim if it had surfaced there first.
        used_expansions: list[str] = []
        for expansion in expansions:
            if not expansion.text.strip():
                continue
            used_expansions.append(expansion.text)
            lane = f"expansion:{expansion.lane_id}"
            expanded = (await self._embedding.embed_sparse_queries([expansion.text]))[0]
            hits, failure = await self._safe_search(
                lane,
                collection,
                sparse_vector_name,
                {"indices": list(expanded.indices), "values": list(expanded.values)},
                query_filter,
                evidence,
            )
            if failure:
                lane_failures.append(failure)
            self._merge(rankings, lane, hits)

        presenter = self._presenter_for(corpus_release_id, evidence)
        passages, suppressed = self._select(self._fuse(rankings), evidence, presenter)

        covered: set[EvidenceRole] = set()
        disqualified: list[tuple[str, EvidenceRole]] = []
        for passage in passages:
            # Unqualified, the corpus's own asset-level label is taken at face value -
            # which is what F2 measured as wrong on 91.0% of the records carrying it.
            qualified = (
                set(passage.qualified_roles)
                if self._ablation.qualify_roles_by_form
                else set(passage.evidence_roles)
            )
            covered |= qualified
            disqualified.extend(
                (passage.evidence_id, role)
                for role in passage.evidence_roles
                if role not in qualified
            )
        missing = (
            tuple(role for role in REQUIRED_ANSWER_ROLES if role not in covered)
            if self._ablation.enforce_role_gate
            else ()
        )
        return ServingRetrievalResult(
            passages=passages,
            lane_failures=tuple(lane_failures),
            missing_required_roles=missing,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            expansions_used=tuple(used_expansions),
            suppressed_duplicate_count=suppressed,
            disqualified_role_claims=tuple(disqualified),
        )

    def _select(
        self,
        fused: Sequence[tuple[float, _Ranked]],
        evidence: Mapping[str, CorpusEvidenceRecord],
        presenter: PassagePresenter,
    ) -> tuple[tuple[ServingPassage, ...], int]:
        """Take the top-k *distinct* passages, in fused order.

        Suppression happens here rather than after selection because a duplicate that
        reaches the output has already cost a slot. The first occurrence wins, which is
        the highest-ranked one, and fusion order is already deterministic, so the
        survivor of a duplicate pair is too. Nothing is discarded quietly: a suppressed
        record is recorded against the passage that displaced it.
        """

        selected: list[ServingPassage] = []
        suppressed_by: dict[str, list[str]] = {}
        survivor_of: dict[str, str] = {}
        for score, item in fused:
            record = evidence[item.evidence_id]
            rendered = presenter.render(record)
            survivor = (
                survivor_of.get(rendered.fingerprint)
                if self._ablation.suppress_duplicates
                else None
            )
            if survivor is not None:
                suppressed_by.setdefault(survivor, []).append(item.evidence_id)
                continue
            if len(selected) >= self._top_k:
                # Past the output depth. Keep walking so duplicates of what was selected
                # are still attributed; a distinct record here is simply below the cut.
                continue
            survivor_of[rendered.fingerprint] = item.evidence_id
            selected.append(self._to_passage(record, item, score, rendered))

        passages = tuple(
            replace(
                passage,
                duplicates_suppressed=tuple(suppressed_by.get(passage.evidence_id, ())),
            )
            for passage in selected
        )
        return passages, sum(len(ids) for ids in suppressed_by.values())

    def _release_filter(
        self,
        corpus_release_id: str,
        *,
        jurisdictions: Sequence[str],
        languages: Sequence[str],
    ) -> dict[str, list[dict[str, Any]]]:
        must: list[dict[str, Any]] = [
            {"key": "corpus_release_id", "match": {"value": corpus_release_id}},
            {"key": "approval_status", "match": {"value": "APPROVED"}},
            # Lifecycle is a serving filter, not a ranking signal. A superseded edition
            # can be near-identical in text to the current one, so fusion and duplicate
            # suppression cannot be trusted to prefer the right member - and returning a
            # withdrawn recommendation with valid provenance attached is the worst
            # failure this system can emit. Inert on a single-version release; load-
            # bearing the moment a publisher with multiple editions is materialized.
            {
                "key": "lifecycle_status",
                "match": {"any": [status.value for status in SERVABLE_LIFECYCLE_STATES]},
            },
        ]
        if jurisdictions:
            must.append({"key": "jurisdiction", "match": {"any": list(jurisdictions)}})
        if languages:
            must.append({"key": "language", "match": {"any": list(languages)}})
        return {"must": must}

    async def _safe_search(
        self,
        lane: str,
        collection: str,
        vector_name: str,
        query: list[float] | dict[str, list[int] | list[float]],
        query_filter: dict[str, Any],
        evidence: Mapping[str, CorpusEvidenceRecord],
    ) -> tuple[list[str], str | None]:
        """Isolate lane failures. An index-integrity fault is never swallowed."""
        try:
            return (
                await self._search(collection, vector_name, query, query_filter, evidence),
                None,
            )
        except ServingRetrievalError:
            raise
        except Exception as error:  # noqa: BLE001 - one lane failing is survivable
            return [], f"{lane.upper()}_LANE_FAILURE:{error.__class__.__name__}"

    async def _search(
        self,
        collection: str,
        vector_name: str,
        query: list[float] | dict[str, list[int] | list[float]],
        query_filter: dict[str, Any],
        evidence: Mapping[str, CorpusEvidenceRecord],
    ) -> list[str]:
        raw = await self._qdrant.query_points(
            collection,
            {
                "query": query,
                "using": vector_name,
                "filter": query_filter,
                "limit": self._candidate_limit,
                "with_payload": SERVING_PAYLOAD_FIELDS,
                "with_vector": False,
            },
        )
        if not isinstance(raw, list):
            raise ServingRetrievalError("Qdrant query result is not a list")

        ordered: list[str] = []
        seen: set[str] = set()
        for result in raw:
            payload = result.get("payload")
            evidence_id = payload.get("evidence_id") if isinstance(payload, dict) else None
            if not isinstance(evidence_id, str) or evidence_id not in evidence:
                raise ServingRetrievalError("Qdrant returned unknown or malformed evidence")
            if evidence_id in seen:
                raise ServingRetrievalError(
                    f"Qdrant repeated evidence in one ranking: {evidence_id}"
                )
            seen.add(evidence_id)
            record = evidence[evidence_id]
            if result.get("id") != stable_qdrant_point_id(evidence_id):
                raise ServingRetrievalError(
                    f"Qdrant point ID does not match evidence: {evidence_id}"
                )
            if (
                payload.get("corpus_release_id") != record.corpus_release_id
                or payload.get("approval_status") != "APPROVED"
                or payload.get("evidence_sha256") != record.sha256
                or payload.get("jurisdiction") != record.jurisdiction
                or payload.get("language") != record.language
                or payload.get("publisher_id") != record.publisher_id
                or payload.get("source_version_id") != record.source_version_id
                or payload.get("lifecycle_status") != record.lifecycle_status.value
            ):
                raise ServingRetrievalError(
                    f"Qdrant payload disagrees with canonical evidence: {evidence_id}"
                )
            ordered.append(evidence_id)
        return ordered

    @staticmethod
    def _merge(
        rankings: dict[str, _Ranked], lane: str, ordered: Sequence[str]
    ) -> None:
        for rank, evidence_id in enumerate(ordered, start=1):
            entry = rankings.setdefault(evidence_id, _Ranked(evidence_id))
            entry.lanes[lane] = rank

    def _fuse(self, rankings: Mapping[str, _Ranked]) -> list[tuple[float, _Ranked]]:
        scored: list[tuple[float, _Ranked]] = []
        for entry in rankings.values():
            score = 0.0
            for lane, rank in entry.lanes.items():
                weight = self._dense_weight if lane == "dense" else self._sparse_weight
                score += weight / (self._rrf_k + rank)
            scored.append((score, entry))
        # Ties break on evidence_id so an identical corpus always ranks identically.
        scored.sort(key=lambda item: (-item[0], item[1].evidence_id))
        return scored

    @staticmethod
    def _to_passage(
        record: CorpusEvidenceRecord,
        ranked: _Ranked,
        score: float,
        rendered: RenderedPassage,
    ) -> ServingPassage:
        return ServingPassage(
            evidence_id=record.evidence_id,
            content_exact=record.content_exact,
            evidence_roles=record.evidence_roles,
            source_version_id=record.source_version_id,
            publisher_id=record.publisher_id,
            jurisdiction=record.jurisdiction,
            language=record.language,
            render_allowed=record.render_allowed,
            fused_score=score,
            lanes=tuple(sorted(ranked.lanes)),
            rendered_text=rendered.text,
            kind=rendered.kind,
            is_recommendation_bearing=rendered.is_recommendation_bearing,
        )


__all__ = [
    "REQUIRED_ANSWER_ROLES",
    "ROLES_REQUIRING_RECOMMENDATION",
    "SERVING_PAYLOAD_FIELDS",
    "ServingPassage",
    "ServingRetrievalError",
    "ServingRetrievalResult",
    "ServingRetrievalService",
]
