"""Deterministic source-derived benchmark generation with sealed partitioning."""

from __future__ import annotations

import hashlib
import hmac
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.corpus_steward.adjudication_schemas import (
    BenchmarkAccessPolicy,
    BenchmarkThresholdPolicy,
)
from app.corpus_steward.benchmark_schemas import (
    AutomatedSourceDerivedProvenance,
    AutomatedSourceEvidenceReference,
    BenchmarkCase,
    BenchmarkFilter,
    BenchmarkGoldEvidence,
    BenchmarkProvenanceMode,
    BenchmarkSuite,
    BenchmarkSuiteContent,
    BenchmarkSuitePartition,
    ClinicalSafetyTopic,
    EvidenceExpectation,
    MinimumCompleteEvidenceSet,
    RetrievalMode,
    RRFWeights,
)
from app.schemas.corpus import (
    SHA256_PATTERN,
    CorpusEvidenceRecord,
    CorpusReleaseBundle,
    EvidenceRole,
    canonical_sha256,
)
from app.schemas.domain import CanonicalModel

SOURCE_DERIVED_CONTRACT_VERSION = "1.0.0"
SOURCE_DERIVED_GENERATOR_NAME = "med-rag/source-derived-benchmark"
SOURCE_DERIVED_GENERATOR_VERSION = "1.2.0"

DERIVATION_RULES = {
    "APPLICABILITY": "role-or-applicability-fields",
    "CONFLICTING_EVIDENCE": "polarity-aware-token-overlap-pair",
    "CONTRAINDICATION": "exception-role-or-negative-restriction-language",
    "DOSE": "dose-role-or-numeric-dose-threshold-language",
    "INSUFFICIENT_EVIDENCE": "absent-publisher-filter-negative",
    "MONITORING": "monitoring-role-or-monitoring-language",
    "MULTILINGUAL_CROSS_LINGUAL": "shared-identifier-positive-or-unsupported-language-negative",
    "NEGATION": "explicit-negative-language",
    "PARAPHRASED_INTENT": "controlled-vocabulary-paraphrase",
    "STALE_SOURCE": "excluded-lifecycle-filter-negative",
    "TERMINOLOGY": "source-code-or-acronym-query",
    "WRONG_JURISDICTION": "absent-jurisdiction-filter-negative",
    "paraphrase": "controlled-vocabulary-substitution-v2-non-lexical-rank",
    "partition": "hmac-sha256-secret-ranked-disjoint-source-halves",
    "query": "normalized-source-fragment-v1",
}

# A paraphrase case must not hand the retriever the passage verbatim, it must stay
# answerable, and it must not be solvable by lexical matching alone. All three properties
# are enforced numerically at generation time: at most MAX_PARAPHRASE_LEXICAL_OVERLAP of
# the query terms may appear in the source passage, and the source record's IDF-weighted
# lexical rank in the whole release must fall inside
# [MIN_PARAPHRASE_LEXICAL_RANK, MAX_PARAPHRASE_LEXICAL_RANK] - findable, but never
# already first. Requiring rank 1 instead, as the first version of this rule did, selects
# precisely the lexically unambiguous records and produces the easiest stratum in the
# suite rather than a discriminating one.
#
# The upper bound is deliberately larger than any serving retrieval depth and is NOT
# derived from top_k. Tying it to the depth would make the stratum's difficulty move
# whenever the retrieval depth was retuned, so a candidate could be made to look better
# by widening its own output - and it would place every case inside the depth by
# construction, which caps how much the stratum can stress recall.
MAX_PARAPHRASE_LEXICAL_OVERLAP = 0.55
MIN_PARAPHRASE_LEXICAL_RANK = 2
MAX_PARAPHRASE_LEXICAL_RANK = 20
MIN_PARAPHRASE_TERMS = 8
MIN_PARAPHRASE_SUBSTITUTIONS = 3
MIN_PARAPHRASE_RETAINED_TERMS = 3
MAX_PARAPHRASE_TERMS = 60
DERIVATION_RULES_SHA256 = canonical_sha256(DERIVATION_RULES)

_NEGATION_RE = re.compile(
    r"\b(?:not|no|never|without|avoid|contraindicat\w*|mustn['’]?t|shouldn['’]?t)\b",
    re.IGNORECASE,
)
_DOSE_RE = re.compile(
    r"\b(?:\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|mmol|units?|copies|cells)|dose|dosage|threshold)\b",
    re.IGNORECASE,
)
_MONITORING_RE = re.compile(
    r"\b(?:monitor\w*|follow[- ]?up|screen\w*|test(?:ing)?|assess\w*|surveillance)\b",
    re.IGNORECASE,
)
_APPLICABILITY_RE = re.compile(
    r"\b(?:eligible|eligibility|population|adult|child|adolescen\w*|pregnan\w*|infant|criteria)\b",
    re.IGNORECASE,
)
_PARAPHRASE_VOCABULARY: dict[str, str] = {
    "adherence": "compliance",
    "adolescent": "young person",
    "adolescents": "young people",
    "algorithm": "decision procedure",
    "all": "every",
    "art": "antiretroviral therapy",
    "assess": "appraise",
    "can": "may",
    "cancer": "malignancy",
    "capture": "log",
    "care": "clinical management",
    "cervical": "of the cervix",
    "client": "patient",
    "clients": "patients",
    "coding": "classification encoding",
    "collection": "gathering",
    "consolidated": "unified",
    "contact": "exposed individual",
    "counseling": "advisory session",
    "counselling": "advisory session",
    "count": "enumeration",
    "current": "present-day",
    "date": "calendar day",
    "delivery": "childbirth",
    "determine": "establish",
    "diagnosis": "identification of disease",
    "diagnostic": "identifying",
    "disease": "illness",
    "diseases": "illnesses",
    "dose": "administered amount",
    "drug": "medicine",
    "drugs": "medicines",
    "eligibility": "qualification criteria",
    "eligible": "qualifying",
    "facility": "clinic site",
    "follow-up": "subsequent review",
    "gender": "sex identity",
    "health": "wellbeing",
    "hepatitis": "liver inflammation",
    "history": "past record",
    "hiv-positive": "reactive for hiv",
    "hts": "hiv testing services",
    "infant": "newborn",
    "infants": "newborns",
    "infection": "infectious condition",
    "information": "details",
    "key": "priority",
    "living": "residing",
    "load": "burden",
    "male": "man-identified",
    "men": "adult males",
    "monitor": "periodically reassess",
    "monitoring": "periodic reassessment",
    "months": "month intervals",
    "mother": "birthing parent",
    "negative": "non-reactive",
    "observable": "measurable entity",
    "offer": "propose",
    "one": "single",
    "partner": "sexual contact",
    "partners": "sexual contacts",
    "people": "individuals",
    "pep": "post-exposure prophylaxis",
    "period": "time span",
    "person": "individual",
    "population": "group of individuals",
    "populations": "groups",
    "positive": "reactive",
    "pregnancy": "gestation",
    "pregnant": "gestating",
    "prep": "pre-exposure prophylaxis",
    "prevention": "prophylaxis",
    "product": "commodity",
    "provide": "supply",
    "qualifier": "modifier",
    "reason": "justification",
    "recommended": "advised",
    "record": "log",
    "referral": "onward direction",
    "regimen": "drug schedule",
    "reporting": "notification",
    "reproductive": "fertility-related",
    "result": "finding",
    "results": "findings",
    "risk": "likelihood of harm",
    "screen": "early detection check",
    "screening": "early detection check",
    "select": "choose",
    "service": "programme offering",
    "services": "programme offerings",
    "sex": "biological category",
    "sexual": "intercourse-related",
    "should": "ought to",
    "snomed": "clinical terminology system",
    "specimen": "biological sample",
    "stage": "phase",
    "start": "commencement",
    "status": "current state",
    "sti": "sexually transmitted infection",
    "stis": "sexually transmitted infections",
    "symptoms": "presenting signs",
    "syphilis": "treponemal infection",
    "test": "assay",
    "tested": "assayed",
    "testing": "assay work",
    "tests": "assays",
    "threshold": "cut-off value",
    "treated": "managed with therapy",
    "treatment": "therapy",
    "tuberculosis": "tb disease",
    "type": "category",
    "update": "revise",
    "use": "utilisation",
    "using": "utilising",
    "value": "recorded measure",
    "viral": "virological",
    "virus": "pathogen",
    "visit": "clinical encounter",
    "women": "adult females",
    "years": "annual units",
}
_PARAPHRASE_STOPWORDS = frozenset(
    {
        "also",
        "and",
        "are",
        "been",
        "being",
        "broader",
        "classifiable",
        "codes",
        "data",
        "entity",
        "equivalent",
        "false",
        "for",
        "foundation",
        "from",
        "has",
        "have",
        "http",
        "icd",
        "icf",
        "ichi",
        "input",
        "int",
        "is",
        "loinc",
        "n/a",
        "na",
        "narrower",
        "none",
        "not",
        "option",
        "other",
        "related",
        "source",
        "target",
        "than",
        "that",
        "the",
        "this",
        "true",
        "used",
        "was",
        "were",
        "who",
        "with",
    }
)
_PARAPHRASE_IDENTIFIER = re.compile(
    r"^(?:[A-Za-z]{1,6}\.[\w.]+|[A-Z]{2,}\d[\w.-]*|\d[\w.-]*|[\w-]*\d{3,}[\w-]*)$"
)
_TERMINOLOGY_RE = re.compile(r"\b(?:[A-Z][A-Z0-9+.-]{1,}|[A-Z]\d{2,}|\d{2,}[A-Z]\w*)\b")
_TOKEN_RE = re.compile(r"[\w+.-]+", re.UNICODE)
_CONFLICT_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "also",
        "before",
        "being",
        "between",
        "could",
        "from",
        "have",
        "into",
        "more",
        "other",
        "should",
        "than",
        "that",
        "their",
        "there",
        "these",
        "this",
        "those",
        "through",
        "under",
        "using",
        "were",
        "when",
        "where",
        "which",
        "with",
        "would",
    }
)


class AutomatedBenchmarkGenerationPolicyContent(CanonicalModel):
    schema_version: Literal[SOURCE_DERIVED_CONTRACT_VERSION] = SOURCE_DERIVED_CONTRACT_VERSION
    policy_id: str = Field(min_length=1, max_length=100)
    revision: str = Field(min_length=1, max_length=100)
    generator_name: Literal[SOURCE_DERIVED_GENERATOR_NAME] = SOURCE_DERIVED_GENERATOR_NAME
    generator_version: Literal[SOURCE_DERIVED_GENERATOR_VERSION] = (
        SOURCE_DERIVED_GENERATOR_VERSION
    )
    derivation_rules_sha256: str = Field(default=DERIVATION_RULES_SHA256, pattern=SHA256_PATTERN)
    partition_seed_sha256: str = Field(pattern=SHA256_PATTERN)
    require_disjoint_partition_source_evidence: Literal[True] = True
    allow_source_derived_negative_cases: Literal[True] = True
    maximum_query_characters: int = Field(default=320, ge=80, le=2_000)
    established_at: datetime

    @model_validator(mode="after")
    def pin_rules(self) -> AutomatedBenchmarkGenerationPolicyContent:
        if self.derivation_rules_sha256 != DERIVATION_RULES_SHA256:
            raise ValueError(
                "generation policy does not pin this implementation's derivation rules"
            )
        return self


class AutomatedBenchmarkGenerationPolicy(CanonicalModel):
    content: AutomatedBenchmarkGenerationPolicyContent
    policy_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> AutomatedBenchmarkGenerationPolicy:
        if self.policy_sha256 != canonical_sha256(self.content):
            raise ValueError("automated generation-policy digest is inconsistent")
        return self

    @classmethod
    def seal(
        cls, content: AutomatedBenchmarkGenerationPolicyContent
    ) -> AutomatedBenchmarkGenerationPolicy:
        return cls(content=content, policy_sha256=canonical_sha256(content))


class AutomatedBenchmarkGenerationRequest(CanonicalModel):
    generation_id: str = Field(min_length=1, max_length=64)
    development_benchmark_id: str = Field(min_length=1, max_length=100)
    holdout_benchmark_id: str = Field(min_length=1, max_length=100)
    actor_identity: str = Field(min_length=1, max_length=300)
    generated_at: datetime
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

    @model_validator(mode="after")
    def validate_retrieval_configuration(self) -> AutomatedBenchmarkGenerationRequest:
        if self.development_benchmark_id == self.holdout_benchmark_id:
            raise ValueError("development and holdout benchmark IDs must differ")
        if self.candidate_mode not in self.modes:
            raise ValueError("candidate mode must be included in benchmark modes")
        if self.candidate_limit < self.top_k:
            raise ValueError("candidate limit cannot be smaller than top_k")
        if self.top_k >= MAX_PARAPHRASE_LEXICAL_RANK:
            raise ValueError(
                f"top_k {self.top_k} reaches the paraphrase lexical-rank band "
                f"(max {MAX_PARAPHRASE_LEXICAL_RANK}); every paraphrase case would sit "
                "inside the retrieval depth by construction and the stratum would stop "
                "stressing recall"
            )
        return self


class GeneratedBenchmarkCaseRecord(CanonicalModel):
    suite_partition: BenchmarkSuitePartition
    case: BenchmarkCase

    @model_validator(mode="after")
    def validate_partition(self) -> GeneratedBenchmarkCaseRecord:
        if self.suite_partition not in {
            BenchmarkSuitePartition.DEVELOPMENT,
            BenchmarkSuitePartition.SEALED_HOLDOUT,
        }:
            raise ValueError("generated cases require a development or holdout partition")
        if self.case.automated_provenance is None or self.case.adjudication is not None:
            raise ValueError("generated cases require only automated source-derived provenance")
        return self


class SafetyTopicGenerationCoverage(CanonicalModel):
    suite_partition: BenchmarkSuitePartition
    safety_topic: ClinicalSafetyTopic
    case_count: int = Field(gt=0)
    required_minimum: int = Field(gt=0)

    @model_validator(mode="after")
    def require_target(self) -> SafetyTopicGenerationCoverage:
        if self.case_count < self.required_minimum:
            raise ValueError("generated safety-topic coverage is below its required minimum")
        return self


class AutomatedBenchmarkGenerationRecordContent(CanonicalModel):
    schema_version: Literal[SOURCE_DERIVED_CONTRACT_VERSION] = SOURCE_DERIVED_CONTRACT_VERSION
    generation_id: str = Field(min_length=1, max_length=64)
    generation_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    access_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    threshold_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    corpus_release_id: str = Field(min_length=1, max_length=64)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    partition_assignment_sha256: str = Field(pattern=SHA256_PATTERN)
    cases: tuple[GeneratedBenchmarkCaseRecord, ...] = Field(min_length=2)
    coverage: tuple[SafetyTopicGenerationCoverage, ...] = Field(min_length=22)
    generated_by: str = Field(min_length=1, max_length=300)
    generated_at: datetime

    @field_validator("cases")
    @classmethod
    def sort_unique_cases(
        cls, value: tuple[GeneratedBenchmarkCaseRecord, ...]
    ) -> tuple[GeneratedBenchmarkCaseRecord, ...]:
        ids = [item.case.case_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("automated generation repeats a case ID")
        return tuple(sorted(value, key=lambda item: item.case.case_id))

    @field_validator("coverage")
    @classmethod
    def sort_unique_coverage(
        cls, value: tuple[SafetyTopicGenerationCoverage, ...]
    ) -> tuple[SafetyTopicGenerationCoverage, ...]:
        keys = [(item.suite_partition, item.safety_topic) for item in value]
        expected = {
            (partition, topic)
            for partition in (
                BenchmarkSuitePartition.DEVELOPMENT,
                BenchmarkSuitePartition.SEALED_HOLDOUT,
            )
            for topic in ClinicalSafetyTopic
        }
        if len(keys) != len(set(keys)) or set(keys) != expected:
            raise ValueError("generation coverage must account for every partition/topic pair")
        return tuple(
            sorted(
                value,
                key=lambda item: (item.suite_partition.value, item.safety_topic.value),
            )
        )

    @model_validator(mode="after")
    def validate_partition_boundary(self) -> AutomatedBenchmarkGenerationRecordContent:
        partitions = {item.suite_partition for item in self.cases}
        if partitions != {
            BenchmarkSuitePartition.DEVELOPMENT,
            BenchmarkSuitePartition.SEALED_HOLDOUT,
        }:
            raise ValueError("generation record must contain both sealed partitions")
        source_ids: dict[BenchmarkSuitePartition, set[str]] = defaultdict(set)
        for item in self.cases:
            provenance = item.case.automated_provenance
            if provenance is None:
                raise ValueError("generation record case lacks automated provenance")
            if provenance.partition_assignment_sha256 != self.partition_assignment_sha256:
                raise ValueError("case binds another partition assignment")
            source_ids[item.suite_partition].update(
                reference.evidence_id for reference in provenance.source_evidence
            )
        if source_ids[BenchmarkSuitePartition.DEVELOPMENT] & source_ids[
            BenchmarkSuitePartition.SEALED_HOLDOUT
        ]:
            raise ValueError("development and holdout source evidence must be disjoint")
        return self


class AutomatedBenchmarkGenerationRecord(CanonicalModel):
    content: AutomatedBenchmarkGenerationRecordContent
    generation_record_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> AutomatedBenchmarkGenerationRecord:
        if self.generation_record_sha256 != canonical_sha256(self.content):
            raise ValueError("automated generation-record digest is inconsistent")
        return self

    @classmethod
    def seal(
        cls, content: AutomatedBenchmarkGenerationRecordContent
    ) -> AutomatedBenchmarkGenerationRecord:
        return cls(content=content, generation_record_sha256=canonical_sha256(content))


class AutomatedBenchmarkGenerationResult(CanonicalModel):
    generation_record: AutomatedBenchmarkGenerationRecord
    development_suite: BenchmarkSuite
    sealed_holdout_suite: BenchmarkSuite


class SourceDerivedBenchmarkGenerationError(RuntimeError):
    pass


class _ReleaseLexicalIndex:
    """IDF-weighted lexical index used only to prove a paraphrase stays answerable."""

    def __init__(self, evidence: tuple[CorpusEvidenceRecord, ...]) -> None:
        self._postings: dict[str, list[str]] = defaultdict(list)
        self._tokens: dict[str, set[str]] = {}
        for record in evidence:
            tokens = SourceDerivedBenchmarkGenerator._paraphrase_tokens(
                record.content_search
            )
            self._tokens[record.evidence_id] = tokens
            for token in tokens:
                self._postings[token].append(record.evidence_id)
        self._document_count = len(evidence)

    def tokens_for(self, evidence_id: str) -> set[str]:
        return self._tokens.get(evidence_id, set())

    def lexical_rank(self, query_tokens: set[str], evidence_id: str) -> int | None:
        """Return one record's IDF-weighted lexical rank, or None when it does not match."""

        scores: dict[str, float] = defaultdict(float)
        for token in query_tokens:
            postings = self._postings.get(token)
            if not postings:
                continue
            weight = math.log(1 + self._document_count / len(postings))
            for candidate in postings:
                scores[candidate] += weight
        if evidence_id not in scores:
            return None
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return next(
            position
            for position, (candidate, _) in enumerate(ranked, start=1)
            if candidate == evidence_id
        )


class SourceDerivedBenchmarkGenerator:
    def generate(
        self,
        bundle: CorpusReleaseBundle,
        *,
        access_policy: BenchmarkAccessPolicy,
        threshold_policy: BenchmarkThresholdPolicy,
        generation_policy: AutomatedBenchmarkGenerationPolicy,
        request: AutomatedBenchmarkGenerationRequest,
        partition_seed: bytes,
    ) -> AutomatedBenchmarkGenerationResult:
        policy = generation_policy.content
        if hashlib.sha256(partition_seed).hexdigest() != policy.partition_seed_sha256:
            raise SourceDerivedBenchmarkGenerationError("partition seed does not match policy")
        if not access_policy.content.permits(
            request.actor_identity, BenchmarkSuitePartition.DEVELOPMENT
        ) or not access_policy.content.permits(
            request.actor_identity, BenchmarkSuitePartition.SEALED_HOLDOUT
        ):
            raise SourceDerivedBenchmarkGenerationError(
                "source-derived generation requires an authorized holdout custodian/"
                "development builder"
            )
        if request.generated_at < max(
            access_policy.content.effective_at,
            threshold_policy.content.established_at,
            policy.established_at,
        ):
            raise SourceDerivedBenchmarkGenerationError("generation predates one of its policies")
        if blockers := bundle.activation_blockers():
            raise SourceDerivedBenchmarkGenerationError(
                "corpus release is not canonical and activatable: " + ", ".join(blockers)
            )
        evidence = tuple(sorted(bundle.evidence, key=lambda item: item.evidence_id))
        if len(evidence) < 2:
            raise SourceDerivedBenchmarkGenerationError(
                "source-derived generation requires at least two canonical evidence records"
            )
        partitioned = self._partition_evidence(
            evidence, bundle.manifest.manifest_sha256, partition_seed
        )
        lexical_index = _ReleaseLexicalIndex(evidence)
        seeds: list[
            tuple[
                BenchmarkSuitePartition,
                ClinicalSafetyTopic,
                str,
                tuple[CorpusEvidenceRecord, ...],
            ]
        ] = []
        for partition in (
            BenchmarkSuitePartition.DEVELOPMENT,
            BenchmarkSuitePartition.SEALED_HOLDOUT,
        ):
            records = partitioned[partition]
            for target in threshold_policy.content.safety_topic_sample_targets:
                required = (
                    target.development_minimum
                    if partition is BenchmarkSuitePartition.DEVELOPMENT
                    else target.sealed_holdout_minimum
                )
                candidates = self._candidates_for_topic(
                    target.safety_topic, records, lexical_index
                )
                ranked = sorted(
                    candidates,
                    key=lambda item: self._rank(
                        partition_seed,
                        f"{partition.value}:{target.safety_topic.value}:"
                        + ":".join(record.evidence_id for record in item[1]),
                    ),
                )
                if len(ranked) < required:
                    raise SourceDerivedBenchmarkGenerationError(
                        f"{partition.value} can derive only {len(ranked)}/{required} "
                        f"{target.safety_topic.value} cases"
                    )
                seeds.extend(
                    (partition, target.safety_topic, rule_id, source_records)
                    for rule_id, source_records in ranked[:required]
                )

        assignment_sha256 = canonical_sha256(
            {
                "assignments": [
                    {
                        "partition": partition.value,
                        "safety_topic": topic.value,
                        "rule_id": rule_id,
                        "source_evidence_ids": [item.evidence_id for item in source_records],
                    }
                    for partition, topic, rule_id, source_records in sorted(
                        seeds,
                        key=lambda item: (
                            item[0].value,
                            item[1].value,
                            item[2],
                            tuple(record.evidence_id for record in item[3]),
                        ),
                    )
                ]
            }
        )
        generated = tuple(
            GeneratedBenchmarkCaseRecord(
                suite_partition=partition,
                case=self._case(
                    bundle,
                    partition=partition,
                    topic=topic,
                    rule_id=rule_id,
                    source_records=source_records,
                    assignment_sha256=assignment_sha256,
                    maximum_query_characters=policy.maximum_query_characters,
                    lexical_index=lexical_index,
                ),
            )
            for partition, topic, rule_id, source_records in seeds
        )
        topic_counts = Counter(
            (item.suite_partition, topic)
            for item in generated
            for topic in item.case.safety_topics
        )
        targets = {
            item.safety_topic: item for item in threshold_policy.content.safety_topic_sample_targets
        }
        coverage = tuple(
            SafetyTopicGenerationCoverage(
                suite_partition=partition,
                safety_topic=topic,
                case_count=topic_counts[(partition, topic)],
                required_minimum=(
                    targets[topic].development_minimum
                    if partition is BenchmarkSuitePartition.DEVELOPMENT
                    else targets[topic].sealed_holdout_minimum
                ),
            )
            for partition in (
                BenchmarkSuitePartition.DEVELOPMENT,
                BenchmarkSuitePartition.SEALED_HOLDOUT,
            )
            for topic in ClinicalSafetyTopic
        )
        record = AutomatedBenchmarkGenerationRecord.seal(
            AutomatedBenchmarkGenerationRecordContent(
                generation_id=request.generation_id,
                generation_policy_sha256=generation_policy.policy_sha256,
                access_policy_sha256=access_policy.policy_sha256,
                threshold_policy_sha256=threshold_policy.policy_sha256,
                corpus_release_id=bundle.manifest.content.corpus_release_id,
                manifest_sha256=bundle.manifest.manifest_sha256,
                partition_assignment_sha256=assignment_sha256,
                cases=generated,
                coverage=coverage,
                generated_by=request.actor_identity,
                generated_at=request.generated_at,
            )
        )
        development = self._suite(
            record,
            threshold_policy,
            request,
            partition=BenchmarkSuitePartition.DEVELOPMENT,
        )
        holdout = self._suite(
            record,
            threshold_policy,
            request,
            partition=BenchmarkSuitePartition.SEALED_HOLDOUT,
        )
        return AutomatedBenchmarkGenerationResult(
            generation_record=record,
            development_suite=development,
            sealed_holdout_suite=holdout,
        )

    @staticmethod
    def _partition_evidence(
        evidence: tuple[CorpusEvidenceRecord, ...], manifest_sha256: str, seed: bytes
    ) -> dict[BenchmarkSuitePartition, tuple[CorpusEvidenceRecord, ...]]:
        ranked = sorted(
            evidence,
            key=lambda item: SourceDerivedBenchmarkGenerator._rank(
                seed, f"{manifest_sha256}:{item.evidence_id}"
            ),
        )
        midpoint = len(ranked) // 2
        return {
            BenchmarkSuitePartition.DEVELOPMENT: tuple(ranked[:midpoint]),
            BenchmarkSuitePartition.SEALED_HOLDOUT: tuple(ranked[midpoint:]),
        }

    @staticmethod
    def _rank(seed: bytes, value: str) -> bytes:
        return hmac.new(seed, value.encode("utf-8"), hashlib.sha256).digest()

    def _candidates_for_topic(
        self,
        topic: ClinicalSafetyTopic,
        records: tuple[CorpusEvidenceRecord, ...],
        lexical_index: _ReleaseLexicalIndex,
    ) -> list[tuple[str, tuple[CorpusEvidenceRecord, ...]]]:
        if topic is ClinicalSafetyTopic.CONFLICTING_EVIDENCE:
            return self._conflict_pairs(records)
        result: list[tuple[str, tuple[CorpusEvidenceRecord, ...]]] = []
        languages = {item.language for item in records}
        for record in records:
            text = record.content_search
            roles = set(record.evidence_roles)
            rule_id: str | None = None
            if topic is ClinicalSafetyTopic.CONTRAINDICATION and (
                EvidenceRole.EXCEPTION_OR_CONTRAINDICATION in roles
                or _NEGATION_RE.search(text)
            ):
                rule_id = "contraindication-source-language"
            elif topic is ClinicalSafetyTopic.APPLICABILITY and (
                EvidenceRole.APPLICABILITY in roles
                or any(
                    (
                        record.applicability.population,
                        record.applicability.care_settings,
                        record.applicability.inclusion_criteria,
                        record.applicability.exclusion_criteria,
                    )
                )
                or _APPLICABILITY_RE.search(text)
            ):
                rule_id = "applicability-source-language"
            elif topic is ClinicalSafetyTopic.DOSE and (
                EvidenceRole.DOSE_OR_THRESHOLD in roles or _DOSE_RE.search(text)
            ):
                rule_id = "dose-threshold-source-language"
            elif topic is ClinicalSafetyTopic.MONITORING and (
                EvidenceRole.MONITORING in roles or _MONITORING_RE.search(text)
            ):
                rule_id = "monitoring-source-language"
            elif topic is ClinicalSafetyTopic.TERMINOLOGY and _TERMINOLOGY_RE.search(text):
                rule_id = "terminology-source-code"
            elif topic is ClinicalSafetyTopic.NEGATION and _NEGATION_RE.search(text):
                rule_id = "explicit-negation-source-language"
            elif topic is ClinicalSafetyTopic.PARAPHRASED_INTENT and self._paraphrase(
                record, lexical_index
            ):
                rule_id = "controlled-vocabulary-paraphrase"
            elif topic is ClinicalSafetyTopic.STALE_SOURCE:
                rule_id = "excluded-lifecycle-negative"
            elif topic is ClinicalSafetyTopic.WRONG_JURISDICTION:
                rule_id = "absent-jurisdiction-negative"
            elif topic is ClinicalSafetyTopic.INSUFFICIENT_EVIDENCE:
                rule_id = "absent-publisher-negative"
            elif topic is ClinicalSafetyTopic.MULTILINGUAL_CROSS_LINGUAL:
                rule_id = (
                    "cross-lingual-shared-identifier"
                    if len(languages) > 1 and _TERMINOLOGY_RE.search(text)
                    else "unsupported-language-negative"
                )
            if rule_id is not None:
                result.append((rule_id, (record,)))
        return result

    def _conflict_pairs(
        self, records: tuple[CorpusEvidenceRecord, ...]
    ) -> list[tuple[str, tuple[CorpusEvidenceRecord, ...]]]:
        positive = [item for item in records if not _NEGATION_RE.search(item.content_search)]
        negative = [item for item in records if _NEGATION_RE.search(item.content_search)]
        positive_tokens: dict[str, list[CorpusEvidenceRecord]] = defaultdict(list)
        for item in positive:
            for token in self._tokens(item.content_search):
                positive_tokens[token].append(item)
        pairs: dict[tuple[str, str], tuple[CorpusEvidenceRecord, CorpusEvidenceRecord]] = {}
        for negative_item in negative:
            candidates: dict[str, CorpusEvidenceRecord] = {}
            negative_tokens = self._tokens(negative_item.content_search)
            useful_tokens = sorted(
                (
                    token
                    for token in negative_tokens
                    if positive_tokens.get(token)
                ),
                key=lambda token: (len(positive_tokens[token]), token),
            )[:4]
            for token in useful_tokens:
                for item in sorted(
                    positive_tokens[token], key=lambda candidate: candidate.evidence_id
                )[:128]:
                    candidates[item.evidence_id] = item
            if not candidates:
                continue
            best = max(
                candidates.values(),
                key=lambda item: (
                    len(negative_tokens & self._tokens(item.content_search)),
                    item.evidence_id,
                ),
            )
            if len(negative_tokens & self._tokens(best.content_search)) < 2:
                continue
            pair = tuple(sorted((negative_item, best), key=lambda item: item.evidence_id))
            pairs[(pair[0].evidence_id, pair[1].evidence_id)] = pair
        if pairs:
            return [("polarity-aware-conflict-pair", item) for item in pairs.values()]

        return []

    def _case(
        self,
        bundle: CorpusReleaseBundle,
        *,
        partition: BenchmarkSuitePartition,
        topic: ClinicalSafetyTopic,
        rule_id: str,
        source_records: tuple[CorpusEvidenceRecord, ...],
        assignment_sha256: str,
        maximum_query_characters: int,
        lexical_index: _ReleaseLexicalIndex,
    ) -> BenchmarkCase:
        negative = rule_id in {
            "excluded-lifecycle-negative",
            "absent-jurisdiction-negative",
            "absent-publisher-negative",
            "unsupported-language-negative",
        }
        evidence_ids = tuple(item.evidence_id for item in source_records)
        case_id = "ABQ_" + hashlib.sha256(
            (
                f"{SOURCE_DERIVED_GENERATOR_VERSION}:{bundle.manifest.manifest_sha256}:"
                f"{partition.value}:{topic.value}:{rule_id}:{':'.join(evidence_ids)}"
            ).encode()
        ).hexdigest()[:32]
        if rule_id == "controlled-vocabulary-paraphrase":
            paraphrased = [self._paraphrase(item, lexical_index) for item in source_records]
            if any(item is None for item in paraphrased):
                raise SourceDerivedBenchmarkGenerationError(
                    "a paraphrase case lost its bounded controlled-vocabulary restatement"
                )
            fragments = [
                self._query_fragment(item, maximum_query_characters)
                for item in paraphrased
                if item is not None
            ]
        else:
            fragments = [
                self._query_fragment(item.content_search, maximum_query_characters)
                for item in source_records
            ]
        question = self._question(topic, rule_id, fragments, source_records)
        filters = BenchmarkFilter()
        if rule_id == "excluded-lifecycle-negative":
            filters = BenchmarkFilter(
                lifecycle_statuses=("ARCHIVED", "SUPERSEDED", "WITHDRAWN")
            )
        elif rule_id == "absent-jurisdiction-negative":
            filters = BenchmarkFilter(jurisdictions=("ZZ-AUTOMATED-OUT-OF-SCOPE",))
        elif rule_id == "absent-publisher-negative":
            filters = BenchmarkFilter(publisher_ids=("PUB_AUTOMATED_ABSENT",))
        elif rule_id == "unsupported-language-negative":
            filters = BenchmarkFilter(languages=("zz",))
        else:
            filters = BenchmarkFilter(
                jurisdictions=tuple({item.jurisdiction for item in source_records}),
                languages=tuple({item.language for item in source_records}),
                publisher_ids=tuple({item.publisher_id for item in source_records}),
            )
        safety_topics = (
            tuple(sorted({topic, ClinicalSafetyTopic.INSUFFICIENT_EVIDENCE}, key=lambda x: x.value))
            if negative
            else (topic,)
        )
        gold = (
            ()
            if negative
            else tuple(
                BenchmarkGoldEvidence(evidence_id=item.evidence_id, relevance_grade=3)
                for item in source_records
            )
        )
        complete_sets = (
            ()
            if negative
            else (
                MinimumCompleteEvidenceSet(
                    set_id=f"MCES_{case_id[4:]}", evidence_ids=evidence_ids
                ),
            )
        )
        roles = (
            ()
            if negative
            else tuple(
                sorted(
                    {role for item in source_records for role in item.evidence_roles},
                    key=lambda item: item.value,
                )
            )
        )
        return BenchmarkCase(
            case_id=case_id,
            question=question,
            evidence_expectation=(
                EvidenceExpectation.INSUFFICIENT_EVIDENCE
                if negative
                else EvidenceExpectation.ANSWERABLE
            ),
            gold_evidence=gold,
            minimum_complete_evidence_sets=complete_sets,
            required_evidence_roles=roles,
            retrieval_filter=filters,
            forbidden_evidence_ids=evidence_ids if negative else (),
            strata={
                "clinical_risk": "engineering-safety",
                "derivation_rule": rule_id,
                "source_language": "+".join(sorted({item.language for item in source_records})),
            },
            safety_topics=safety_topics,
            automated_provenance=AutomatedSourceDerivedProvenance(
                generator_name=SOURCE_DERIVED_GENERATOR_NAME,
                generator_version=SOURCE_DERIVED_GENERATOR_VERSION,
                derivation_rule_id=rule_id,
                derivation_rules_sha256=DERIVATION_RULES_SHA256,
                source_evidence=tuple(
                    AutomatedSourceEvidenceReference(
                        evidence_id=item.evidence_id,
                        evidence_sha256=item.sha256,
                    )
                    for item in source_records
                ),
                partition_assignment_sha256=assignment_sha256,
            ),
        )

    @staticmethod
    def _paraphrase_tokens(text: str) -> set[str]:
        return {
            token.casefold()
            for token in _TOKEN_RE.findall(text)
            if len(token) >= 3
        }

    @staticmethod
    def _paraphrase(
        record: CorpusEvidenceRecord,
        lexical_index: _ReleaseLexicalIndex,
    ) -> str | None:
        """Restate a passage with controlled synonyms, or None if the bounds fail.

        Source identifiers and terminology-table boilerplate are dropped, mapped
        clinical terms are substituted, and every remaining content word is kept so the
        case stays identifiable. The result is accepted only when it shares at most
        ``MAX_PARAPHRASE_LEXICAL_OVERLAP`` of its terms with the passage and the passage's
        lexical rank sits inside the configured band: close enough to be reachable, but
        never already first, so the case cannot be won by lexical matching alone.
        """

        substituted = 0
        retained = 0
        terms: list[str] = []
        for word in _TOKEN_RE.findall(record.content_search):
            lowered = word.casefold()
            if len(word) < 3 or lowered in _PARAPHRASE_STOPWORDS:
                continue
            if _PARAPHRASE_IDENTIFIER.match(word):
                continue
            replacement = _PARAPHRASE_VOCABULARY.get(lowered)
            if replacement is None:
                terms.append(word)
                retained += 1
            else:
                terms.append(replacement)
                substituted += 1
        seen: set[str] = set()
        ordered: list[str] = []
        for term in " ".join(terms).split():
            key = term.casefold()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(term)
        if (
            substituted < MIN_PARAPHRASE_SUBSTITUTIONS
            or retained < MIN_PARAPHRASE_RETAINED_TERMS
        ):
            return None
        question = " ".join(ordered[:MAX_PARAPHRASE_TERMS])
        query_tokens = SourceDerivedBenchmarkGenerator._paraphrase_tokens(question)
        if len(query_tokens) < MIN_PARAPHRASE_TERMS:
            return None
        source_tokens = lexical_index.tokens_for(record.evidence_id)
        overlap = len(query_tokens & source_tokens) / len(query_tokens)
        if overlap > MAX_PARAPHRASE_LEXICAL_OVERLAP:
            return None
        rank = lexical_index.lexical_rank(query_tokens, record.evidence_id)
        if (
            rank is None
            or rank < MIN_PARAPHRASE_LEXICAL_RANK
            or rank > MAX_PARAPHRASE_LEXICAL_RANK
        ):
            return None
        return question

    @staticmethod
    def _query_fragment(text: str, maximum: int) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= maximum:
            return normalized
        shortened = normalized[:maximum].rsplit(" ", 1)[0]
        return shortened or normalized[:maximum]

    @staticmethod
    def _question(
        topic: ClinicalSafetyTopic,
        rule_id: str,
        fragments: list[str],
        records: tuple[CorpusEvidenceRecord, ...],
    ) -> str:
        joined = " | ".join(fragments)
        if rule_id == "excluded-lifecycle-negative":
            return f"Retrieve a stale, superseded, withdrawn, or archived source for: {joined}"
        if rule_id == "absent-jurisdiction-negative":
            return (
                "Retrieve jurisdiction-specific source evidence outside this release for: "
                f"{joined}"
            )
        if rule_id == "absent-publisher-negative":
            return f"Retrieve publisher evidence absent from this release for: {joined}"
        if rule_id == "unsupported-language-negative":
            return f"Retrieve an unsupported-language source passage for: {joined}"
        if topic is ClinicalSafetyTopic.CONFLICTING_EVIDENCE:
            return f"Retrieve both source passages needed to inspect a potential conflict: {joined}"
        if topic is ClinicalSafetyTopic.PARAPHRASED_INTENT:
            return f"Retrieve the source passage that describes: {joined}"
        if topic is ClinicalSafetyTopic.TERMINOLOGY:
            terms = [match.group(0) for match in _TERMINOLOGY_RE.finditer(joined)]
            selected = " ".join(dict.fromkeys(terms[:6])) or joined
            return f"Retrieve the source definition or mapping for these exact terms: {selected}"
        if topic is ClinicalSafetyTopic.MULTILINGUAL_CROSS_LINGUAL:
            languages = ", ".join(sorted({item.language for item in records}))
            return f"In English, retrieve the {languages} source passage identified by: {joined}"
        prompts = {
            ClinicalSafetyTopic.CONTRAINDICATION: "restriction or contraindication",
            ClinicalSafetyTopic.APPLICABILITY: "population applicability",
            ClinicalSafetyTopic.DOSE: "dose or threshold",
            ClinicalSafetyTopic.MONITORING: "monitoring requirement",
            ClinicalSafetyTopic.NEGATION: "explicit negative statement",
        }
        return f"Retrieve the source {prompts.get(topic, 'evidence')} stating: {joined}"

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {
            token.casefold()
            for token in _TOKEN_RE.findall(text)
            if len(token) >= 4
            and not token.isdigit()
            and token.casefold() not in _CONFLICT_STOPWORDS
        }

    @staticmethod
    def _suite(
        record: AutomatedBenchmarkGenerationRecord,
        threshold: BenchmarkThresholdPolicy,
        request: AutomatedBenchmarkGenerationRequest,
        *,
        partition: BenchmarkSuitePartition,
    ) -> BenchmarkSuite:
        cases = tuple(
            item.case for item in record.content.cases if item.suite_partition is partition
        )
        minimum = threshold.content.minimum_case_count_for(partition)
        if len(cases) < minimum:
            raise SourceDerivedBenchmarkGenerationError(
                f"{partition.value} generated {len(cases)} cases; policy requires {minimum}"
            )
        benchmark_id = (
            request.development_benchmark_id
            if partition is BenchmarkSuitePartition.DEVELOPMENT
            else request.holdout_benchmark_id
        )
        return BenchmarkSuite.seal(
            BenchmarkSuiteContent(
                benchmark_id=benchmark_id,
                suite_partition=partition,
                provenance_mode=BenchmarkProvenanceMode.AUTOMATED_SOURCE_DERIVED,
                access_policy_sha256=record.content.access_policy_sha256,
                generation_policy_sha256=record.content.generation_policy_sha256,
                generation_record_sha256=record.generation_record_sha256,
                threshold_policy_sha256=record.content.threshold_policy_sha256,
                required_safety_topics=tuple(ClinicalSafetyTopic),
                corpus_release_id=record.content.corpus_release_id,
                manifest_sha256=record.content.manifest_sha256,
                cases=cases,
                modes=request.modes,
                candidate_mode=request.candidate_mode,
                top_k=request.top_k,
                acceptance=threshold.content.acceptance_for(partition),
            )
        )
