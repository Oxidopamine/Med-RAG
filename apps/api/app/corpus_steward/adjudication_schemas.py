"""Immutable clinical benchmark adjudication and suite-building contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.corpus_steward.benchmark_schemas import (
    AdjudicatorReview,
    BenchmarkAcceptance,
    BenchmarkCase,
    BenchmarkSuite,
    BenchmarkSuitePartition,
    ClinicalSafetyTopic,
    RetrievalMode,
    RRFWeights,
)
from app.schemas.corpus import SHA256_PATTERN, canonical_sha256
from app.schemas.domain import CanonicalModel

ADJUDICATION_CONTRACT_VERSION = "1.0.0"
CLINICAL_PARTITIONS = frozenset(
    {BenchmarkSuitePartition.DEVELOPMENT, BenchmarkSuitePartition.SEALED_HOLDOUT}
)


def _sort_unique_strings(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
    if any(not value for value in values):
        raise ValueError(f"{label} cannot be empty")
    return tuple(sorted(values))


class BenchmarkPolicyKind(str, Enum):
    ACCESS = "ACCESS"
    ADJUDICATION_PROCESS = "ADJUDICATION_PROCESS"
    THRESHOLD = "THRESHOLD"


class BenchmarkAccessAction(str, Enum):
    BUILD_DEVELOPMENT_SUITE = "BUILD_DEVELOPMENT_SUITE"
    BUILD_SEALED_HOLDOUT_SUITE = "BUILD_SEALED_HOLDOUT_SUITE"


class BenchmarkAccessPolicyContent(CanonicalModel):
    schema_version: Literal[ADJUDICATION_CONTRACT_VERSION] = ADJUDICATION_CONTRACT_VERSION
    policy_id: str = Field(min_length=1, max_length=100)
    revision: str = Field(min_length=1, max_length=100)
    development_builder_identities: tuple[str, ...] = Field(min_length=1)
    holdout_custodian_identities: tuple[str, ...] = Field(min_length=1)
    candidate_team_identities: tuple[str, ...] = ()
    holdout_access_mode: Literal["CUSTODIAN_ONLY_UNTIL_ACCEPTANCE"] = (
        "CUSTODIAN_ONLY_UNTIL_ACCEPTANCE"
    )
    prohibit_candidate_team_holdout_access: Literal[True] = True
    require_access_audit_event: Literal[True] = True
    effective_at: datetime

    @field_validator(
        "development_builder_identities",
        "holdout_custodian_identities",
        "candidate_team_identities",
    )
    @classmethod
    def sort_identities(cls, value: tuple[str, ...], info) -> tuple[str, ...]:
        return _sort_unique_strings(value, label=info.field_name.replace("_", " "))

    @model_validator(mode="after")
    def keep_holdout_custodians_independent(self) -> BenchmarkAccessPolicyContent:
        overlap = set(self.holdout_custodian_identities) & set(
            self.candidate_team_identities
        )
        if overlap:
            raise ValueError(
                "holdout custodians cannot be members of the candidate team: "
                + ", ".join(sorted(overlap))
            )
        return self

    def permits(self, actor_identity: str, partition: BenchmarkSuitePartition) -> bool:
        if partition is BenchmarkSuitePartition.DEVELOPMENT:
            return actor_identity in self.development_builder_identities
        if partition is BenchmarkSuitePartition.SEALED_HOLDOUT:
            return (
                actor_identity in self.holdout_custodian_identities
                and actor_identity not in self.candidate_team_identities
            )
        return False


class BenchmarkAccessPolicy(CanonicalModel):
    content: BenchmarkAccessPolicyContent
    policy_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> BenchmarkAccessPolicy:
        if self.policy_sha256 != canonical_sha256(self.content):
            raise ValueError("benchmark access-policy digest is inconsistent")
        return self

    @classmethod
    def seal(cls, content: BenchmarkAccessPolicyContent) -> BenchmarkAccessPolicy:
        return cls(content=content, policy_sha256=canonical_sha256(content))


class AdjudicationProcessPolicyContent(CanonicalModel):
    schema_version: Literal[ADJUDICATION_CONTRACT_VERSION] = ADJUDICATION_CONTRACT_VERSION
    policy_id: str = Field(min_length=1, max_length=100)
    revision: str = Field(min_length=1, max_length=100)
    instructions_sha256: str = Field(pattern=SHA256_PATTERN)
    minimum_independent_reviews_per_case: int = Field(default=2, ge=2, le=20)
    allowed_clinical_roles: tuple[str, ...] = Field(min_length=1)
    allowed_resolver_roles: tuple[str, ...] = Field(min_length=1)
    require_resolution_for_disagreement: Literal[True] = True
    prohibit_candidate_team_adjudicators: Literal[True] = True
    case_decision_rule: Literal["EXACT_AGREEMENT_OR_RECORDED_RESOLUTION"] = (
        "EXACT_AGREEMENT_OR_RECORDED_RESOLUTION"
    )
    effective_at: datetime

    @field_validator("allowed_clinical_roles", "allowed_resolver_roles")
    @classmethod
    def sort_roles(cls, value: tuple[str, ...], info) -> tuple[str, ...]:
        return _sort_unique_strings(value, label=info.field_name.replace("_", " "))


class AdjudicationProcessPolicy(CanonicalModel):
    content: AdjudicationProcessPolicyContent
    policy_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> AdjudicationProcessPolicy:
        if self.policy_sha256 != canonical_sha256(self.content):
            raise ValueError("adjudication-process policy digest is inconsistent")
        return self

    @classmethod
    def seal(cls, content: AdjudicationProcessPolicyContent) -> AdjudicationProcessPolicy:
        return cls(content=content, policy_sha256=canonical_sha256(content))


class SafetyTopicSampleTarget(CanonicalModel):
    safety_topic: ClinicalSafetyTopic
    development_minimum: int = Field(gt=0)
    sealed_holdout_minimum: int = Field(gt=0)


class ThresholdDerivation(str, Enum):
    """How the numeric gates in this policy were arrived at.

    The distinction is not cosmetic. ``PRESPECIFIED_ABSOLUTE`` asserts that no
    measurement informed the numbers, which is the strong pre-registration claim.
    ``COMPARATOR_WILSON_95_LOWER_BOUND`` asserts something weaker and different: the
    gates are the Wilson-95 lower bounds of a comparator that was measured on this same
    suite, so they are posterior quantities and the policy is a non-inferiority design
    rather than an absolute one. Recording the weaker claim as the stronger one would
    make the sealed artifact misdescribe its own provenance.
    """

    PRESPECIFIED_ABSOLUTE = "PRESPECIFIED_ABSOLUTE"
    CONSUMER_REQUIREMENT = "CONSUMER_REQUIREMENT"
    COMPARATOR_WILSON_95_LOWER_BOUND = "COMPARATOR_WILSON_95_LOWER_BOUND"


class BenchmarkThresholdPolicyContent(CanonicalModel):
    schema_version: Literal[ADJUDICATION_CONTRACT_VERSION] = ADJUDICATION_CONTRACT_VERSION
    policy_id: str = Field(min_length=1, max_length=100)
    revision: str = Field(min_length=1, max_length=100)
    development_minimum_case_count: int = Field(gt=0)
    sealed_holdout_minimum_case_count: int = Field(gt=0)
    safety_topic_sample_targets: tuple[SafetyTopicSampleTarget, ...] = Field(min_length=1)
    minimum_exact_agreement_rate: float = Field(default=0.0, ge=0, le=1)
    development_acceptance: BenchmarkAcceptance
    sealed_holdout_acceptance: BenchmarkAcceptance
    binomial_uncertainty_method: Literal["WILSON_95"] = "WILSON_95"
    non_binomial_uncertainty_method: Literal["STRATIFIED_BOOTSTRAP_95"] = (
        "STRATIFIED_BOOTSTRAP_95"
    )
    threshold_derivation: ThresholdDerivation
    # A comparator-derived policy is still pre-registered with respect to the candidates
    # it will judge: what it may not claim is that no measurement produced its numbers.
    # This flag carries only the claim the policy can actually support.
    candidates_under_test_unevaluated: Literal[True] = True
    comparator_measured_at: datetime | None = None
    established_at: datetime

    @field_validator("safety_topic_sample_targets")
    @classmethod
    def sort_unique_targets(
        cls, value: tuple[SafetyTopicSampleTarget, ...]
    ) -> tuple[SafetyTopicSampleTarget, ...]:
        topics = [item.safety_topic for item in value]
        if len(topics) != len(set(topics)):
            raise ValueError("safety-topic sample targets must be unique")
        missing = set(ClinicalSafetyTopic) - set(topics)
        if missing:
            raise ValueError(
                "threshold policy must target every safety topic: "
                + ", ".join(sorted(item.value for item in missing))
            )
        return tuple(sorted(value, key=lambda item: item.safety_topic.value))

    @model_validator(mode="after")
    def validate_acceptance_gates(self) -> BenchmarkThresholdPolicyContent:
        if (
            self.development_acceptance.minimum_total_case_count
            < self.development_minimum_case_count
        ):
            raise ValueError("development acceptance case count is below its sample target")
        if (
            self.sealed_holdout_acceptance.minimum_total_case_count
            < self.sealed_holdout_minimum_case_count
        ):
            raise ValueError("holdout acceptance case count is below its sample target")
        for label, acceptance, target_attribute in (
            ("development", self.development_acceptance, "development_minimum"),
            ("sealed holdout", self.sealed_holdout_acceptance, "sealed_holdout_minimum"),
        ):
            gates = {
                item.stratum_value: item
                for item in acceptance.safety_strata
                if item.stratum_key == "safety_topic"
            }
            missing = {item.value for item in ClinicalSafetyTopic} - set(gates)
            if missing:
                raise ValueError(
                    f"{label} acceptance lacks safety-topic gates for: "
                    + ", ".join(sorted(missing))
                )
            targets = {
                item.safety_topic.value: getattr(item, target_attribute)
                for item in self.safety_topic_sample_targets
            }
            impossible = [
                topic
                for topic, gate in gates.items()
                if gate.minimum_case_count > targets[topic]
            ]
            if impossible:
                raise ValueError(
                    f"{label} gate minimum exceeds its declared sample target for: "
                    + ", ".join(sorted(impossible))
                )
        return self._validate_derivation()

    def _validate_derivation(self) -> BenchmarkThresholdPolicyContent:
        """Keep the declared derivation consistent with what the gates actually are."""

        acceptances = (
            ("development", self.development_acceptance),
            ("sealed holdout", self.sealed_holdout_acceptance),
        )
        comparator_declared = any(
            acceptance.comparator_candidate_id is not None for _, acceptance in acceptances
        )
        if self.threshold_derivation is ThresholdDerivation.COMPARATOR_WILSON_95_LOWER_BOUND:
            for label, acceptance in acceptances:
                if acceptance.comparator_candidate_id is None:
                    raise ValueError(
                        f"{label} acceptance declares comparator-derived thresholds "
                        "without pinning a comparator"
                    )
            if self.comparator_measured_at is None:
                raise ValueError(
                    "comparator-derived thresholds require the comparator measurement time"
                )
            if self.comparator_measured_at > self.established_at:
                raise ValueError(
                    "a comparator cannot be measured after the policy it produced"
                )
        elif comparator_declared:
            raise ValueError(
                "a pinned comparator contradicts a threshold derivation of "
                f"{self.threshold_derivation.value}"
            )
        elif self.comparator_measured_at is not None:
            raise ValueError(
                "comparator measurement time requires comparator-derived thresholds"
            )
        return self

    def acceptance_for(self, partition: BenchmarkSuitePartition) -> BenchmarkAcceptance:
        if partition is BenchmarkSuitePartition.DEVELOPMENT:
            return self.development_acceptance
        if partition is BenchmarkSuitePartition.SEALED_HOLDOUT:
            return self.sealed_holdout_acceptance
        raise ValueError("threshold policies apply only to clinical partitions")

    def minimum_case_count_for(self, partition: BenchmarkSuitePartition) -> int:
        if partition is BenchmarkSuitePartition.DEVELOPMENT:
            return self.development_minimum_case_count
        if partition is BenchmarkSuitePartition.SEALED_HOLDOUT:
            return self.sealed_holdout_minimum_case_count
        raise ValueError("threshold policies apply only to clinical partitions")


class BenchmarkThresholdPolicy(CanonicalModel):
    content: BenchmarkThresholdPolicyContent
    policy_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> BenchmarkThresholdPolicy:
        if self.policy_sha256 != canonical_sha256(self.content):
            raise ValueError("benchmark threshold-policy digest is inconsistent")
        return self

    @classmethod
    def seal(cls, content: BenchmarkThresholdPolicyContent) -> BenchmarkThresholdPolicy:
        return cls(content=content, policy_sha256=canonical_sha256(content))


class ClinicalReviewDecisionContent(CanonicalModel):
    schema_version: Literal[ADJUDICATION_CONTRACT_VERSION] = ADJUDICATION_CONTRACT_VERSION
    review_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    suite_partition: BenchmarkSuitePartition
    adjudicator_identity: str = Field(min_length=1, max_length=300)
    clinical_role: str = Field(min_length=1, max_length=300)
    independent_of_candidate_team: Literal[True] = True
    instructions_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_access_revision_sha256: str = Field(pattern=SHA256_PATTERN)
    decided_case: BenchmarkCase
    decided_at: datetime

    @model_validator(mode="after")
    def validate_case(self) -> ClinicalReviewDecisionContent:
        if self.suite_partition not in CLINICAL_PARTITIONS:
            raise ValueError("clinical review decisions require a development or holdout partition")
        if self.decided_case.case_id != self.case_id:
            raise ValueError("review decision case ID does not match its decided case")
        if self.decided_case.adjudication is not None:
            raise ValueError("imported reviewer decisions cannot pre-adjudicate a case")
        if not self.decided_case.safety_topics:
            raise ValueError("clinical reviewer decisions require at least one safety topic")
        return self

    def provenance(self) -> AdjudicatorReview:
        return AdjudicatorReview(
            review_id=self.review_id,
            adjudicator_identity=self.adjudicator_identity,
            clinical_role=self.clinical_role,
            independent_of_candidate_team=self.independent_of_candidate_team,
            instructions_sha256=self.instructions_sha256,
            evidence_access_revision_sha256=self.evidence_access_revision_sha256,
            decided_at=self.decided_at,
        )


class ClinicalReviewDecision(CanonicalModel):
    content: ClinicalReviewDecisionContent
    decision_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> ClinicalReviewDecision:
        if self.decision_sha256 != canonical_sha256(self.content):
            raise ValueError("clinical review decision digest is inconsistent")
        return self

    @classmethod
    def seal(cls, content: ClinicalReviewDecisionContent) -> ClinicalReviewDecision:
        return cls(content=content, decision_sha256=canonical_sha256(content))


class ClinicalReviewImport(CanonicalModel):
    decisions: tuple[ClinicalReviewDecision, ...] = Field(min_length=1)

    @field_validator("decisions")
    @classmethod
    def sort_unique_decisions(
        cls, value: tuple[ClinicalReviewDecision, ...]
    ) -> tuple[ClinicalReviewDecision, ...]:
        ids = [item.content.review_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("clinical review import repeats a review ID")
        return tuple(sorted(value, key=lambda item: item.content.review_id))


class DisagreementResolutionContent(CanonicalModel):
    schema_version: Literal[ADJUDICATION_CONTRACT_VERSION] = ADJUDICATION_CONTRACT_VERSION
    resolution_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    review_decision_sha256s: tuple[str, ...] = Field(min_length=2)
    resolver_identity: str = Field(min_length=1, max_length=300)
    resolver_clinical_role: str = Field(min_length=1, max_length=300)
    resolver_independent_of_candidate_team: Literal[True] = True
    resolution_note_sha256: str = Field(pattern=SHA256_PATTERN)
    finalized_case: BenchmarkCase
    resolved_at: datetime

    @field_validator("review_decision_sha256s")
    @classmethod
    def sort_decision_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = _sort_unique_strings(value, label="resolution decision digests")
        if any(
            len(item) != 64 or any(c not in "0123456789abcdef" for c in item)
            for item in normalized
        ):
            raise ValueError("resolution decision digests must be lowercase SHA-256 values")
        return normalized

    @model_validator(mode="after")
    def validate_final_case(self) -> DisagreementResolutionContent:
        if self.finalized_case.case_id != self.case_id:
            raise ValueError("resolution case ID does not match its finalized case")
        if self.finalized_case.adjudication is not None:
            raise ValueError("a disagreement resolution cannot pre-adjudicate its final case")
        return self


class DisagreementResolution(CanonicalModel):
    content: DisagreementResolutionContent
    resolution_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> DisagreementResolution:
        if self.resolution_sha256 != canonical_sha256(self.content):
            raise ValueError("disagreement-resolution digest is inconsistent")
        return self

    @classmethod
    def seal(cls, content: DisagreementResolutionContent) -> DisagreementResolution:
        return cls(content=content, resolution_sha256=canonical_sha256(content))


class DisagreementResolutionImport(CanonicalModel):
    resolutions: tuple[DisagreementResolution, ...] = Field(min_length=1)

    @field_validator("resolutions")
    @classmethod
    def sort_unique_resolutions(
        cls, value: tuple[DisagreementResolution, ...]
    ) -> tuple[DisagreementResolution, ...]:
        ids = [item.content.resolution_id for item in value]
        case_ids = [item.content.case_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("resolution import repeats a resolution ID")
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("resolution import repeats a case ID")
        return tuple(sorted(value, key=lambda item: item.content.resolution_id))


class AdjudicatedCaseRecord(CanonicalModel):
    case_id: str = Field(min_length=1, max_length=64)
    suite_partition: BenchmarkSuitePartition
    finalized_case: BenchmarkCase
    review_decision_sha256s: tuple[str, ...] = Field(min_length=2)
    exact_agreement: bool
    resolution_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)

    @field_validator("review_decision_sha256s")
    @classmethod
    def sort_reviews(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sort_unique_strings(value, label="adjudicated review decisions")

    @model_validator(mode="after")
    def validate_record(self) -> AdjudicatedCaseRecord:
        if self.suite_partition not in CLINICAL_PARTITIONS:
            raise ValueError("adjudicated cases require a clinical partition")
        if self.finalized_case.case_id != self.case_id:
            raise ValueError("adjudicated case ID is inconsistent")
        adjudication = self.finalized_case.adjudication
        if adjudication is None:
            raise ValueError("adjudicated case records require final adjudication provenance")
        if self.exact_agreement:
            if adjudication.disagreement_observed or self.resolution_sha256 is not None:
                raise ValueError("exactly agreed cases cannot carry a resolution")
        elif not adjudication.disagreement_observed or self.resolution_sha256 is None:
            raise ValueError("disagreed cases require a sealed resolution")
        return self


class AdjudicationAgreementReport(CanonicalModel):
    case_count: int = Field(gt=0)
    independent_review_count: int = Field(gt=0)
    exact_agreement_case_count: int = Field(ge=0)
    disagreement_case_count: int = Field(ge=0)
    exact_agreement_rate: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_accounting(self) -> AdjudicationAgreementReport:
        if self.exact_agreement_case_count + self.disagreement_case_count != self.case_count:
            raise ValueError("adjudication agreement case accounting is incomplete")
        expected = self.exact_agreement_case_count / self.case_count
        if abs(self.exact_agreement_rate - expected) > 1e-12:
            raise ValueError("adjudication agreement rate is inconsistent")
        return self


class BenchmarkAdjudicationRecordContent(CanonicalModel):
    schema_version: Literal[ADJUDICATION_CONTRACT_VERSION] = ADJUDICATION_CONTRACT_VERSION
    adjudication_record_id: str = Field(min_length=1, max_length=64)
    access_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    adjudication_process_sha256: str = Field(pattern=SHA256_PATTERN)
    cases: tuple[AdjudicatedCaseRecord, ...] = Field(min_length=1)
    agreement: AdjudicationAgreementReport
    sealed_at: datetime

    @field_validator("cases")
    @classmethod
    def sort_unique_cases(
        cls, value: tuple[AdjudicatedCaseRecord, ...]
    ) -> tuple[AdjudicatedCaseRecord, ...]:
        ids = [item.case_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("adjudication record repeats a case ID")
        return tuple(sorted(value, key=lambda item: item.case_id))

    @model_validator(mode="after")
    def validate_agreement(self) -> BenchmarkAdjudicationRecordContent:
        if self.agreement.case_count != len(self.cases):
            raise ValueError("adjudication agreement does not cover every case")
        if self.agreement.independent_review_count != sum(
            len(item.review_decision_sha256s) for item in self.cases
        ):
            raise ValueError("adjudication review accounting is inconsistent")
        return self


class BenchmarkAdjudicationRecord(CanonicalModel):
    content: BenchmarkAdjudicationRecordContent
    adjudication_record_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> BenchmarkAdjudicationRecord:
        if self.adjudication_record_sha256 != canonical_sha256(self.content):
            raise ValueError("benchmark adjudication-record digest is inconsistent")
        return self

    @classmethod
    def seal(
        cls, content: BenchmarkAdjudicationRecordContent
    ) -> BenchmarkAdjudicationRecord:
        return cls(
            content=content,
            adjudication_record_sha256=canonical_sha256(content),
        )


class AdjudicationSealRequest(CanonicalModel):
    adjudication_record_id: str = Field(min_length=1, max_length=64)
    access_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    adjudication_process_sha256: str = Field(pattern=SHA256_PATTERN)
    case_ids: tuple[str, ...] = Field(min_length=1)
    sealed_at: datetime

    @field_validator("case_ids")
    @classmethod
    def sort_case_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sort_unique_strings(value, label="adjudication case IDs")


class BenchmarkSuiteBuildRequest(CanonicalModel):
    benchmark_id: str = Field(min_length=1, max_length=100)
    suite_partition: BenchmarkSuitePartition
    adjudication_record_sha256: str = Field(pattern=SHA256_PATTERN)
    threshold_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_configuration_sha256: str = Field(pattern=SHA256_PATTERN)
    corpus_release_id: str = Field(min_length=1, max_length=64)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    actor_identity: str = Field(min_length=1, max_length=300)
    requested_at: datetime
    modes: tuple[RetrievalMode, ...] = (
        RetrievalMode.SPARSE,
        RetrievalMode.DENSE,
        RetrievalMode.HYBRID,
    )
    candidate_mode: RetrievalMode = RetrievalMode.HYBRID
    top_k: int = Field(default=10, gt=0, le=1_000)
    candidate_limit: int = Field(default=50, gt=0, le=10_000)
    rrf_k: int = Field(default=60, gt=0, le=100_000)
    rrf_weights: RRFWeights = Field(default_factory=RRFWeights)

    @field_validator("modes")
    @classmethod
    def unique_modes(cls, value: tuple[RetrievalMode, ...]) -> tuple[RetrievalMode, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("suite build retrieval modes must be non-empty and unique")
        return value

    @model_validator(mode="after")
    def validate_build(self) -> BenchmarkSuiteBuildRequest:
        if self.suite_partition not in CLINICAL_PARTITIONS:
            raise ValueError("suite builder creates only development or sealed-holdout suites")
        if self.candidate_mode not in self.modes:
            raise ValueError("candidate mode must be included in suite modes")
        if self.candidate_limit < self.top_k:
            raise ValueError("suite candidate limit cannot be smaller than top_k")
        return self


class BenchmarkSuiteBuildResult(CanonicalModel):
    suite: BenchmarkSuite
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    storage_key: str = Field(min_length=1, max_length=512)
    built_by: str = Field(min_length=1, max_length=300)
    built_at: datetime
