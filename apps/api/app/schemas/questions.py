from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.corpus import ActiveCorpusRelease
from app.schemas.domain import ClinicalContext, utc_now


class ApiContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


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


class SourceFilters(ApiContractModel):
    jurisdictions: list[str] = Field(default_factory=lambda: ["US", "EU", "UK"])
    organizations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_unique_filters(self) -> "SourceFilters":
        if len(self.jurisdictions) != len(set(self.jurisdictions)):
            raise ValueError("jurisdiction filters must be unique")
        if len(self.organizations) != len(set(self.organizations)):
            raise ValueError("organization filters must be unique")
        return self


class QuestionCreate(ApiContractModel):
    question: str = Field(min_length=3, max_length=4000)
    source_filters: SourceFilters = Field(default_factory=SourceFilters)
    conversation_id: str | None = None


class QuestionAccepted(ApiContractModel):
    question_id: str
    status: QuestionStatus


class ProgressEvent(ApiContractModel):
    question_id: str
    sequence: int
    status: QuestionStatus
    occurred_at: datetime = Field(default_factory=utc_now)


class RenderedClaim(ApiContractModel):
    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    verification_status: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_evidence_ids(self) -> "RenderedClaim":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("rendered claim evidence IDs must be unique")
        return self


class EvidenceLocator(ApiContractModel):
    """Where a passage sits in its source, as a client is told it.

    A ``TABLE_CELL`` anchor is defined by its table, row, and column. Projecting one
    without them leaves a locator that names a cell it cannot identify, so the three
    travel together or not at all.
    """

    kind: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    pdf_page: int | None = Field(default=None, ge=1)
    printed_page: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    exact_highlight_available: bool = False
    table_id: str | None = Field(default=None, min_length=1)
    row_index: int | None = Field(default=None, ge=0)
    column_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_highlight(self) -> "EvidenceLocator":
        if self.bbox is not None:
            left, top, right, bottom = self.bbox
            if right <= left or bottom <= top:
                raise ValueError("an evidence locator bounding box must have positive dimensions")
        if self.exact_highlight_available and self.bbox is None:
            raise ValueError("an exact highlight requires a bounding box")
        cell = (self.table_id, self.row_index, self.column_index)
        if any(part is not None for part in cell) and any(part is None for part in cell):
            raise ValueError("a table-cell address requires a table, a row, and a column")
        return self


class EvidenceDetail(ApiContractModel):
    evidence_id: str = Field(min_length=1)
    exact_text: str | None = Field(default=None, min_length=1)
    evidence_type: str | None = Field(default=None, min_length=1)
    evidence_roles: list[str] = Field(min_length=1, max_length=20)
    section_path: list[str] = Field(default_factory=list)
    source_id: str = Field(min_length=1)
    source_version_id: str = Field(min_length=1)
    source_title: str = Field(min_length=1)
    source_version_label: str = Field(min_length=1)
    publisher_name: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_class: str = Field(min_length=1)
    jurisdiction: str = Field(min_length=1)
    language: str = Field(min_length=2)
    lifecycle_status: str = Field(min_length=1)
    effective_from: date | None = None
    effective_to: date | None = None
    approval_status: Literal["APPROVED"] = "APPROVED"
    render_allowed: bool
    locators: list[EvidenceLocator] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def protect_restricted_content(self) -> "EvidenceDetail":
        if len(self.evidence_roles) != len(set(self.evidence_roles)):
            raise ValueError("evidence roles must be unique")
        if not self.render_allowed:
            if self.exact_text is not None:
                raise ValueError("restricted evidence cannot expose exact text")
            if any(locator.exact_highlight_available for locator in self.locators):
                raise ValueError("restricted evidence cannot expose exact highlights")
        return self


class RetrievalCandidate(ApiContractModel):
    """A retrieved passage that no rendered claim cites.

    Uncited evidence reaches a client through this field and nowhere else. Keeping it
    out of ``claims`` is the point: a candidate carries the rank it held in retrieval,
    so a reader can see how far past the cited support they have gone, and it can never
    be mistaken for the evidence a claim was verified against. Ranks are sparse by
    construction - the cited passages are the gaps.
    """

    evidence_id: str = Field(min_length=1)
    retrieval_rank: int = Field(ge=1)


class VerificationSummary(ApiContractModel):
    rendered_claims: int = 0
    supported_claims: int = 0
    withheld_claims: int = 0


class AbstentionDetail(ApiContractModel):
    reason_code: str
    message: str
    missing_evidence_roles: list[str] = Field(default_factory=list)
    closest_evidence_ids: list[str] = Field(default_factory=list)


class QuestionResult(ApiContractModel):
    question_id: str
    question: str
    status: QuestionStatus
    corpus_release: ActiveCorpusRelease | None = None
    interpreted_context: ClinicalContext | None = None
    claims: list[RenderedClaim] = Field(default_factory=list, max_length=100)
    evidence_details: list[EvidenceDetail] = Field(default_factory=list, max_length=100)
    retrieval_candidates: list[RetrievalCandidate] = Field(default_factory=list, max_length=100)
    conflicts: list[dict[str, str]] = Field(default_factory=list)
    verification_summary: VerificationSummary = Field(default_factory=VerificationSummary)
    abstention: AbstentionDetail | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> "QuestionResult":
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("rendered claim IDs must be unique")
        cited_evidence_ids = {
            evidence_id for claim in self.claims for evidence_id in claim.evidence_ids
        }
        candidate_ids = [candidate.evidence_id for candidate in self.retrieval_candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("retrieval candidate evidence IDs must be unique")
        if cited_evidence_ids & set(candidate_ids):
            raise ValueError("a cited evidence ID cannot also be an uncited retrieval candidate")
        candidate_ranks = [candidate.retrieval_rank for candidate in self.retrieval_candidates]
        if candidate_ranks != sorted(set(candidate_ranks)):
            raise ValueError("retrieval candidates must be listed in ascending, unique rank order")
        if self.retrieval_candidates and self.status is not QuestionStatus.ANSWER_READY:
            raise ValueError("only an answer-ready result can expose retrieval candidates")
        referenced_evidence_ids = cited_evidence_ids | set(candidate_ids)
        detail_ids = [detail.evidence_id for detail in self.evidence_details]
        if len(detail_ids) != len(set(detail_ids)):
            raise ValueError("evidence detail IDs must be unique")
        if not set(detail_ids).issubset(referenced_evidence_ids):
            raise ValueError(
                "evidence details must be referenced by a rendered claim or retrieval candidate"
            )
        if self.status is QuestionStatus.ANSWER_READY and self.abstention is not None:
            raise ValueError("an answer-ready result cannot include an abstention")
        if self.status is QuestionStatus.ANSWER_READY:
            if self.corpus_release is None:
                raise ValueError("an answer-ready result requires an active corpus release")
            if not self.claims:
                raise ValueError("an answer-ready result requires at least one rendered claim")
            if set(detail_ids) != referenced_evidence_ids:
                raise ValueError(
                    "an answer-ready result requires canonical details for every evidence ID"
                )
        if self.status in {QuestionStatus.ABSTAINED, QuestionStatus.FAILED}:
            if self.claims:
                raise ValueError("abstained and failed results cannot render claims")
            if self.evidence_details:
                raise ValueError("abstained and failed results cannot expose evidence details")
            if self.abstention is None:
                raise ValueError("abstained and failed results require an abstention detail")
        return self


class QuestionContextPatch(ApiContractModel):
    context: ClinicalContext
