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
    kind: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    pdf_page: int | None = Field(default=None, ge=1)
    printed_page: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    exact_highlight_available: bool = False

    @model_validator(mode="after")
    def validate_highlight(self) -> "EvidenceLocator":
        if self.bbox is not None:
            left, top, right, bottom = self.bbox
            if right <= left or bottom <= top:
                raise ValueError("an evidence locator bounding box must have positive dimensions")
        if self.exact_highlight_available and self.bbox is None:
            raise ValueError("an exact highlight requires a bounding box")
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


class WithheldClaimSummary(ApiContractModel):
    """How many proposed claims one validator withheld, and in which state.

    Deliberately carries no claim text. A withheld claim is unverified model output, and
    the entire point of withholding it is that it must not reach a reader; putting it in
    the response under a diagnostic name would hand it to exactly the audience the check
    protects. Counts by validator are enough to tell a careful model from a broken
    validator, which is what this exists for.
    """

    validator: str = Field(min_length=1)
    status: str = Field(min_length=1)
    count: int = Field(gt=0)


class VerificationSummary(ApiContractModel):
    rendered_claims: int = Field(default=0, ge=0)
    supported_claims: int = Field(default=0, ge=0)
    withheld_claims: int = Field(default=0, ge=0)
    withheld_by_validator: list[WithheldClaimSummary] = Field(
        default_factory=list, max_length=20
    )

    @model_validator(mode="after")
    def reconcile_counts(self) -> "VerificationSummary":
        if self.supported_claims + self.withheld_claims != self.rendered_claims:
            raise ValueError(
                "a verification summary must account for every rendered claim as either "
                "supported or withheld"
            )
        # One claim can fail several validators at once, so the per-validator counts sum
        # to at least the number of withheld claims rather than exactly to it.
        if self.withheld_by_validator and not self.withheld_claims:
            raise ValueError("per-validator counts require at least one withheld claim")
        return self


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
        referenced_evidence_ids = {
            evidence_id for claim in self.claims for evidence_id in claim.evidence_ids
        }
        detail_ids = [detail.evidence_id for detail in self.evidence_details]
        if len(detail_ids) != len(set(detail_ids)):
            raise ValueError("evidence detail IDs must be unique")
        if not set(detail_ids).issubset(referenced_evidence_ids):
            raise ValueError("evidence details must be referenced by a rendered claim")
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
