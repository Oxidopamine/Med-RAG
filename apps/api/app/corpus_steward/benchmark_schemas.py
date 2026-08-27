"""Frozen contracts for release-bound retrieval benchmark suites and reports."""

from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from statistics import fmean
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.corpus import SHA256_PATTERN, EvidenceRole, canonical_sha256
from app.schemas.domain import CanonicalModel

BENCHMARK_CONTRACT_VERSION = "1.6.0"


class RetrievalMode(str, Enum):
    SPARSE = "sparse"
    DENSE = "dense"
    HYBRID = "hybrid"


class EvidenceExpectation(str, Enum):
    ANSWERABLE = "ANSWERABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class BenchmarkSuitePartition(str, Enum):
    DEVELOPMENT = "DEVELOPMENT"
    SEALED_HOLDOUT = "SEALED_HOLDOUT"
    SYNTHETIC = "SYNTHETIC"


class BenchmarkProvenanceMode(str, Enum):
    SYNTHETIC = "SYNTHETIC"
    INDEPENDENT_REVIEWED = "INDEPENDENT_REVIEWED"
    AUTOMATED_SOURCE_DERIVED = "AUTOMATED_SOURCE_DERIVED"


class ClinicalSafetyTopic(str, Enum):
    CONTRAINDICATION = "CONTRAINDICATION"
    APPLICABILITY = "APPLICABILITY"
    DOSE = "DOSE"
    MONITORING = "MONITORING"
    TERMINOLOGY = "TERMINOLOGY"
    MULTILINGUAL_CROSS_LINGUAL = "MULTILINGUAL_CROSS_LINGUAL"
    STALE_SOURCE = "STALE_SOURCE"
    WRONG_JURISDICTION = "WRONG_JURISDICTION"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NEGATION = "NEGATION"
    PARAPHRASED_INTENT = "PARAPHRASED_INTENT"


class AdjudicatorReview(CanonicalModel):
    review_id: str = Field(min_length=1, max_length=64)
    adjudicator_identity: str = Field(min_length=1, max_length=300)
    clinical_role: str = Field(min_length=1, max_length=300)
    independent_of_candidate_team: bool
    instructions_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_access_revision_sha256: str = Field(pattern=SHA256_PATTERN)
    decided_at: datetime


class CaseAdjudication(CanonicalModel):
    reviews: tuple[AdjudicatorReview, ...] = Field(min_length=1)
    disagreement_observed: bool
    resolution: Literal["NOT_REQUIRED", "RESOLVED"]
    resolver_identity: str | None = Field(default=None, max_length=300)
    resolution_note_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    resolved_at: datetime | None = None

    @field_validator("reviews")
    @classmethod
    def sort_unique_reviews(
        cls, value: tuple[AdjudicatorReview, ...]
    ) -> tuple[AdjudicatorReview, ...]:
        ids = [item.review_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("adjudicator review IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.review_id))

    @model_validator(mode="after")
    def validate_resolution(self) -> CaseAdjudication:
        if any(not item.independent_of_candidate_team for item in self.reviews):
            raise ValueError("clinical benchmark adjudicators must be independent")
        resolved_fields = (
            self.resolver_identity,
            self.resolution_note_sha256,
            self.resolved_at,
        )
        if self.disagreement_observed:
            if self.resolution != "RESOLVED" or any(item is None for item in resolved_fields):
                raise ValueError("adjudication disagreement requires a complete resolution")
        elif self.resolution != "NOT_REQUIRED" or any(
            item is not None for item in resolved_fields
        ):
            raise ValueError("non-disputed adjudication cannot declare resolution fields")
        return self


class AutomatedSourceEvidenceReference(CanonicalModel):
    evidence_id: str = Field(min_length=1, max_length=64)
    evidence_sha256: str = Field(pattern=SHA256_PATTERN)


class AutomatedSourceDerivedProvenance(CanonicalModel):
    provenance_type: Literal["AUTOMATED_SOURCE_DERIVED"] = "AUTOMATED_SOURCE_DERIVED"
    generator_name: str = Field(min_length=1, max_length=200)
    generator_version: str = Field(min_length=1, max_length=100)
    derivation_rule_id: str = Field(min_length=1, max_length=200)
    derivation_rules_sha256: str = Field(pattern=SHA256_PATTERN)
    source_evidence: tuple[AutomatedSourceEvidenceReference, ...] = Field(min_length=1)
    partition_assignment_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("source_evidence")
    @classmethod
    def sort_unique_sources(
        cls, value: tuple[AutomatedSourceEvidenceReference, ...]
    ) -> tuple[AutomatedSourceEvidenceReference, ...]:
        ids = [item.evidence_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("automated provenance source evidence IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.evidence_id))


class BenchmarkGoldEvidence(CanonicalModel):
    evidence_id: str = Field(min_length=1, max_length=64)
    relevance_grade: int = Field(default=1, ge=1, le=3)
    required: bool = True


class MinimumCompleteEvidenceSet(CanonicalModel):
    set_id: str = Field(min_length=1, max_length=64)
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @field_validator("evidence_ids")
    @classmethod
    def sort_unique_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("a minimum complete evidence set cannot repeat evidence IDs")
        return tuple(sorted(value))


class BenchmarkFilter(CanonicalModel):
    jurisdictions: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    publisher_ids: tuple[str, ...] = ()
    source_classes: tuple[str, ...] = ()
    source_version_ids: tuple[str, ...] = ()
    lifecycle_statuses: tuple[str, ...] = ()

    @field_validator(
        "jurisdictions",
        "languages",
        "publisher_ids",
        "source_classes",
        "source_version_ids",
        "lifecycle_statuses",
    )
    @classmethod
    def sort_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("benchmark filter values must be unique")
        if any(not item for item in value):
            raise ValueError("benchmark filter values cannot be empty")
        return tuple(sorted(value))


class BenchmarkCase(CanonicalModel):
    case_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=10_000)
    evidence_expectation: EvidenceExpectation = EvidenceExpectation.ANSWERABLE
    gold_evidence: tuple[BenchmarkGoldEvidence, ...] = ()
    minimum_complete_evidence_sets: tuple[MinimumCompleteEvidenceSet, ...] = ()
    required_evidence_roles: tuple[EvidenceRole, ...] = ()
    retrieval_filter: BenchmarkFilter = Field(default_factory=BenchmarkFilter)
    forbidden_evidence_ids: tuple[str, ...] = ()
    strata: dict[str, str] = Field(default_factory=dict)
    safety_topics: tuple[ClinicalSafetyTopic, ...] = ()
    adjudication: CaseAdjudication | None = None
    automated_provenance: AutomatedSourceDerivedProvenance | None = None

    @model_validator(mode="after")
    def keep_provenance_modes_exclusive(self) -> BenchmarkCase:
        if self.adjudication is not None and self.automated_provenance is not None:
            raise ValueError("a benchmark case cannot mix reviewed and automated provenance")
        return self

    @field_validator("gold_evidence")
    @classmethod
    def sort_unique_gold(
        cls, value: tuple[BenchmarkGoldEvidence, ...]
    ) -> tuple[BenchmarkGoldEvidence, ...]:
        ids = [item.evidence_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("benchmark gold evidence IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.evidence_id))

    @field_validator("required_evidence_roles")
    @classmethod
    def sort_unique_roles(cls, value: tuple[EvidenceRole, ...]) -> tuple[EvidenceRole, ...]:
        if len(value) != len(set(value)):
            raise ValueError("required benchmark evidence roles must be unique")
        return tuple(sorted(value, key=lambda item: item.value))

    @field_validator("minimum_complete_evidence_sets")
    @classmethod
    def sort_unique_complete_sets(
        cls, value: tuple[MinimumCompleteEvidenceSet, ...]
    ) -> tuple[MinimumCompleteEvidenceSet, ...]:
        ids = [item.set_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("minimum complete evidence set IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.set_id))

    @field_validator("forbidden_evidence_ids")
    @classmethod
    def sort_unique_forbidden(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("forbidden benchmark evidence IDs must be unique")
        return tuple(sorted(value))

    @field_validator("strata")
    @classmethod
    def validate_strata(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key or not item for key, item in value.items()):
            raise ValueError("benchmark stratum keys and values cannot be empty")
        return dict(sorted(value.items()))

    @field_validator("safety_topics")
    @classmethod
    def sort_unique_safety_topics(
        cls, value: tuple[ClinicalSafetyTopic, ...]
    ) -> tuple[ClinicalSafetyTopic, ...]:
        if len(value) != len(set(value)):
            raise ValueError("benchmark safety topics must be unique")
        return tuple(sorted(value, key=lambda item: item.value))

    @model_validator(mode="after")
    def validate_evidence_sets(self) -> BenchmarkCase:
        gold_ids = {item.evidence_id for item in self.gold_evidence}
        if gold_ids.intersection(self.forbidden_evidence_ids):
            raise ValueError("gold evidence cannot also be forbidden")
        complete_ids = {
            evidence_id
            for evidence_set in self.minimum_complete_evidence_sets
            for evidence_id in evidence_set.evidence_ids
        }
        if not complete_ids.issubset(gold_ids):
            raise ValueError("minimum complete evidence sets must reference gold evidence")
        if self.evidence_expectation is EvidenceExpectation.INSUFFICIENT_EVIDENCE:
            if self.gold_evidence:
                raise ValueError("insufficient-evidence cases cannot declare gold evidence")
            if self.minimum_complete_evidence_sets or self.required_evidence_roles:
                raise ValueError(
                    "insufficient-evidence cases cannot require evidence sets or roles"
                )
            if self.safety_topics and (
                ClinicalSafetyTopic.INSUFFICIENT_EVIDENCE not in self.safety_topics
            ):
                raise ValueError(
                    "an insufficient-evidence case must carry its matching safety topic"
                )
        elif not self.gold_evidence:
            raise ValueError("answerable benchmark cases require gold evidence")
        elif not self.minimum_complete_evidence_sets and not any(
            item.required for item in self.gold_evidence
        ):
            raise ValueError("an answerable case must declare required evidence")
        return self

    def complete_evidence_sets(self) -> tuple[frozenset[str], ...]:
        if self.minimum_complete_evidence_sets:
            return tuple(
                frozenset(item.evidence_ids)
                for item in self.minimum_complete_evidence_sets
            )
        required = frozenset(
            item.evidence_id for item in self.gold_evidence if item.required
        )
        return (required,) if required else ()


class RRFWeights(CanonicalModel):
    dense: float = Field(default=1.0, ge=0)
    sparse: float = Field(default=1.0, ge=0)

    @field_validator("dense", "sparse")
    @classmethod
    def finite_weight(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("RRF weights must be finite")
        return value

    @model_validator(mode="after")
    def require_lane(self) -> RRFWeights:
        if self.dense == 0 and self.sparse == 0:
            raise ValueError("at least one RRF lane weight must be positive")
        return self


class SafetyStratumThreshold(CanonicalModel):
    stratum_key: str = Field(min_length=1, max_length=100)
    stratum_value: str = Field(min_length=1, max_length=200)
    minimum_case_count: int = Field(default=1, gt=0)
    minimum_mean_recall_at_k: float = Field(default=0.0, ge=0, le=1)
    minimum_complete_evidence_set_rate: float = Field(default=0.0, ge=0, le=1)
    minimum_mean_required_role_recall: float = Field(default=0.0, ge=0, le=1)
    minimum_insufficient_evidence_accuracy: float = Field(default=0.0, ge=0, le=1)
    maximum_forbidden_leakage_cases: int = Field(default=0, ge=0)


class BenchmarkAcceptance(CanonicalModel):
    """Prespecified gates for one benchmark run.

    Absolute score floors are not transferable between corpora: retrieval effectiveness
    varies more with collection and topic difficulty than with system quality, so a bar
    copied from a leaderboard measures the corpus rather than the stack. Every gate here
    is therefore expected to be one of three defensible forms - derived from what a
    downstream consumer actually needs, stated as a confidence bound rather than a point
    estimate, or expressed relative to a named comparator that was measured on this same
    suite. Ranking-quality metrics that no safety argument depends on are reported rather
    than gated.
    """

    minimum_total_case_count: int = Field(default=1, gt=0)
    minimum_mean_recall_at_k: float = Field(default=0.8, ge=0, le=1)
    minimum_mean_ndcg_at_k: float = Field(default=0.8, ge=0, le=1)
    minimum_mean_reciprocal_rank: float = Field(default=0.8, ge=0, le=1)
    minimum_mean_context_precision_at_k: float = Field(default=0.0, ge=0, le=1)
    # R-precision degenerates to precision@1 on single-gold cases, so it measures top-1
    # ranking rather than evidence completeness. Nothing downstream consumes the top hit
    # alone; it is reported, and defaults to ungated.
    minimum_mean_r_precision: float = Field(default=0.0, ge=0, le=1)
    minimum_complete_evidence_set_rate: float = Field(default=1.0, ge=0, le=1)
    minimum_mean_required_role_recall: float = Field(default=1.0, ge=0, le=1)
    minimum_answerable_mean_recall_at_k: float = Field(default=0.0, ge=0, le=1)
    minimum_answerable_complete_evidence_set_rate: float = Field(default=0.0, ge=0, le=1)
    # The evidence-completeness gate is evaluated at the number of passages the rendering
    # layer will actually hand its verifier, not at the benchmark's retrieval depth.
    generation_context_budget: int | None = Field(default=None, gt=0, le=1_000)
    minimum_answerable_complete_evidence_at_budget_rate: float = Field(
        default=0.0, ge=0, le=1
    )
    # Gate the Wilson lower bound rather than the point estimate: "the lower bound clears
    # X" is a statistical claim about the population, "the mean clears X" is not.
    require_confidence_lower_bound: bool = True
    comparator_candidate_id: str | None = Field(default=None, min_length=1, max_length=100)
    comparator_report_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    comparator_answerable_complete_evidence_rate: float | None = Field(
        default=None, ge=0, le=1
    )
    minimum_comparator_margin: float = Field(default=0.0, ge=0, le=1)
    # A tuned comparator encodes whatever this suite's iteration history produced, so
    # non-inferiority against it cannot rule out that both systems overfit the suite
    # together. The naive comparator is the second floor that can't: an untuned lexical
    # baseline nobody iterated against. Clearing the tuned floor while failing the naive
    # one is the signature of a suite that has stopped measuring retrieval.
    naive_comparator_candidate_id: str | None = Field(
        default=None, min_length=1, max_length=100
    )
    naive_comparator_report_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    naive_comparator_answerable_complete_evidence_rate: float | None = Field(
        default=None, ge=0, le=1
    )
    minimum_naive_comparator_margin: float = Field(default=0.0, ge=0, le=1)
    maximum_forbidden_leakage_cases: int = Field(default=0, ge=0)
    maximum_candidate_failure_cases: int = Field(default=0, ge=0)
    maximum_p95_latency_ms: float = Field(default=2_000.0, gt=0)
    minimum_insufficient_evidence_accuracy: float = Field(default=1.0, ge=0, le=1)
    safety_strata: tuple[SafetyStratumThreshold, ...] = ()

    @model_validator(mode="after")
    def validate_comparator(self) -> BenchmarkAcceptance:
        for label, identifier, digest, rate, margin in (
            (
                "comparator",
                self.comparator_candidate_id,
                self.comparator_report_sha256,
                self.comparator_answerable_complete_evidence_rate,
                self.minimum_comparator_margin,
            ),
            (
                "naive comparator",
                self.naive_comparator_candidate_id,
                self.naive_comparator_report_sha256,
                self.naive_comparator_answerable_complete_evidence_rate,
                self.minimum_naive_comparator_margin,
            ),
        ):
            parts = (identifier, digest, rate)
            if any(item is not None for item in parts) and not all(
                item is not None for item in parts
            ):
                raise ValueError(
                    f"a {label} floor requires the candidate ID, report digest, and its "
                    "measured answerable complete-evidence rate together"
                )
            if margin and identifier is None:
                raise ValueError(f"a {label} margin requires a pinned {label}")
        if (
            self.naive_comparator_candidate_id is not None
            and self.naive_comparator_candidate_id == self.comparator_candidate_id
        ):
            raise ValueError(
                "the naive comparator must be a different candidate from the tuned "
                "comparator, otherwise it adds no independent floor"
            )
        if self.minimum_answerable_complete_evidence_at_budget_rate and (
            self.generation_context_budget is None
        ):
            raise ValueError(
                "an at-budget completeness gate requires a declared generation context budget"
            )
        return self

    @field_validator("safety_strata")
    @classmethod
    def sort_unique_strata(
        cls, value: tuple[SafetyStratumThreshold, ...]
    ) -> tuple[SafetyStratumThreshold, ...]:
        keys = [(item.stratum_key, item.stratum_value) for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("safety-stratum thresholds must be unique")
        return tuple(sorted(value, key=lambda item: (item.stratum_key, item.stratum_value)))


class BenchmarkSuiteContent(CanonicalModel):
    schema_version: Literal[BENCHMARK_CONTRACT_VERSION] = BENCHMARK_CONTRACT_VERSION
    benchmark_id: str = Field(min_length=1, max_length=100)
    suite_partition: BenchmarkSuitePartition
    provenance_mode: BenchmarkProvenanceMode
    access_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    adjudication_process_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    adjudication_record_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    generation_policy_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    generation_record_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    threshold_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    required_safety_topics: tuple[ClinicalSafetyTopic, ...] = ()
    candidate_configuration_sha256: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    corpus_release_id: str = Field(min_length=1, max_length=64)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    cases: tuple[BenchmarkCase, ...] = Field(min_length=1)
    modes: tuple[RetrievalMode, ...] = (
        RetrievalMode.SPARSE,
        RetrievalMode.DENSE,
        RetrievalMode.HYBRID,
    )
    candidate_mode: RetrievalMode = RetrievalMode.HYBRID
    # The evaluation depth is part of the measurement: every rank metric is defined at
    # it, and the context-precision ceiling is derived from it. Fusion parameters and
    # pool width are not - they describe the system under test and live on the candidate
    # configuration, so that retuning fusion does not require regenerating the suite the
    # fusion is being judged against.
    top_k: int = Field(default=10, gt=0, le=1_000)
    acceptance: BenchmarkAcceptance = Field(default_factory=BenchmarkAcceptance)

    @field_validator("cases")
    @classmethod
    def sort_unique_cases(cls, value: tuple[BenchmarkCase, ...]) -> tuple[BenchmarkCase, ...]:
        ids = [item.case_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("benchmark case IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.case_id))

    def context_precision_ceiling(self) -> float:
        """Return the highest mean context precision@k this suite can ever produce.

        Precision at a fixed depth is bounded by ``min(|gold|, k) / k``. A suite whose
        cases carry fewer gold records than ``top_k`` therefore has a hard ceiling below
        1.0, and a threshold above that ceiling can never be satisfied by any retrieval
        stack. Correctly abstaining insufficient-evidence cases score a full 1.0.
        """

        return fmean(
            1.0
            if case.evidence_expectation is EvidenceExpectation.INSUFFICIENT_EVIDENCE
            else min(len(case.gold_evidence), self.top_k) / self.top_k
            for case in self.cases
        )

    @field_validator("modes")
    @classmethod
    def unique_modes(cls, value: tuple[RetrievalMode, ...]) -> tuple[RetrievalMode, ...]:
        if not value:
            raise ValueError("a benchmark suite must specify at least one retrieval mode")
        if len(value) != len(set(value)):
            raise ValueError("benchmark retrieval modes must be unique")
        return value

    @field_validator("required_safety_topics")
    @classmethod
    def sort_unique_required_topics(
        cls, value: tuple[ClinicalSafetyTopic, ...]
    ) -> tuple[ClinicalSafetyTopic, ...]:
        if len(value) != len(set(value)):
            raise ValueError("required safety topics must be unique")
        return tuple(sorted(value, key=lambda item: item.value))

    @model_validator(mode="after")
    def validate_candidate_mode(self) -> BenchmarkSuiteContent:
        if self.candidate_mode not in self.modes:
            raise ValueError("benchmark candidate mode must be included in modes")
        if self.suite_partition is BenchmarkSuitePartition.SYNTHETIC:
            if self.provenance_mode is not BenchmarkProvenanceMode.SYNTHETIC:
                raise ValueError("synthetic suites require SYNTHETIC provenance")
            if any(
                value is not None
                for value in (
                    self.adjudication_process_sha256,
                    self.adjudication_record_sha256,
                    self.generation_policy_sha256,
                    self.generation_record_sha256,
                )
            ):
                raise ValueError("synthetic suites cannot bind production provenance records")
            if self.candidate_configuration_sha256 is None:
                raise ValueError("synthetic suites require a pinned candidate configuration")
        elif self.provenance_mode is BenchmarkProvenanceMode.INDEPENDENT_REVIEWED:
            if self.adjudication_process_sha256 is None or self.adjudication_record_sha256 is None:
                raise ValueError("reviewed suites require adjudication process and record digests")
            if (
                self.generation_policy_sha256 is not None
                or self.generation_record_sha256 is not None
            ):
                raise ValueError("reviewed suites cannot bind automated generation records")
            if any(case.adjudication is None for case in self.cases):
                raise ValueError("reviewed benchmark cases require adjudication provenance")
            if self.candidate_configuration_sha256 is None:
                raise ValueError("reviewed suites require a pinned candidate configuration")
        elif self.provenance_mode is BenchmarkProvenanceMode.AUTOMATED_SOURCE_DERIVED:
            if self.generation_policy_sha256 is None or self.generation_record_sha256 is None:
                raise ValueError("automated suites require generation policy and record digests")
            if (
                self.adjudication_process_sha256 is not None
                or self.adjudication_record_sha256 is not None
            ):
                raise ValueError("automated suites cannot bind reviewer adjudication records")
            if any(case.automated_provenance is None for case in self.cases):
                raise ValueError("automated benchmark cases require source-derived provenance")
            if any(case.adjudication is not None for case in self.cases):
                raise ValueError("automated benchmark cases cannot carry reviewer decisions")
            assignments = {
                case.automated_provenance.partition_assignment_sha256
                for case in self.cases
                if case.automated_provenance is not None
            }
            if len(assignments) != 1:
                raise ValueError("automated suite cases must bind one partition assignment")
            if (
                self.suite_partition is BenchmarkSuitePartition.SEALED_HOLDOUT
                and self.candidate_configuration_sha256 is not None
            ):
                raise ValueError(
                    "automated holdout suites bind the sealed candidate only at execution"
                )
        else:
            raise ValueError("non-synthetic suites require a production provenance mode")
        if self.suite_partition is not BenchmarkSuitePartition.SYNTHETIC:
            missing_declared = set(ClinicalSafetyTopic) - set(self.required_safety_topics)
            if missing_declared:
                raise ValueError("clinical suites must require every safety topic")
            covered = {topic for case in self.cases for topic in case.safety_topics}
            if missing_coverage := set(self.required_safety_topics) - covered:
                raise ValueError(
                    "clinical suite lacks case coverage for: "
                    + ", ".join(sorted(item.value for item in missing_coverage))
                )
            gated = {
                threshold.stratum_value
                for threshold in self.acceptance.safety_strata
                if threshold.stratum_key == "safety_topic"
            }
            if missing_gates := {
                item.value for item in self.required_safety_topics
            } - gated:
                raise ValueError(
                    "clinical suite lacks safety-topic gates for: "
                    + ", ".join(sorted(missing_gates))
                )
        budget = self.acceptance.generation_context_budget
        if budget is not None and budget > self.top_k:
            raise ValueError(
                "generation context budget cannot exceed the suite's retrieval depth: "
                f"{budget} > top_k={self.top_k}"
            )
        ceiling = self.context_precision_ceiling()
        if self.acceptance.minimum_mean_context_precision_at_k > ceiling + 1e-9:
            raise ValueError(
                "context-precision threshold is unsatisfiable for this suite: "
                f"required {self.acceptance.minimum_mean_context_precision_at_k:.4f} "
                f"exceeds the achievable ceiling {ceiling:.4f} at top_k={self.top_k}"
            )
        return self


class BenchmarkSuite(CanonicalModel):
    content: BenchmarkSuiteContent
    suite_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> BenchmarkSuite:
        if self.suite_sha256 != canonical_sha256(self.content):
            raise ValueError("benchmark suite digest does not match canonical content")
        return self

    @classmethod
    def seal(cls, content: BenchmarkSuiteContent) -> BenchmarkSuite:
        return cls(content=content, suite_sha256=canonical_sha256(content))


class RetrievedEvidence(CanonicalModel):
    evidence_id: str = Field(min_length=1, max_length=64)
    rank: int = Field(gt=0)
    score: float

    @field_validator("score")
    @classmethod
    def finite_score(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("retrieval scores must be finite")
        return value


class BenchmarkCaseMetrics(CanonicalModel):
    recall_at_k: float = Field(ge=0, le=1)
    ndcg_at_k: float = Field(ge=0, le=1)
    reciprocal_rank: float = Field(ge=0, le=1)
    context_precision_at_k: float = Field(ge=0, le=1)
    context_precision_ceiling_at_k: float = Field(ge=0, le=1)
    r_precision: float = Field(ge=0, le=1)
    complete_evidence_set_recalled: bool
    complete_evidence_set_recalled_at_budget: bool
    required_role_recall: float = Field(ge=0, le=1)
    forbidden_leakage_ids: tuple[str, ...] = ()
    insufficient_evidence_correct: bool | None = None
    latency_ms: float = Field(ge=0)


class BenchmarkCaseResult(CanonicalModel):
    case_id: str = Field(min_length=1, max_length=64)
    mode: RetrievalMode
    retrieved: tuple[RetrievedEvidence, ...]
    metrics: BenchmarkCaseMetrics
    candidate_failures: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_ranking(self) -> BenchmarkCaseResult:
        ranks = tuple(item.rank for item in self.retrieved)
        if ranks != tuple(range(1, len(ranks) + 1)):
            raise ValueError("retrieved evidence ranks must be contiguous")
        ids = [item.evidence_id for item in self.retrieved]
        if len(ids) != len(set(ids)):
            raise ValueError("retrieval results cannot repeat evidence IDs")
        return self


class BenchmarkModeSummary(CanonicalModel):
    mode: RetrievalMode
    case_count: int = Field(gt=0)
    mean_recall_at_k: float = Field(ge=0, le=1)
    mean_ndcg_at_k: float = Field(ge=0, le=1)
    mean_reciprocal_rank: float = Field(ge=0, le=1)
    mean_context_precision_at_k: float = Field(ge=0, le=1)
    mean_context_precision_ceiling_at_k: float = Field(ge=0, le=1)
    mean_r_precision: float = Field(ge=0, le=1)
    complete_evidence_set_rate: float = Field(ge=0, le=1)
    generation_context_budget: int = Field(gt=0)
    complete_evidence_at_budget_rate: float = Field(ge=0, le=1)
    mean_required_role_recall: float = Field(ge=0, le=1)
    forbidden_leakage_case_count: int = Field(ge=0)
    candidate_failure_case_count: int = Field(ge=0)
    answerable_case_count: int = Field(ge=0)
    answerable_mean_recall_at_k: float | None = Field(default=None, ge=0, le=1)
    answerable_mean_ndcg_at_k: float | None = Field(default=None, ge=0, le=1)
    answerable_mean_reciprocal_rank: float | None = Field(default=None, ge=0, le=1)
    answerable_mean_context_precision_at_k: float | None = Field(default=None, ge=0, le=1)
    answerable_mean_r_precision: float | None = Field(default=None, ge=0, le=1)
    answerable_complete_evidence_set_rate: float | None = Field(default=None, ge=0, le=1)
    answerable_complete_evidence_at_budget_rate: float | None = Field(
        default=None, ge=0, le=1
    )
    answerable_mean_required_role_recall: float | None = Field(default=None, ge=0, le=1)
    answerable_complete_evidence_set_confidence: MetricConfidenceInterval | None = None
    answerable_complete_evidence_at_budget_confidence: MetricConfidenceInterval | None = None
    insufficient_evidence_case_count: int = Field(ge=0)
    insufficient_evidence_accuracy: float | None = Field(default=None, ge=0, le=1)
    complete_evidence_set_confidence: MetricConfidenceInterval | None = None
    insufficient_evidence_confidence: MetricConfidenceInterval | None = None
    p95_latency_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_partition_accounting(self) -> BenchmarkModeSummary:
        if self.answerable_case_count + self.insufficient_evidence_case_count != self.case_count:
            raise ValueError("answerable and insufficient-evidence cases must sum to the total")
        answerable = (
            self.answerable_mean_recall_at_k,
            self.answerable_mean_ndcg_at_k,
            self.answerable_mean_reciprocal_rank,
            self.answerable_mean_context_precision_at_k,
            self.answerable_mean_r_precision,
            self.answerable_complete_evidence_set_rate,
            self.answerable_complete_evidence_at_budget_rate,
            self.answerable_mean_required_role_recall,
            self.answerable_complete_evidence_set_confidence,
            self.answerable_complete_evidence_at_budget_confidence,
        )
        if bool(self.answerable_case_count) != all(item is not None for item in answerable):
            raise ValueError("answerable-only accounting is incomplete")
        return self


class MetricConfidenceInterval(CanonicalModel):
    method: Literal["WILSON_95"] = "WILSON_95"
    sample_count: int = Field(ge=0)
    successes: int = Field(ge=0)
    lower: float = Field(ge=0, le=1)
    upper: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_counts(self) -> MetricConfidenceInterval:
        if self.successes > self.sample_count:
            raise ValueError("confidence successes cannot exceed sample count")
        if self.lower > self.upper:
            raise ValueError("confidence interval bounds are reversed")
        return self


class BenchmarkStratumSummary(CanonicalModel):
    stratum_key: str = Field(min_length=1, max_length=100)
    stratum_value: str = Field(min_length=1, max_length=200)
    case_count: int = Field(gt=0)
    mean_recall_at_k: float = Field(ge=0, le=1)
    complete_evidence_set_rate: float = Field(ge=0, le=1)
    mean_required_role_recall: float = Field(ge=0, le=1)
    forbidden_leakage_case_count: int = Field(ge=0)
    insufficient_evidence_case_count: int = Field(ge=0)
    insufficient_evidence_accuracy: float | None = Field(default=None, ge=0, le=1)
    complete_evidence_set_confidence: MetricConfidenceInterval
    insufficient_evidence_confidence: MetricConfidenceInterval | None = None


class BenchmarkProvenanceSummary(CanonicalModel):
    provenance_mode: BenchmarkProvenanceMode
    case_count: int = Field(gt=0)
    adjudicated_case_count: int = Field(ge=0)
    automated_case_count: int = Field(ge=0)
    source_evidence_count: int = Field(ge=0)
    independent_review_count: int = Field(ge=0)
    disagreement_case_count: int = Field(ge=0)
    disagreement_rate: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_accounting(self) -> BenchmarkProvenanceSummary:
        if self.adjudicated_case_count > self.case_count:
            raise ValueError("adjudicated cases cannot exceed total benchmark cases")
        if self.automated_case_count > self.case_count:
            raise ValueError("automated cases cannot exceed total benchmark cases")
        if self.adjudicated_case_count and self.automated_case_count:
            raise ValueError("a provenance summary cannot mix reviewed and automated cases")
        if self.disagreement_case_count > self.adjudicated_case_count:
            raise ValueError("disagreements cannot exceed adjudicated cases")
        if (self.disagreement_rate is None) != (self.adjudicated_case_count == 0):
            raise ValueError("adjudication disagreement rate has inconsistent accounting")
        if self.provenance_mode is BenchmarkProvenanceMode.INDEPENDENT_REVIEWED:
            if self.adjudicated_case_count != self.case_count or self.automated_case_count:
                raise ValueError("reviewed provenance accounting is incomplete")
        elif self.provenance_mode is BenchmarkProvenanceMode.AUTOMATED_SOURCE_DERIVED:
            if self.automated_case_count != self.case_count or self.adjudicated_case_count:
                raise ValueError("automated provenance accounting is incomplete")
            if self.source_evidence_count == 0:
                raise ValueError("automated provenance must account for source evidence")
        elif self.adjudicated_case_count or self.automated_case_count:
            raise ValueError("synthetic provenance cannot account for production cases")
        return self


class BenchmarkReportContent(CanonicalModel):
    schema_version: Literal[BENCHMARK_CONTRACT_VERSION] = BENCHMARK_CONTRACT_VERSION
    runner_version: str = Field(min_length=1, max_length=100)
    benchmark_id: str = Field(min_length=1, max_length=100)
    suite_partition: BenchmarkSuitePartition
    provenance_mode: BenchmarkProvenanceMode
    access_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    adjudication_process_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    adjudication_record_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    generation_policy_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    generation_record_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    threshold_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    benchmark_suite_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_configuration_sha256: str = Field(pattern=SHA256_PATTERN)
    corpus_release_id: str = Field(min_length=1, max_length=64)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    vector_batch_sha256: str = Field(pattern=SHA256_PATTERN)
    qdrant_collection: str = Field(min_length=1, max_length=255)
    top_k: int = Field(gt=0)
    candidate_limit: int = Field(gt=0)
    rrf_k: int = Field(gt=0)
    rrf_weights: RRFWeights
    candidate_mode: RetrievalMode
    case_results: tuple[BenchmarkCaseResult, ...] = Field(min_length=1)
    mode_summaries: tuple[BenchmarkModeSummary, ...] = Field(min_length=1)
    stratum_summaries: tuple[BenchmarkStratumSummary, ...] = ()
    provenance_summary: BenchmarkProvenanceSummary
    generated_at: datetime
    outcome: Literal["ACCEPTED", "REJECTED"]
    blockers: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_outcome(self) -> BenchmarkReportContent:
        if (self.outcome == "ACCEPTED") == bool(self.blockers):
            raise ValueError("accepted reports cannot have blockers; rejected reports require them")
        modes = [item.mode for item in self.mode_summaries]
        if len(modes) != len(set(modes)):
            raise ValueError("benchmark report modes must be unique")
        case_counts = {item.case_count for item in self.mode_summaries}
        if len(case_counts) != 1:
            raise ValueError("benchmark report mode case counts differ")
        case_count = case_counts.pop()
        expected_results = len(self.mode_summaries) * case_count
        if len(self.case_results) != expected_results:
            raise ValueError("benchmark report case accounting is incomplete")
        result_keys = {(item.mode, item.case_id) for item in self.case_results}
        if len(result_keys) != len(self.case_results):
            raise ValueError("benchmark report repeats a mode/case result")
        if {item.mode for item in self.case_results} != set(modes):
            raise ValueError("benchmark report result and summary modes differ")
        if self.candidate_mode not in modes:
            raise ValueError("benchmark candidate mode has no report summary")
        if self.provenance_summary.case_count != case_count:
            raise ValueError("benchmark provenance and retrieval case counts differ")
        if self.provenance_summary.provenance_mode is not self.provenance_mode:
            raise ValueError("benchmark report provenance mode is inconsistent")
        stratum_keys = [
            (item.stratum_key, item.stratum_value) for item in self.stratum_summaries
        ]
        if len(stratum_keys) != len(set(stratum_keys)):
            raise ValueError("benchmark report repeats a stratum summary")
        return self


class BenchmarkReport(CanonicalModel):
    content: BenchmarkReportContent
    report_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> BenchmarkReport:
        if self.report_sha256 != canonical_sha256(self.content):
            raise ValueError("benchmark report digest does not match canonical content")
        return self

    @classmethod
    def seal(cls, content: BenchmarkReportContent) -> BenchmarkReport:
        return cls(content=content, report_sha256=canonical_sha256(content))


class BenchmarkAcceptanceAttestationContent(CanonicalModel):
    schema_version: Literal[BENCHMARK_CONTRACT_VERSION] = BENCHMARK_CONTRACT_VERSION
    acceptance_id: str = Field(min_length=1, max_length=64)
    suite_partition: Literal["SEALED_HOLDOUT"] = "SEALED_HOLDOUT"
    benchmark_suite_sha256: str = Field(pattern=SHA256_PATTERN)
    benchmark_report_sha256: str = Field(pattern=SHA256_PATTERN)
    runner_version: str = Field(min_length=1, max_length=100)
    candidate_configuration_sha256: str = Field(pattern=SHA256_PATTERN)
    corpus_release_id: str = Field(min_length=1, max_length=64)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    vector_batch_sha256: str = Field(pattern=SHA256_PATTERN)
    qdrant_collection: str = Field(min_length=1, max_length=255)
    index_attestation_sha256: str = Field(pattern=SHA256_PATTERN)
    holdout_access_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    provenance_mode: BenchmarkProvenanceMode
    adjudication_process_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    adjudication_record_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    generation_policy_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    generation_record_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    threshold_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    outcome: Literal["ACCEPTED"] = "ACCEPTED"
    accepted_at: datetime
    valid_until: datetime

    @model_validator(mode="after")
    def validate_window(self) -> BenchmarkAcceptanceAttestationContent:
        if self.valid_until <= self.accepted_at:
            raise ValueError("benchmark acceptance must expire after it is accepted")
        if self.provenance_mode is BenchmarkProvenanceMode.AUTOMATED_SOURCE_DERIVED:
            if self.generation_policy_sha256 is None or self.generation_record_sha256 is None:
                raise ValueError("automated acceptance requires generation provenance")
            if (
                self.adjudication_process_sha256 is not None
                or self.adjudication_record_sha256 is not None
            ):
                raise ValueError("automated acceptance cannot bind reviewer adjudication")
        elif self.provenance_mode is BenchmarkProvenanceMode.INDEPENDENT_REVIEWED:
            if self.adjudication_process_sha256 is None or self.adjudication_record_sha256 is None:
                raise ValueError("reviewed acceptance requires adjudication provenance")
            if (
                self.generation_policy_sha256 is not None
                or self.generation_record_sha256 is not None
            ):
                raise ValueError("reviewed acceptance cannot bind automated generation")
        else:
            raise ValueError("sealed-holdout acceptance requires production provenance")
        return self


class SignedBenchmarkAcceptance(CanonicalModel):
    content: BenchmarkAcceptanceAttestationContent
    statement_sha256: str = Field(pattern=SHA256_PATTERN)
    signature_sha256: str = Field(pattern=SHA256_PATTERN)
    signer_identity: str = Field(min_length=1, max_length=300)
    signing_key_id: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def verify_statement_digest(self) -> SignedBenchmarkAcceptance:
        if self.statement_sha256 != canonical_sha256(self.content):
            raise ValueError("benchmark acceptance statement digest is inconsistent")
        return self

    @classmethod
    def seal(
        cls,
        content: BenchmarkAcceptanceAttestationContent,
        *,
        signature_sha256: str,
        signer_identity: str,
        signing_key_id: str,
    ) -> SignedBenchmarkAcceptance:
        return cls(
            content=content,
            statement_sha256=canonical_sha256(content),
            signature_sha256=signature_sha256,
            signer_identity=signer_identity,
            signing_key_id=signing_key_id,
        )
