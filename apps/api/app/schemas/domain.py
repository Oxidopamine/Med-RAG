from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CanonicalModel(BaseModel):
    """Fail-closed base for canonical domain records."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SourceClass(str, Enum):
    E1 = "E1"
    E2 = "E2"
    E3 = "E3"
    E4 = "E4"
    E5 = "E5"
    E6 = "E6"
    E7 = "E7"


class SourceStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    DOWNLOADED = "DOWNLOADED"
    PARSING = "PARSING"
    QA_REQUIRED = "QA_REQUIRED"
    APPROVED = "APPROVED"
    EFFECTIVE = "EFFECTIVE"
    PARTIALLY_SUPERSEDED = "PARTIALLY_SUPERSEDED"
    SUPERSEDED = "SUPERSEDED"
    WITHDRAWN = "WITHDRAWN"
    ARCHIVED = "ARCHIVED"
    REJECTED = "REJECTED"
    QUARANTINED = "QUARANTINED"


# The single answer to "may a client be served content from a source in this state?"
# It lives here rather than in either consumer because retrieval and evidence-detail
# resolution answer the same question about the same record and must never diverge: a
# detail that renders for a record retrieval refuses to return is the system disagreeing
# with itself about what is current.
#
# Deliberately an allowlist. A state added to `SourceStatus` later is excluded until
# somebody decides it is safe to answer from, rather than admitted by default.
#
# `APPROVED` is excluded because approved-for-retrieval is not the same as in force.
# `PARTIALLY_SUPERSEDED` is excluded because neither consumer can tell which part of a
# record was superseded, and a superseded clause is textually indistinguishable from a
# current one.
SERVABLE_LIFECYCLE_STATES: tuple[SourceStatus, ...] = (SourceStatus.EFFECTIVE,)
SERVABLE_LIFECYCLE_VALUES: frozenset[str] = frozenset(
    status.value for status in SERVABLE_LIFECYCLE_STATES
)


class RelationshipType(str, Enum):
    SUPERSEDES = "SUPERSEDES"
    PARTIALLY_SUPERSEDES = "PARTIALLY_SUPERSEDES"
    AMENDS = "AMENDS"
    CORRECTS = "CORRECTS"
    WITHDRAWS = "WITHDRAWS"
    SUPPLEMENTS = "SUPPLEMENTS"
    ENDORSES = "ENDORSES"
    REPLACES_SECTION = "REPLACES_SECTION"
    INCORPORATES = "INCORPORATES"


class EvidenceType(str, Enum):
    RECOMMENDATION = "RECOMMENDATION"
    RECOMMENDATION_RATIONALE = "RECOMMENDATION_RATIONALE"
    DEFINITION = "DEFINITION"
    CONTRAINDICATION = "CONTRAINDICATION"
    WARNING = "WARNING"
    EXCEPTION = "EXCEPTION"
    DOSING = "DOSING"
    THRESHOLD = "THRESHOLD"
    TABLE = "TABLE"
    TABLE_ROW = "TABLE_ROW"
    TABLE_CELL = "TABLE_CELL"
    ALGORITHM_STEP = "ALGORITHM_STEP"
    FIGURE = "FIGURE"
    FOOTNOTE = "FOOTNOTE"
    SUPPORTING_TEXT = "SUPPORTING_TEXT"
    OTHER = "OTHER"


class EvidenceTrustStatus(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED_NATIVE = "VERIFIED_NATIVE"
    VERIFIED_CROSS_PARSER = "VERIFIED_CROSS_PARSER"
    HUMAN_VERIFIED = "HUMAN_VERIFIED"
    SUSPECT = "SUSPECT"
    QUARANTINED = "QUARANTINED"


class ClaimType(str, Enum):
    RECOMMENDATION = "RECOMMENDATION"
    DOSE = "DOSE"
    DURATION = "DURATION"
    CONTRAINDICATION = "CONTRAINDICATION"
    THRESHOLD = "THRESHOLD"
    DEFINITION = "DEFINITION"
    SOURCE_FACT = "SOURCE_FACT"


class ClaimRisk(str, Enum):
    LOW = "LOW"
    HIGH = "HIGH"


class CheckType(str, Enum):
    SOURCE_ALLOWED = "SOURCE_ALLOWED"
    SOURCE_EFFECTIVE = "SOURCE_EFFECTIVE"
    LICENSE_RENDER_ALLOWED = "LICENSE_RENDER_ALLOWED"
    EVIDENCE_EXISTS = "EVIDENCE_EXISTS"
    PROVENANCE_VALID = "PROVENANCE_VALID"
    QUOTE_EXACT = "QUOTE_EXACT"
    SEMANTIC_ENTAILMENT = "SEMANTIC_ENTAILMENT"
    INDEPENDENT_SEMANTIC_VERIFIER = "INDEPENDENT_SEMANTIC_VERIFIER"
    ELIGIBILITY_APPLICABLE = "ELIGIBILITY_APPLICABLE"
    REQUIRED_EVIDENCE_COMPLETE = "REQUIRED_EVIDENCE_COMPLETE"
    CONTRADICTION_CLEAR = "CONTRADICTION_CLEAR"
    NUMERIC_VALID = "NUMERIC_VALID"
    UNIT_VALID = "UNIT_VALID"
    OPERATOR_VALID = "OPERATOR_VALID"
    DURATION_VALID = "DURATION_VALID"
    DOSE_VALID = "DOSE_VALID"
    TABLE_CONTEXT_COMPLETE = "TABLE_CONTEXT_COMPLETE"


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNRESOLVED = "UNRESOLVED"


class ClaimDisposition(str, Enum):
    RENDERABLE = "RENDERABLE"
    WITHHELD = "WITHHELD"


class ApplicabilityStatus(str, Enum):
    MATCH = "MATCH"
    PARTIAL = "PARTIAL"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"


class Materiality(str, Enum):
    LOW = "LOW"
    HIGH = "HIGH"


class Measurement(CanonicalModel):
    concept: str = Field(min_length=1)
    value: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1)
    provenance: str = Field(default="USER_ENTERED", min_length=1)


# Field names a reader sees on the interpreted-context panel. An inferred marker naming
# anything else is a bug in the extractor rather than a fact about the patient.
CONTEXT_FIELDS = frozenset(
    {
        "age",
        "sex",
        "conditions",
        "known_absent_conditions",
        "measurements",
        "special_populations",
        "known_absent_special_populations",
        "care_setting",
        "jurisdiction",
        "question_type",
        "topic",
    }
)


class ClinicalContext(CanonicalModel):
    """What the system believes about the patient, and how it came to believe it.

    ``inferred_fields`` names the fields the extractor *derived* rather than read from the
    question. A stated age and an inferred one are not the same claim, and rendering both
    in identical type asks a reader to audit everything or nothing. Measurements carry
    their own ``provenance`` and are not listed here unless the whole list was derived.
    """

    age: int | None = Field(default=None, ge=0, le=130)
    sex: str | None = None
    conditions: frozenset[str] = Field(default_factory=frozenset)
    known_absent_conditions: frozenset[str] = Field(default_factory=frozenset)
    measurements: list[Measurement] = Field(default_factory=list)
    special_populations: frozenset[str] = Field(default_factory=frozenset)
    known_absent_special_populations: frozenset[str] = Field(default_factory=frozenset)
    care_setting: str | None = None
    jurisdiction: str | None = None
    question_type: str | None = None
    topic: str | None = None
    inferred_fields: frozenset[str] = Field(default_factory=frozenset)

    @model_validator(mode="after")
    def validate_known_state(self) -> "ClinicalContext":
        unknown_inferred = self.inferred_fields - CONTEXT_FIELDS
        if unknown_inferred:
            names = ", ".join(sorted(unknown_inferred))
            raise ValueError(f"inferred fields must name context fields: {names}")

        contradictory_conditions = self.conditions & self.known_absent_conditions
        if contradictory_conditions:
            names = ", ".join(sorted(contradictory_conditions))
            raise ValueError(f"conditions cannot be both present and absent: {names}")

        contradictory_populations = (
            self.special_populations & self.known_absent_special_populations
        )
        if contradictory_populations:
            names = ", ".join(sorted(contradictory_populations))
            raise ValueError(
                f"special populations cannot be both present and absent: {names}"
            )

        concepts = [measurement.concept for measurement in self.measurements]
        if len(concepts) != len(set(concepts)):
            raise ValueError("clinical context cannot contain duplicate measurement concepts")
        return self


class AgeCriterion(CanonicalModel):
    minimum: int | None = Field(default=None, ge=0)
    maximum: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_range(self) -> "AgeCriterion":
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("age minimum cannot exceed maximum")
        return self


class MeasurementCriterion(CanonicalModel):
    concept: str = Field(min_length=1)
    operator: Literal["<", "<=", "=", "==", ">=", ">"]
    value: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1)


class EligibilityRule(CanonicalModel):
    eligibility_rule_id: str = Field(min_length=1)
    age: AgeCriterion | None = None
    required_conditions: frozenset[str] = Field(default_factory=frozenset)
    excluded_conditions: frozenset[str] = Field(default_factory=frozenset)
    measurements: list[MeasurementCriterion] = Field(default_factory=list)
    pregnancy_allowed: bool | None = None
    allowed_settings: frozenset[str] = Field(default_factory=frozenset)
    materiality: Materiality = Materiality.HIGH

    @model_validator(mode="after")
    def validate_criteria(self) -> "EligibilityRule":
        overlap = self.required_conditions & self.excluded_conditions
        if overlap:
            names = ", ".join(sorted(overlap))
            raise ValueError(f"conditions cannot be both required and excluded: {names}")

        concepts = [criterion.concept for criterion in self.measurements]
        if len(concepts) != len(set(concepts)):
            raise ValueError("eligibility rule cannot repeat a measurement concept")
        return self


class ApplicabilityResult(CanonicalModel):
    status: ApplicabilityStatus
    matched: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)
    mismatched: list[str] = Field(default_factory=list)
    materiality: Materiality
    eligible_for_unconditional_render: bool


class SourceVersion(CanonicalModel):
    source_version_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    version_label: str = Field(min_length=1)
    status: SourceStatus
    effective_from: date | None = None
    effective_to: date | None = None
    approved_for_retrieval: bool = False

    @model_validator(mode="after")
    def validate_effective_range(self) -> "SourceVersion":
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_from > self.effective_to
        ):
            raise ValueError("effective_from cannot be after effective_to")
        return self


class LifecycleRelationship(CanonicalModel):
    source_relationship_id: str = Field(min_length=1)
    from_source_version_id: str = Field(min_length=1)
    to_source_version_id: str = Field(min_length=1)
    relationship_type: RelationshipType
    valid_from: date | None = None
    valid_to: date | None = None
    affected_section_ids: frozenset[str] = Field(default_factory=frozenset)
    affected_recommendation_ids: frozenset[str] = Field(default_factory=frozenset)
    affected_evidence_ids: frozenset[str] = Field(default_factory=frozenset)
    relationship_reason: str | None = None

    @model_validator(mode="after")
    def validate_relationship(self) -> "LifecycleRelationship":
        if self.from_source_version_id == self.to_source_version_id:
            raise ValueError("a lifecycle relationship cannot reference the same version")
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_from > self.valid_to
        ):
            raise ValueError("valid_from cannot be after valid_to")
        return self


class EvidenceObject(CanonicalModel):
    evidence_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_version_id: str = Field(min_length=1)
    source_file_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    evidence_type: EvidenceType
    section_id: str | None = None
    section_path: list[str] = Field(default_factory=list)
    recommendation_id: str | None = None
    recommendation_label: str | None = None
    text_exact: str = Field(min_length=1)
    text_normalized: str = Field(min_length=1)
    pdf_page: int = Field(ge=1)
    printed_page: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    source_extractor: str = Field(min_length=1)
    trust_status: EvidenceTrustStatus = EvidenceTrustStatus.UNVERIFIED
    exact_highlight_available: bool = False

    @field_validator("source_file_sha256")
    @classmethod
    def normalize_sha256(cls, value: str) -> str:
        return value.lower()

    @model_validator(mode="after")
    def require_bbox_for_highlight(self) -> "EvidenceObject":
        if self.exact_highlight_available and self.bbox is None:
            raise ValueError("an exact highlight requires a bounding box")
        if self.bbox is not None:
            left, top, right, bottom = self.bbox
            if right <= left or bottom <= top:
                raise ValueError("a bounding box must have positive width and height")
        return self


class Claim(CanonicalModel):
    claim_id: str = Field(min_length=1)
    claim_type: ClaimType
    text: str = Field(min_length=1)
    candidate_evidence_ids: list[str] = Field(default_factory=list)
    risk: ClaimRisk = ClaimRisk.LOW
    contains_numeric_content: bool = False
    uses_composite_table_evidence: bool = False

    @model_validator(mode="after")
    def require_unique_candidate_evidence(self) -> "Claim":
        if len(self.candidate_evidence_ids) != len(set(self.candidate_evidence_ids)):
            raise ValueError("candidate evidence IDs must be unique")
        return self


class VerificationCheck(CanonicalModel):
    check_type: CheckType
    status: CheckStatus
    evidence_ids: list[str] = Field(default_factory=list)
    reason_code: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_unique_evidence(self) -> "VerificationCheck":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("verification evidence IDs must be unique")
        return self


class ClaimEvaluation(CanonicalModel):
    claim: Claim
    disposition: ClaimDisposition
    evidence_ids: list[str] = Field(default_factory=list)
    checks: list[VerificationCheck]
    policy_version: str
    withheld_reasons: list[str] = Field(default_factory=list)
