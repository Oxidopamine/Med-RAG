import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from app.reasoning.context_extractor import extract_context_preview
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
    ) -> None:
        self._active_release_provider = active_release_provider
        self._legacy_approved_corpus_available = approved_corpus_available
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
            await asyncio.sleep(0.08)
            if not preserve_context:
                record.interpreted_context = extract_context_preview(record.question)
            await self._emit(record, QuestionStatus.CONTEXT_EXTRACTED)

            for status in (
                QuestionStatus.RETRIEVING,
                QuestionStatus.SEARCHING_COUNTER_EVIDENCE,
                QuestionStatus.CHECKING_EVIDENCE_COMPLETENESS,
                QuestionStatus.VERIFYING,
            ):
                await asyncio.sleep(0.08)
                await self._emit(record, status)

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
            await asyncio.sleep(0.08)
            await self._emit(record, QuestionStatus.ABSTAINED)
        except asyncio.CancelledError:
            raise
        except Exception:
            record.abstention = AbstentionDetail(
                reason_code="PIPELINE_FAILURE",
                message="The evidence pipeline failed closed and did not render a clinical claim.",
            )
            await self._emit(record, QuestionStatus.FAILED)

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
