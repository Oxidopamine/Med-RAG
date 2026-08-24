from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.schemas.domain import ClinicalContext, utc_now


class QuestionStatus(str, Enum):
    QUEUED = "QUEUED"
    CONTEXT_EXTRACTED = "CONTEXT_EXTRACTED"
    RETRIEVING = "RETRIEVING"
    RERANKING = "RERANKING"
    SEARCHING_COUNTER_EVIDENCE = "SEARCHING_COUNTER_EVIDENCE"
    CHECKING_EVIDENCE_COMPLETENESS = "CHECKING_EVIDENCE_COMPLETENESS"
    VERIFYING = "VERIFYING"
    ANSWER_READY = "ANSWER_READY"
    ABSTAINED = "ABSTAINED"
    FAILED = "FAILED"


TERMINAL_STATUSES = {
    QuestionStatus.ANSWER_READY,
    QuestionStatus.ABSTAINED,
    QuestionStatus.FAILED,
}


class SourceFilters(BaseModel):
    jurisdictions: list[str] = Field(default_factory=lambda: ["US", "EU", "UK"])
    organizations: list[str] = Field(default_factory=list)


class QuestionCreate(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
    source_filters: SourceFilters = Field(default_factory=SourceFilters)
    conversation_id: str | None = None


class QuestionAccepted(BaseModel):
    question_id: str
    status: QuestionStatus


class ProgressEvent(BaseModel):
    question_id: str
    sequence: int
    status: QuestionStatus
    occurred_at: datetime = Field(default_factory=utc_now)


class RenderedClaim(BaseModel):
    claim_id: str
    text: str
    evidence_ids: list[str]
    verification_status: str


class VerificationSummary(BaseModel):
    rendered_claims: int = 0
    supported_claims: int = 0
    withheld_claims: int = 0


class AbstentionDetail(BaseModel):
    reason_code: str
    message: str
    missing_evidence_roles: list[str] = Field(default_factory=list)
    closest_evidence_ids: list[str] = Field(default_factory=list)


class QuestionResult(BaseModel):
    question_id: str
    question: str
    status: QuestionStatus
    interpreted_context: ClinicalContext | None = None
    claims: list[RenderedClaim] = Field(default_factory=list)
    conflicts: list[dict[str, str]] = Field(default_factory=list)
    verification_summary: VerificationSummary = Field(default_factory=VerificationSummary)
    abstention: AbstentionDetail | None = None
    created_at: datetime
    updated_at: datetime


class QuestionContextPatch(BaseModel):
    context: ClinicalContext

