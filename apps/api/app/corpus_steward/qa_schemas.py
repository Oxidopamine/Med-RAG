"""Phase 4 contracts for evidence QA and canonical corpus promotion."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.corpus_steward.schemas import (
    STEWARD_CONTRACT_VERSION,
    VerifiedAttestationReference,
)
from app.schemas.corpus import (
    SHA256_PATTERN,
    ApplicabilityScope,
    CorpusReleaseBundle,
    EvidenceRole,
    RecommendationGrade,
    VerificationInvariant,
    VerificationOutcome,
    canonical_sha256,
)
from app.schemas.domain import CanonicalModel

QA_WORKFLOW_NAME = "evidence-qa-and-corpus-promotion"
QA_WORKFLOW_VERSION = "1.0.0"


class QARunState(str, Enum):
    PREPARED = "PREPARED"
    VALIDATED = "VALIDATED"


class QADisposition(str, Enum):
    APPROVE = "APPROVE"
    QUARANTINE = "QUARANTINE"


class QAQuarantineReason(str, Enum):
    ANCHOR_REPLAY_FAILED = "ANCHOR_REPLAY_FAILED"
    EXTRACTION_STRUCTURE_FAILED = "EXTRACTION_STRUCTURE_FAILED"
    HEADER_OR_FOOTER = "HEADER_OR_FOOTER"
    DUPLICATE = "DUPLICATE"
    NON_EVIDENCE = "NON_EVIDENCE"
    CLINICAL_CLASSIFICATION_UNRESOLVED = "CLINICAL_CLASSIFICATION_UNRESOLVED"
    OTHER = "OTHER"


class ReplayCheck(CanonicalModel):
    invariant: VerificationInvariant
    outcome: VerificationOutcome
    details: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def failure_requires_details(self) -> ReplayCheck:
        if self.outcome is not VerificationOutcome.PASS and not self.details:
            raise ValueError("a failed or unresolved replay check requires details")
        return self


class AnchorReplayResult(CanonicalModel):
    evidence_id: str = Field(min_length=1, max_length=64)
    materialized_evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    source_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    replayed_evidence_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    checks: tuple[ReplayCheck, ...]
    passed: bool
    replayed_at: datetime

    @field_validator("checks")
    @classmethod
    def sort_unique_checks(cls, value: tuple[ReplayCheck, ...]) -> tuple[ReplayCheck, ...]:
        invariants = [item.invariant for item in value]
        if len(invariants) != len(set(invariants)):
            raise ValueError("anchor replay invariants must be unique")
        return tuple(sorted(value, key=lambda item: item.invariant.value))

    @model_validator(mode="after")
    def verify_passed(self) -> AnchorReplayResult:
        expected = bool(self.checks) and all(
            check.outcome is VerificationOutcome.PASS for check in self.checks
        )
        if self.passed != expected:
            raise ValueError("anchor replay pass flag is inconsistent")
        return self

    @property
    def sha256(self) -> str:
        return canonical_sha256(self)


class EvidenceArtifactReference(CanonicalModel):
    evidence_id: str = Field(min_length=1, max_length=64)
    materialized_evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    source_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    asset_id: str = Field(min_length=1, max_length=200)
    source_unit_id: str = Field(min_length=1, max_length=500)


class EvidenceArtifactManifestContent(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    workflow_name: Literal[QA_WORKFLOW_NAME] = QA_WORKFLOW_NAME
    workflow_version: Literal[QA_WORKFLOW_VERSION] = QA_WORKFLOW_VERSION
    qa_run_id: str = Field(min_length=1, max_length=64)
    corpus_release_candidate_id: str = Field(min_length=1, max_length=64)
    corpus_release_candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    materialization_run_id: str = Field(min_length=1, max_length=64)
    entries: tuple[EvidenceArtifactReference, ...] = Field(min_length=1)
    created_at: datetime

    @field_validator("entries")
    @classmethod
    def sort_unique_entries(
        cls, value: tuple[EvidenceArtifactReference, ...]
    ) -> tuple[EvidenceArtifactReference, ...]:
        ids = [item.evidence_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence artifact manifest IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.evidence_id))


class SignedEvidenceArtifactManifest(CanonicalModel):
    content: EvidenceArtifactManifestContent
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    attestation: VerifiedAttestationReference

    @model_validator(mode="after")
    def verify_digest(self) -> SignedEvidenceArtifactManifest:
        expected = canonical_sha256(self.content)
        if self.manifest_sha256 != expected:
            raise ValueError("evidence artifact manifest digest is inconsistent")
        if self.attestation.statement_sha256 != expected:
            raise ValueError("manifest attestation covers different content")
        return self


class EvidenceQADecision(CanonicalModel):
    evidence_id: str = Field(min_length=1, max_length=64)
    materialized_evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    disposition: QADisposition
    evidence_roles: tuple[EvidenceRole, ...] = ()
    applicability: ApplicabilityScope = Field(default_factory=ApplicabilityScope)
    recommendation_grade: RecommendationGrade | None = None
    quarantine_reasons: tuple[QAQuarantineReason, ...] = ()
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("evidence_roles", "quarantine_reasons")
    @classmethod
    def sort_unique_enums(cls, value: tuple[Enum, ...]) -> tuple[Enum, ...]:
        if len(value) != len(set(value)):
            raise ValueError("QA decision classifications must be unique")
        return tuple(sorted(value, key=lambda item: item.value))

    @model_validator(mode="after")
    def verify_disposition(self) -> EvidenceQADecision:
        if self.disposition is QADisposition.APPROVE:
            if not self.evidence_roles:
                raise ValueError("approved evidence requires at least one clinical role")
            if self.quarantine_reasons:
                raise ValueError("approved evidence cannot have quarantine reasons")
        else:
            if not self.quarantine_reasons:
                raise ValueError("quarantined evidence requires at least one reason")
            if self.evidence_roles or self.recommendation_grade is not None:
                raise ValueError("quarantined evidence cannot be clinically promoted")
        return self

    @property
    def sha256(self) -> str:
        return canonical_sha256(self)


class QADecisionBatchInput(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    corpus_release_candidate_id: str = Field(min_length=1, max_length=64)
    decision_authority: str = Field(min_length=1, max_length=300)
    decided_at: datetime
    decisions: tuple[EvidenceQADecision, ...] = Field(min_length=1)

    @field_validator("decisions")
    @classmethod
    def sort_unique_decisions(
        cls, value: tuple[EvidenceQADecision, ...]
    ) -> tuple[EvidenceQADecision, ...]:
        ids = [item.evidence_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("a QA batch cannot decide an evidence record twice")
        return tuple(sorted(value, key=lambda item: item.evidence_id))


class QADecisionBatchContent(QADecisionBatchInput):
    qa_run_id: str = Field(min_length=1, max_length=64)
    evidence_artifact_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    replay_set_sha256: str = Field(pattern=SHA256_PATTERN)


class SignedQADecisionBatch(CanonicalModel):
    content: QADecisionBatchContent
    batch_sha256: str = Field(pattern=SHA256_PATTERN)
    attestation: VerifiedAttestationReference

    @model_validator(mode="after")
    def verify_digest(self) -> SignedQADecisionBatch:
        expected = canonical_sha256(self.content)
        if self.batch_sha256 != expected:
            raise ValueError("QA decision batch digest is inconsistent")
        if self.attestation.statement_sha256 != expected:
            raise ValueError("QA decision attestation covers different content")
        return self


class ReleasePolicy(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    policy_id: str = Field(default="clinical-evidence-release-v1", min_length=1)
    require_complete_inventory: Literal[True] = True
    require_decision_for_every_record: Literal[True] = True
    require_successful_anchor_replay_for_approval: Literal[True] = True
    quarantine_headers_and_non_evidence: Literal[True] = True
    prohibit_duplicate_approved_content: Literal[True] = True
    prohibit_quarantined_content_in_release: Literal[True] = True

    @property
    def sha256(self) -> str:
        return canonical_sha256(self)


class InventoryReconciliationAttestationContent(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    corpus_release_id: str = Field(min_length=1, max_length=64)
    reconciliation_candidate_id: str = Field(min_length=1, max_length=64)
    reconciliation_candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    inventory_snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    upstream_reconcile_attestation_id: str = Field(min_length=1, max_length=64)
    complete: Literal[True] = True
    attested_at: datetime


class EvidenceVerificationAttestationContent(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    corpus_release_id: str = Field(min_length=1, max_length=64)
    qa_run_id: str = Field(min_length=1, max_length=64)
    evidence_artifact_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    replay_set_sha256: str = Field(pattern=SHA256_PATTERN)
    decision_batch_sha256: str = Field(pattern=SHA256_PATTERN)
    canonical_evidence_set_sha256: str = Field(pattern=SHA256_PATTERN)
    materialized_count: int = Field(gt=0)
    approved_count: int = Field(gt=0)
    quarantined_count: int = Field(ge=0)
    attested_at: datetime

    @model_validator(mode="after")
    def verify_accounting(self) -> EvidenceVerificationAttestationContent:
        if self.approved_count + self.quarantined_count != self.materialized_count:
            raise ValueError("evidence verification counts do not reconcile")
        return self


class ReleasePolicyAttestationContent(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    corpus_release_id: str = Field(min_length=1, max_length=64)
    release_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    inventory_attestation_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_verification_attestation_sha256: str = Field(pattern=SHA256_PATTERN)
    qdrant_collection: str = Field(min_length=1, max_length=255)
    release_contract_validated: Literal[True] = True
    retrieval_index_status: Literal["NOT_BUILT"] = "NOT_BUILT"
    attested_at: datetime


class QAClassificationInputItem(CanonicalModel):
    evidence_id: str
    materialized_evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    asset_id: str
    source_unit_id: str
    replay_passed: bool


class QAClassificationInput(CanonicalModel):
    qa_run_id: str
    corpus_release_candidate_id: str
    evidence_artifact_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_count: int = Field(gt=0)
    replay_passed_count: int = Field(ge=0)
    replay_failed_count: int = Field(ge=0)
    items: tuple[QAClassificationInputItem, ...]


class QAResult(CanonicalModel):
    state: QARunState
    qa_run_id: str
    corpus_release_candidate_id: str
    evidence_artifact_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_manifest_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_count: int = Field(gt=0)
    replay_passed_count: int = Field(ge=0)
    replay_failed_count: int = Field(ge=0)
    approved_count: int = Field(ge=0)
    quarantined_count: int = Field(ge=0)
    decision_batch_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    corpus_release_bundle: CorpusReleaseBundle | None = None
    bundle_artifact_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_state(self) -> QAResult:
        decided = self.approved_count + self.quarantined_count
        if self.state is QARunState.PREPARED:
            if decided or self.decision_batch_sha256 or self.corpus_release_bundle:
                raise ValueError("a prepared QA result cannot contain decisions or a release")
        else:
            if decided != self.evidence_count:
                raise ValueError("validated QA result must decide every materialized record")
            if not all(
                (
                    self.decision_batch_sha256,
                    self.corpus_release_bundle,
                    self.bundle_artifact_sha256,
                )
            ):
                raise ValueError("validated QA result requires a registered release bundle")
        return self
