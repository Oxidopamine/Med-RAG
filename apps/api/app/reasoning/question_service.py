"""Orchestration of one question across the serving path.

The status stream is a claim about what happened. Every event this service emits marks
a phase that actually ran, in the order it ran, and a phase that does not run is not
announced: there is no counter-evidence search in the serving path today and no
reranking lane, so `SEARCHING_COUNTER_EVIDENCE` and `RERANKING` stay in the contract -
clients consume the enum - and are never emitted. Timed sleeps standing in for work
would make the stream a plausible fiction, which is the one thing a progress feed for a
clinical answer must not be.

Abstention is decided in four places, and the order matters:

1. no active release, or no serving pipeline wired - decided before retrieval;
2. nothing retrieved, or an incomplete evidence-role set - decided after retrieval and
   *before any model call*, because a model handed an incomplete evidence set is
   measurably more likely to answer confidently than to abstain;
3. inside `GroundedAnswerComposer`, where the model's proposal is checked against what
   was actually retrieved;
4. after grounding, if a surviving claim's canonical record cannot be resolved.

A lane failure is not in that list. Retrieval isolates a failed dense, sparse, or
expansion lane, and the surviving lanes may still have found a complete evidence set,
so a lane failure never blocks an answer by itself.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from app.reasoning.answer_service import ComposedAnswer
from app.reasoning.context_extractor import extract_context_preview
from app.reasoning.generation_schemas import AbstentionReason
from app.reasoning.retrieval_service import ServingRetrievalResult
from app.reasoning.serving_pipeline import ServingPipeline
from app.schemas.corpus import ActiveCorpusRelease
from app.schemas.domain import ClinicalContext, utc_now
from app.schemas.questions import (
    TERMINAL_STATUSES,
    AbstentionDetail,
    EvidenceDetail,
    ProgressEvent,
    QuestionAccepted,
    QuestionCreate,
    QuestionResult,
    QuestionStatus,
    RenderedClaim,
    RetrievalCandidate,
    SourceFilters,
    VerificationSummary,
)

# How many retrieved passages an abstention names as the nearest thing it found. The
# retrieved set is not evidence for an answer that was withheld, so this is a pointer
# for a reader deciding what to search next, never a partial answer.
CLOSEST_EVIDENCE_LIMIT = 5

_ABSTENTION_MESSAGES = {
    AbstentionReason.NO_EVIDENCE_RETRIEVED: (
        "No approved guideline evidence matched this question under the active release."
    ),
    AbstentionReason.INCOMPLETE_EVIDENCE_ROLE_SET: (
        "The retrieved guideline evidence does not form a complete evidence set for a "
        "clinical answer, so no answer was composed."
    ),
    AbstentionReason.EVIDENCE_DETAIL_UNAVAILABLE: (
        "A supporting guideline record could not be resolved from the active release, "
        "so the answer was withheld rather than cited to evidence that cannot be opened."
    ),
}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


@dataclass
class QuestionRecord:
    question_id: str
    question: str
    source_filters: SourceFilters
    status: QuestionStatus
    created_at: datetime
    updated_at: datetime
    corpus_release: ActiveCorpusRelease | None = None
    approved_corpus_available: bool = False
    interpreted_context: ClinicalContext | None = None
    claims: list[RenderedClaim] = field(default_factory=list)
    evidence_details: list[EvidenceDetail] = field(default_factory=list)
    retrieval_candidates: list[RetrievalCandidate] = field(default_factory=list)
    conflicts: list[dict[str, str]] = field(default_factory=list)
    verification_summary: VerificationSummary = field(default_factory=VerificationSummary)
    abstention: AbstentionDetail | None = None
    previous_question_id: str | None = None
    events: list[ProgressEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)


class QuestionNotFoundError(KeyError):
    pass


class QuestionService:
    """In-process research orchestrator; durable jobs arrive with the persistence slice."""

    def __init__(
        self,
        *,
        active_release_provider: Callable[
            [], Awaitable[ActiveCorpusRelease | None]
        ]
        | None = None,
        approved_corpus_available: bool = False,
        pipeline: ServingPipeline | None = None,
    ) -> None:
        self._active_release_provider = active_release_provider
        self._legacy_approved_corpus_available = approved_corpus_available
        # No pipeline is a legitimate deployment state, not a defect: the API can run
        # with a release registered and no serving path wired to it. It abstains with
        # RETRIEVAL_PIPELINE_NOT_CONFIGURED, which says exactly that.
        self._pipeline = pipeline
        self._records: dict[str, QuestionRecord] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def submit(self, request: QuestionCreate) -> QuestionAccepted:
        approved_corpus_available, corpus_release = await self._resolve_active_release()
        record = self._create_record(
            request,
            approved_corpus_available=approved_corpus_available,
            corpus_release=corpus_release,
        )
        await self._emit(record, QuestionStatus.QUEUED)
        task = asyncio.create_task(self._run(record))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return QuestionAccepted(question_id=record.question_id, status=record.status)

    async def replace_context(
        self,
        question_id: str,
        context: ClinicalContext,
    ) -> QuestionAccepted:
        prior = self._require(question_id)
        approved_corpus_available, corpus_release = await self._resolve_active_release()
        record = self._create_record(
            QuestionCreate(question=prior.question, source_filters=prior.source_filters),
            context=context,
            previous_question_id=prior.question_id,
            approved_corpus_available=approved_corpus_available,
            corpus_release=corpus_release,
        )
        await self._emit(record, QuestionStatus.QUEUED)
        task = asyncio.create_task(self._run(record, preserve_context=True))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return QuestionAccepted(question_id=record.question_id, status=record.status)

    def result(self, question_id: str) -> QuestionResult:
        record = self._require(question_id)
        return QuestionResult(
            question_id=record.question_id,
            question=record.question,
            status=record.status,
            corpus_release=record.corpus_release,
            interpreted_context=record.interpreted_context,
            claims=record.claims,
            evidence_details=record.evidence_details,
            retrieval_candidates=record.retrieval_candidates,
            conflicts=record.conflicts,
            verification_summary=record.verification_summary,
            abstention=record.abstention,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    async def events(self, question_id: str, after: int = 0) -> AsyncIterator[ProgressEvent]:
        record = self._require(question_id)
        cursor = max(after, 0)
        while True:
            while cursor < len(record.events):
                event = record.events[cursor]
                cursor += 1
                yield event
            if record.status in TERMINAL_STATUSES:
                return
            async with record.condition:
                try:
                    await asyncio.wait_for(record.condition.wait(), timeout=15)
                except TimeoutError:
                    continue

    async def close(self) -> None:
        if not self._tasks:
            return
        for task in tuple(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def _create_record(
        self,
        request: QuestionCreate,
        *,
        context: ClinicalContext | None = None,
        previous_question_id: str | None = None,
        approved_corpus_available: bool = False,
        corpus_release: ActiveCorpusRelease | None = None,
    ) -> QuestionRecord:
        now = utc_now()
        record = QuestionRecord(
            question_id=new_id("Q"),
            question=request.question,
            source_filters=request.source_filters,
            status=QuestionStatus.QUEUED,
            created_at=now,
            updated_at=now,
            corpus_release=corpus_release,
            approved_corpus_available=approved_corpus_available,
            interpreted_context=context,
            previous_question_id=previous_question_id,
        )
        self._records[record.question_id] = record
        return record

    async def _run(self, record: QuestionRecord, *, preserve_context: bool = False) -> None:
        try:
            if not preserve_context:
                record.interpreted_context = extract_context_preview(record.question)
            await self._emit(record, QuestionStatus.CONTEXT_EXTRACTED)

            release = record.corpus_release
            if release is None or self._pipeline is None:
                await self._abstain_unconfigured(record)
                return

            await self._emit(record, QuestionStatus.RETRIEVING)
            retrieval = await self._pipeline.retrieve(
                record.question,
                release=release,
                source_filters=record.source_filters,
            )

            await self._emit(record, QuestionStatus.CHECKING_EVIDENCE_COMPLETENESS)
            gate_abstention = self._gate(retrieval)
            if gate_abstention is not None:
                # Decided here, before any model call, and deliberately so.
                record.abstention = gate_abstention
                await self._emit(record, QuestionStatus.ABSTAINED)
                return

            # VERIFYING covers composition: the model proposes claims and every cited
            # evidence ID is checked against the retrieved set inside the composer.
            await self._emit(record, QuestionStatus.VERIFYING)
            composed = await self._pipeline.compose(record.question, retrieval.passages)
            if composed.abstention is not None:
                record.abstention = composed.abstention
                await self._emit(record, QuestionStatus.ABSTAINED)
                return

            details = await self._resolve_details(record, release, composed)
            if details is None:
                await self._emit(record, QuestionStatus.ABSTAINED)
                return
            resolved, candidates = details

            record.claims = list(composed.claims)
            record.conflicts = [dict(conflict) for conflict in composed.conflicts]
            record.retrieval_candidates = candidates
            record.evidence_details = resolved
            record.verification_summary = composed.verification
            record.abstention = None
            await self._emit(record, QuestionStatus.ANSWER_READY)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Nothing partial survives a failure: a half-composed answer is the shape
            # of an answer without the checks that make one safe.
            record.claims = []
            record.evidence_details = []
            record.retrieval_candidates = []
            record.conflicts = []
            record.verification_summary = VerificationSummary()
            record.abstention = AbstentionDetail(
                reason_code="PIPELINE_FAILURE",
                message="The evidence pipeline failed closed and did not render a clinical claim.",
            )
            await self._emit(record, QuestionStatus.FAILED)

    async def _abstain_unconfigured(self, record: QuestionRecord) -> None:
        reason_code = (
            "RETRIEVAL_PIPELINE_NOT_CONFIGURED"
            if record.approved_corpus_available
            else "NO_APPROVED_CORPUS"
        )
        record.abstention = AbstentionDetail(
            reason_code=reason_code,
            message=(
                "No verified answer was produced because an approved guideline corpus "
                "and complete evidence set are not available."
            ),
            missing_evidence_roles=[
                "PRIMARY_SUPPORT",
                "APPLICABILITY",
                "EXCEPTION_OR_CONTRAINDICATION",
            ],
        )
        await self._emit(record, QuestionStatus.ABSTAINED)

    @staticmethod
    def _gate(retrieval: ServingRetrievalResult) -> AbstentionDetail | None:
        """Decide whether an answer may be attempted, before any model is called.

        ``ServingRetrievalResult.is_answerable`` already encodes this decision; it is
        unpacked here only to report *which* of the two ways it failed, because
        "nothing matched" and "what matched cannot carry a recommendation" send a
        reader to different next steps.
        """
        closest = [
            passage.evidence_id for passage in retrieval.passages[:CLOSEST_EVIDENCE_LIMIT]
        ]
        if not retrieval.passages:
            return AbstentionDetail(
                reason_code=AbstentionReason.NO_EVIDENCE_RETRIEVED.value,
                message=_ABSTENTION_MESSAGES[AbstentionReason.NO_EVIDENCE_RETRIEVED],
            )
        if retrieval.missing_required_roles:
            return AbstentionDetail(
                reason_code=AbstentionReason.INCOMPLETE_EVIDENCE_ROLE_SET.value,
                message=_ABSTENTION_MESSAGES[AbstentionReason.INCOMPLETE_EVIDENCE_ROLE_SET],
                missing_evidence_roles=[
                    role.value for role in retrieval.missing_required_roles
                ],
                closest_evidence_ids=closest,
            )
        return None

    async def _resolve_details(
        self,
        record: QuestionRecord,
        release: ActiveCorpusRelease,
        composed: ComposedAnswer,
    ) -> tuple[list[EvidenceDetail], list[RetrievalCandidate]] | None:
        """Resolve canonical records for everything the result will reference.

        Two different failures, treated differently on purpose. A *cited* record that
        cannot be resolved withholds the answer: a claim whose citation does not open
        is worse than no claim. An *uncited* candidate that cannot be resolved is
        simply not offered - it was never support for anything, and dropping it costs
        the reader nothing. Sets ``record.abstention`` and returns None in the first case.
        """
        assert self._pipeline is not None
        cited = {
            evidence_id for claim in composed.claims for evidence_id in claim.evidence_ids
        }
        referenced = cited | {
            candidate.evidence_id for candidate in composed.candidates
        }
        details = await self._pipeline.evidence_details(
            release.corpus_release_id, referenced
        )
        resolved_ids = {detail.evidence_id for detail in details}

        missing_citations = sorted(cited - resolved_ids)
        if missing_citations:
            record.abstention = AbstentionDetail(
                reason_code=AbstentionReason.EVIDENCE_DETAIL_UNAVAILABLE.value,
                message=_ABSTENTION_MESSAGES[AbstentionReason.EVIDENCE_DETAIL_UNAVAILABLE],
                closest_evidence_ids=missing_citations[:CLOSEST_EVIDENCE_LIMIT],
            )
            return None

        surviving = [
            candidate
            for candidate in composed.candidates
            if candidate.evidence_id in resolved_ids
        ]
        keep = cited | {candidate.evidence_id for candidate in surviving}
        return (
            [detail for detail in details if detail.evidence_id in keep],
            surviving,
        )

    async def _resolve_active_release(self) -> tuple[bool, ActiveCorpusRelease | None]:
        if self._active_release_provider is None:
            return self._legacy_approved_corpus_available, None
        try:
            release = await self._active_release_provider()
            return release is not None, release
        except Exception:
            # Canonical release state being unavailable is never evidence of availability.
            return False, None

    async def _emit(self, record: QuestionRecord, status: QuestionStatus) -> None:
        async with record.condition:
            record.status = status
            record.updated_at = utc_now()
            record.events.append(
                ProgressEvent(
                    question_id=record.question_id,
                    sequence=len(record.events) + 1,
                    status=status,
                )
            )
            record.condition.notify_all()

    def _require(self, question_id: str) -> QuestionRecord:
        try:
            return self._records[question_id]
        except KeyError as error:
            raise QuestionNotFoundError(question_id) from error
