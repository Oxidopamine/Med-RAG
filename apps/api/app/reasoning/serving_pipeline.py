"""The release-bound seam between a submitted question and the serving path.

``QuestionService`` orchestrates a question's lifecycle; it does not know how to reach
Qdrant, which vector names a release was built with, or how to construct a generation
client. This module is that knowledge, behind one narrow protocol, for three reasons:

* the pipeline is bound to *one* release. Collection name, vector names, and the
  canonical evidence map all belong to the same immutable release, and binding them
  together makes a mismatch a construction-time fact rather than a per-question hazard;
* it keeps ``QuestionService`` testable without Qdrant, an embedding runtime, or
  generation credentials, which is what lets the fail-closed paths be tested at all;
* loading a release is expensive - the evidence map is 5,145 records and the presenter
  scans it - so it happens once, at wiring time, and never inside a question's latency.

Nothing here decides whether an answer is safe. Retrieval decides what was found, the
role gate in ``retrieval_service`` decides whether an answer may be attempted, and
``GroundedAnswerComposer`` decides what survives grounding. This module only carries a
question across those boundaries with the right release attached.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Protocol

from app.corpus_steward.query_expansion import ExpandedQuery
from app.reasoning.answer_service import ComposedAnswer, GroundedAnswerComposer, RetrievedPassage
from app.reasoning.retrieval_service import (
    ServingPassage,
    ServingRetrievalResult,
    ServingRetrievalService,
)
from app.schemas.corpus import ActiveCorpusRelease, CorpusEvidenceRecord
from app.schemas.questions import EvidenceDetail, SourceFilters

# A record scoped to the whole world is applicable under every jurisdiction filter, so
# it is admitted alongside whatever the client asked for rather than filtered out by it.
#
# This is load-bearing, not cosmetic. `SourceFilters.jurisdictions` defaults to
# ["US", "EU", "UK"], and every one of the 5,145 records in the WHO SMART HIV release
# carries `jurisdiction=WORLD`. Passing the client's filter through unmodified would
# match nothing, and every question from a default client would abstain with
# NO_EVIDENCE_RETRIEVED - a pipeline that looks wired and never answers.
GLOBAL_JURISDICTION = "WORLD"


class EvidenceDetailProvider(Protocol):
    async def evidence_details(
        self, corpus_release_id: str, evidence_ids: Collection[str]
    ) -> list[EvidenceDetail]: ...


class ServingPipeline(Protocol):
    """What ``QuestionService`` needs from the serving path, and nothing more."""

    async def retrieve(
        self,
        question: str,
        *,
        release: ActiveCorpusRelease,
        source_filters: SourceFilters,
    ) -> ServingRetrievalResult: ...

    async def compose(
        self, question: str, passages: Sequence[ServingPassage]
    ) -> ComposedAnswer: ...

    async def evidence_details(
        self, corpus_release_id: str, evidence_ids: Collection[str]
    ) -> list[EvidenceDetail]: ...


class ReleaseMismatchError(RuntimeError):
    """The active release pointer moved away from the release this pipeline serves."""


class ReleaseServingPipeline:
    """One immutable release, wired to retrieval and grounded answer composition."""

    def __init__(
        self,
        *,
        corpus_release_id: str,
        qdrant_collection: str,
        dense_vector_name: str,
        sparse_vector_name: str,
        evidence: Mapping[str, CorpusEvidenceRecord],
        retrieval: ServingRetrievalService,
        composer: GroundedAnswerComposer,
        evidence_details_provider: EvidenceDetailProvider,
        expansions: Sequence[ExpandedQuery] = (),
    ) -> None:
        self._corpus_release_id = corpus_release_id
        self._qdrant_collection = qdrant_collection
        self._dense_vector_name = dense_vector_name
        self._sparse_vector_name = sparse_vector_name
        self._evidence = evidence
        self._retrieval = retrieval
        self._composer = composer
        self._details = evidence_details_provider
        self._expansions = tuple(expansions)

    @property
    def corpus_release_id(self) -> str:
        return self._corpus_release_id

    async def retrieve(
        self,
        question: str,
        *,
        release: ActiveCorpusRelease,
        source_filters: SourceFilters,
    ) -> ServingRetrievalResult:
        # The pipeline holds one release's evidence map and one collection. If the
        # active pointer has moved, serving from what is loaded here would answer from
        # a release the system no longer considers active, with that release's ID
        # attached. Fail rather than reconcile: reloading a release is wiring work.
        if (
            release.corpus_release_id != self._corpus_release_id
            or release.qdrant_collection != self._qdrant_collection
        ):
            raise ReleaseMismatchError(
                f"active release {release.corpus_release_id} is not the release this "
                f"pipeline serves ({self._corpus_release_id})"
            )
        return await self._retrieval.retrieve(
            question,
            collection=self._qdrant_collection,
            corpus_release_id=self._corpus_release_id,
            dense_vector_name=self._dense_vector_name,
            sparse_vector_name=self._sparse_vector_name,
            evidence=self._evidence,
            jurisdictions=self._jurisdictions(source_filters),
            expansions=self._expansions,
        )

    @staticmethod
    def _jurisdictions(source_filters: SourceFilters) -> tuple[str, ...]:
        """Widen the client's jurisdiction filter to admit globally-scoped records.

        An empty filter stays empty, which the retrieval service reads as "no
        jurisdiction constraint" - widening that would be adding a constraint, not
        relaxing one.
        """
        requested = tuple(source_filters.jurisdictions)
        if not requested:
            return ()
        if GLOBAL_JURISDICTION in requested:
            return requested
        return (*requested, GLOBAL_JURISDICTION)

    async def compose(
        self, question: str, passages: Sequence[ServingPassage]
    ) -> ComposedAnswer:
        return await self._composer.compose(
            question,
            tuple(
                RetrievedPassage(
                    # The rendered view, not `content_exact`: a model handed
                    # `A145=HIV.D8 ...` spends its attention on column letters. The
                    # citation is bound by `evidence_id`, so it still resolves to the
                    # exact approved record.
                    evidence_id=passage.evidence_id,
                    text=passage.rendered_text,
                    evidence_roles=tuple(role.value for role in passage.evidence_roles),
                    source_version_label=passage.source_version_id,
                )
                for passage in passages
            ),
        )

    async def evidence_details(
        self, corpus_release_id: str, evidence_ids: Collection[str]
    ) -> list[EvidenceDetail]:
        return await self._details.evidence_details(corpus_release_id, evidence_ids)


__all__ = [
    "GLOBAL_JURISDICTION",
    "EvidenceDetailProvider",
    "ReleaseMismatchError",
    "ReleaseServingPipeline",
    "ServingPipeline",
]
