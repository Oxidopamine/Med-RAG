import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from app.reasoning.answer_service import GroundedAnswerComposer
from app.reasoning.context_extractor import extract_context_preview
from app.retrieval.pipeline import RetrievalStage
from app.retrieval.serving import ServingRetrieval, ServingRetrievalEngine
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
    SourceFilters,
    VerificationSummary,
)

_STAGE_STATUSES = {
    RetrievalStage.LANE_SEARCH: QuestionStatus.RETRIEVING,
    RetrievalStage.EXPANSION_SEARCH: QuestionStatus.SEARCHING_COUNTER_EVIDENCE,
    RetrievalStage.RERANK: QuestionStatus.RERANKING,
}
_MISSING_EVIDENCE_ROLES = [
    "PRIMARY_SUPPORT",
    "APPLICABILITY",
    "EXCEPTION_OR_CONTRAINDICATION",
]


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
    conflicts: list[dict[str, str]] = field(default_factory=list)
    verification_summary: VerificationSummary = field(default_factory=VerificationSummary)
    abstention: AbstentionDetail | None = None
    previous_question_id: str | None = None
    events: list[ProgressEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)


class QuestionNotFoundError(KeyError):
    pass


class QuestionService:
    """In-process research orchestrator; durable jobs arrive with the persistence slice.

    Each progress status is emitted by the stage that produced it. A deployment with no
    retrieval engine still runs this path and still reports progress, but it reaches
    exactly one terminal state - an abstention naming what is missing - and never
    reports work it did not do.
    """

    def __init__(
        self,
        *,
        active_release_provider: Callable[
            [], Awaitable[ActiveCorpusRelease | None]
        ]
        | None = None,
        approved_corpus_available: bool = False,
        retrieval_engine: ServingRetrievalEngine | None = None,
        answer_composer: GroundedAnswerComposer | None = None,
    ) -> None:
        self._active_release_provider = active_release_provider
        self._legacy_approved_corpus_available = approved_corpus_available
        self._retrieval = retrieval_engine
        self._composer = answer_composer
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

            if self._retrieval is None:
                await self._abstain(
                    record,
                    reason_code=(
                        "RETRIEVAL_PIPELINE_NOT_CONFIGURED"
                        if record.approved_corpus_available
                        else "NO_APPROVED_CORPUS"
                    ),
                    message=(
                        "No verified answer was produced because an approved guideline "
                        "corpus and complete evidence set are not available."
                    ),
                    missing_evidence_roles=_MISSING_EVIDENCE_ROLES,
                )
                return

            retrieval = await self._retrieval.retrieve(
                record.question,
                record.source_filters,
                stage=lambda value: self._emit(record, _STAGE_STATUSES[value]),
            )
            if retrieval.release is not None:
                # Report the release the search actually ran against, which is the one
                # the engine resolved, not the pointer read when the question arrived.
                record.corpus_release = retrieval.release.active_release()
            if not retrieval.retrieved:
                assert retrieval.abstention is not None
                await self._abstain(
                    record,
                    reason_code=retrieval.abstention.value,
                    message=retrieval.abstention_message or "",
                    closest_evidence_ids=[item.evidence_id for item in retrieval.ranked[:5]],
                )
                return

            await self._emit(record, QuestionStatus.CHECKING_EVIDENCE_COMPLETENESS)
            if self._composer is None:
                await self._abstain(
                    record,
                    reason_code="GENERATION_UNAVAILABLE",
                    message=(
                        "Evidence was retrieved but this deployment has no generation "
                        "lane configured, so no claim can be rendered or verified."
                    ),
                    closest_evidence_ids=[
                        item.evidence_id for item in retrieval.ranked[:5]
                    ],
                )
                return

            await self._emit(record, QuestionStatus.VERIFYING)
            composed = await self._composer.compose(
                record.question, retrieval.passages, release_active=True
            )
            record.verification_summary = composed.verification
            if composed.abstention is not None:
                record.abstention = composed.abstention
                await self._emit(record, QuestionStatus.ABSTAINED)
                return
            record.claims = list(composed.claims)
            record.conflicts = [dict(conflict) for conflict in composed.conflicts]
            record.evidence_details = self._cited_details(record.claims, retrieval)
            await self._emit(record, QuestionStatus.ANSWER_READY)
        except asyncio.CancelledError:
            raise
        except Exception:
            record.abstention = AbstentionDetail(
                reason_code="PIPELINE_FAILURE",
                message="The evidence pipeline failed closed and did not render a clinical claim.",
            )
            await self._emit(record, QuestionStatus.FAILED)

    @staticmethod
    def _cited_details(
        claims: list[RenderedClaim], retrieval: ServingRetrieval
    ) -> list[EvidenceDetail]:
        """Return canonical details for exactly the evidence the claims cite."""

        cited = {evidence_id for claim in claims for evidence_id in claim.evidence_ids}
        return [detail for detail in retrieval.details if detail.evidence_id in cited]

    async def _abstain(
        self,
        record: QuestionRecord,
        *,
        reason_code: str,
        message: str,
        missing_evidence_roles: list[str] | None = None,
        closest_evidence_ids: list[str] | None = None,
    ) -> None:
        record.abstention = AbstentionDetail(
            reason_code=reason_code,
            message=message,
            missing_evidence_roles=missing_evidence_roles or [],
            closest_evidence_ids=closest_evidence_ids or [],
        )
        await self._emit(record, QuestionStatus.ABSTAINED)

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
