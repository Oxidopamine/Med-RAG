"""Versioned contracts shared by corpus construction and serving.

These models deliberately describe immutable release content. Mutable workflow state
belongs in PostgreSQL and is not part of the signed manifest digest.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.domain import CanonicalModel, SourceStatus

CORPUS_CONTRACT_VERSION = "1.0.0"
ACTIVATION_CONTRACT_VERSION = "2.0.0"
SHA256_PATTERN = r"^[a-f0-9]{64}$"


def canonical_json_bytes(value: CanonicalModel | dict[str, Any]) -> bytes:
    """Serialize canonical content for stable hashing and signing."""

    payload = value.model_dump(mode="json") if isinstance(value, CanonicalModel) else value
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: CanonicalModel | dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class ReleaseState(str, Enum):
    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"


class EvidenceRole(str, Enum):
    PRIMARY_SUPPORT = "PRIMARY_SUPPORT"
    APPLICABILITY = "APPLICABILITY"
    EXCEPTION_OR_CONTRAINDICATION = "EXCEPTION_OR_CONTRAINDICATION"
    DOSE_OR_THRESHOLD = "DOSE_OR_THRESHOLD"
    MONITORING = "MONITORING"
    RATIONALE = "RATIONALE"


class EvidenceApprovalStatus(str, Enum):
    APPROVED = "APPROVED"
    QUARANTINED = "QUARANTINED"


class LocatorKind(str, Enum):
    TEXT_SPAN = "TEXT_SPAN"
    PDF = "PDF"
    HTML = "HTML"
    XML = "XML"
    FHIR = "FHIR"
    TABLE_CELL = "TABLE_CELL"


class VerificationInvariant(str, Enum):
    PROVENANCE = "PROVENANCE"
    EXACT_CONTENT = "EXACT_CONTENT"
    CRITICAL_FIELDS = "CRITICAL_FIELDS"
    READING_ORDER = "READING_ORDER"
    TABLE_STRUCTURE = "TABLE_STRUCTURE"
    NARRATIVE_CONSISTENCY = "NARRATIVE_CONSISTENCY"


class VerificationOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNRESOLVED = "UNRESOLVED"


class ExceptionReason(str, Enum):
    ACCESS = "ACCESS"
    LICENSING = "LICENSING"
    ACQUISITION = "ACQUISITION"
    VALIDATION = "VALIDATION"


class AttestationStage(str, Enum):
    INVENTORY_RECONCILIATION = "INVENTORY_RECONCILIATION"
    EVIDENCE_VERIFICATION = "EVIDENCE_VERIFICATION"
    RELEASE_POLICY = "RELEASE_POLICY"


REQUIRED_RELEASE_ATTESTATIONS = frozenset(
    {
        AttestationStage.INVENTORY_RECONCILIATION,
        AttestationStage.EVIDENCE_VERIFICATION,
        AttestationStage.RELEASE_POLICY,
    }
)


class SourceAnchor(CanonicalModel):
    """Generic source anchor with optional format-specific coordinates."""

    kind: LocatorKind
    source_uri: str = Field(min_length=1)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=1)
    pdf_page: int | None = Field(default=None, ge=1)
    printed_page: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    dom_selector: str | None = None
    xpath: str | None = None
    fhir_resource_type: str | None = None
    fhir_resource_id: str | None = None
    fhir_path: str | None = None
    table_id: str | None = None
    row_index: int | None = Field(default=None, ge=0)
    column_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_locator(self) -> SourceAnchor:
        if (self.char_start is None) != (self.char_end is None):
            raise ValueError("character anchors require both char_start and char_end")
        if self.char_start is not None and self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        if self.bbox is not None:
            left, top, right, bottom = self.bbox
            if right <= left or bottom <= top:
                raise ValueError("a bounding box must have positive width and height")
        if self.kind is LocatorKind.PDF and self.pdf_page is None:
            raise ValueError("PDF anchors require pdf_page")
        if self.kind is LocatorKind.HTML and self.dom_selector is None:
            raise ValueError("HTML anchors require dom_selector")
        if self.kind is LocatorKind.XML and self.xpath is None:
            raise ValueError("XML anchors require xpath")
        if self.kind is LocatorKind.FHIR and not all(
            (self.fhir_resource_type, self.fhir_resource_id, self.fhir_path)
        ):
            raise ValueError("FHIR anchors require resource type, resource ID, and path")
        if self.kind is LocatorKind.TABLE_CELL and (
            self.table_id is None or self.row_index is None or self.column_index is None
        ):
            raise ValueError("table-cell anchors require table, row, and column locators")
        if self.kind is LocatorKind.TEXT_SPAN and self.char_start is None:
            raise ValueError("text-span anchors require character offsets")
        return self


class RecommendationGrade(CanonicalModel):
    grading_system: str = Field(min_length=1)
    publisher_value: str = Field(min_length=1)
    normalized_strength: str | None = None
    certainty: str | None = None


class ApplicabilityScope(CanonicalModel):
    population: tuple[str, ...] = ()
    care_settings: tuple[str, ...] = ()
    inclusion_criteria: tuple[str, ...] = ()
    exclusion_criteria: tuple[str, ...] = ()

    @field_validator(
        "population", "care_settings", "inclusion_criteria", "exclusion_criteria"
    )
    @classmethod
    def sort_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("applicability values must be unique")
        return tuple(sorted(value))


class EvidenceVerificationCheck(CanonicalModel):
    invariant: VerificationInvariant
    outcome: VerificationOutcome
    verifier: str = Field(min_length=1)
    evidence_digest: str = Field(pattern=SHA256_PATTERN)
    details: dict[str, Any] = Field(default_factory=dict)


class EvidenceVerification(CanonicalModel):
    approval_status: EvidenceApprovalStatus
    checks: tuple[EvidenceVerificationCheck, ...]
    unresolved_reasons: tuple[str, ...] = ()

    @field_validator("checks")
    @classmethod
    def require_unique_checks(
        cls, value: tuple[EvidenceVerificationCheck, ...]
    ) -> tuple[EvidenceVerificationCheck, ...]:
        invariants = [check.invariant for check in value]
        if len(invariants) != len(set(invariants)):
            raise ValueError("verification invariants must be unique")
        return tuple(sorted(value, key=lambda item: item.invariant.value))

    @model_validator(mode="after")
    def approved_must_pass_critical_checks(self) -> EvidenceVerification:
        if self.approval_status is not EvidenceApprovalStatus.APPROVED:
            return self
        required = {
            VerificationInvariant.PROVENANCE,
            VerificationInvariant.EXACT_CONTENT,
            VerificationInvariant.CRITICAL_FIELDS,
        }
        passed = {
            check.invariant
            for check in self.checks
            if check.outcome is VerificationOutcome.PASS
        }
        if missing := required - passed:
            names = ", ".join(sorted(item.value for item in missing))
            raise ValueError(f"approved evidence is missing passing checks: {names}")
        if self.unresolved_reasons:
            raise ValueError("approved evidence cannot have unresolved reasons")
        if any(check.outcome is not VerificationOutcome.PASS for check in self.checks):
            raise ValueError("approved evidence cannot contain failed or unresolved checks")
        return self


class CorpusEvidenceRecord(CanonicalModel):
    schema_version: Literal[CORPUS_CONTRACT_VERSION] = CORPUS_CONTRACT_VERSION
    corpus_release_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_version_id: str = Field(min_length=1)
    source_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    publisher_id: str = Field(min_length=1)
    jurisdiction: str = Field(min_length=1)
    language: str = Field(min_length=2)
    lifecycle_status: SourceStatus
    evidence_roles: tuple[EvidenceRole, ...]
    content_exact: str = Field(min_length=1)
    content_search: str = Field(min_length=1)
    anchors: tuple[SourceAnchor, ...] = Field(min_length=1)
    applicability: ApplicabilityScope = Field(default_factory=ApplicabilityScope)
    recommendation_grade: RecommendationGrade | None = None
    render_allowed: bool
    verification: EvidenceVerification

    @field_validator("evidence_roles")
    @classmethod
    def sort_unique_roles(cls, value: tuple[EvidenceRole, ...]) -> tuple[EvidenceRole, ...]:
        if not value:
            raise ValueError("evidence must have at least one evidence role")
        if len(value) != len(set(value)):
            raise ValueError("evidence roles must be unique")
        return tuple(sorted(value, key=lambda item: item.value))

    @model_validator(mode="after")
    def approved_evidence_requires_effective_source(self) -> CorpusEvidenceRecord:
        if self.verification.approval_status is EvidenceApprovalStatus.APPROVED and (
            self.lifecycle_status
            not in {
                SourceStatus.APPROVED,
                SourceStatus.EFFECTIVE,
                SourceStatus.PARTIALLY_SUPERSEDED,
            }
        ):
            raise ValueError("approved evidence must reference an approved lifecycle state")
        return self

    @property
    def sha256(self) -> str:
        return canonical_sha256(self)


class InventorySnapshot(CanonicalModel):
    trust_root_id: str = Field(min_length=1)
    publisher_id: str = Field(min_length=1)
    cutoff_at: datetime
    inventory_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    expected_item_ids: tuple[str, ...]
    included_item_ids: tuple[str, ...]
    excepted_item_ids: tuple[str, ...] = ()
    complete: bool

    @field_validator("expected_item_ids", "included_item_ids", "excepted_item_ids")
    @classmethod
    def sort_unique_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("inventory item IDs must be unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_reconciliation(self) -> InventorySnapshot:
        expected = set(self.expected_item_ids)
        included = set(self.included_item_ids)
        excepted = set(self.excepted_item_ids)
        if included & excepted:
            raise ValueError("inventory items cannot be both included and excepted")
        if not included | excepted <= expected:
            raise ValueError("included and excepted items must belong to the official inventory")
        reconciled = included | excepted == expected
        if self.complete != reconciled:
            raise ValueError("inventory complete flag must match the reconciliation result")
        return self


class CoverageException(CanonicalModel):
    exception_id: str = Field(min_length=1)
    trust_root_id: str = Field(min_length=1)
    inventory_item_id: str = Field(min_length=1)
    reason: ExceptionReason
    details: str = Field(min_length=1)
    approved_by: str = Field(min_length=1)
    approved_at: datetime
    statement_sha256: str = Field(pattern=SHA256_PATTERN)
    signature_sha256: str = Field(pattern=SHA256_PATTERN)


class AttestationReference(CanonicalModel):
    attestation_id: str = Field(min_length=1)
    stage: AttestationStage
    statement_sha256: str = Field(pattern=SHA256_PATTERN)
    signature_sha256: str = Field(pattern=SHA256_PATTERN)
    signer_identity: str = Field(min_length=1)


class ManifestEvidenceEntry(CanonicalModel):
    evidence_id: str = Field(min_length=1)
    source_version_id: str = Field(min_length=1)
    evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    qa_decision_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def artifact_is_canonical_evidence(self) -> ManifestEvidenceEntry:
        if self.artifact_sha256 != self.evidence_sha256:
            raise ValueError("canonical evidence artifact digest must match evidence content")
        return self


class CorpusReleaseManifestContent(CanonicalModel):
    schema_version: Literal[CORPUS_CONTRACT_VERSION] = CORPUS_CONTRACT_VERSION
    corpus_release_id: str = Field(min_length=1)
    created_at: datetime
    cutoff_at: datetime
    previous_release_id: str | None = None
    trust_root_registry_sha256: str = Field(pattern=SHA256_PATTERN)
    release_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    qdrant_collection: str = Field(min_length=1, max_length=255)
    inventory_snapshots: tuple[InventorySnapshot, ...] = Field(min_length=1)
    exceptions: tuple[CoverageException, ...] = ()
    evidence: tuple[ManifestEvidenceEntry, ...]
    attestations: tuple[AttestationReference, ...]

    @field_validator("inventory_snapshots")
    @classmethod
    def sort_unique_inventories(
        cls, value: tuple[InventorySnapshot, ...]
    ) -> tuple[InventorySnapshot, ...]:
        trust_roots = [snapshot.trust_root_id for snapshot in value]
        if len(trust_roots) != len(set(trust_roots)):
            raise ValueError("a release cannot repeat a trust-root inventory")
        return tuple(sorted(value, key=lambda item: item.trust_root_id))

    @field_validator("exceptions")
    @classmethod
    def sort_unique_exceptions(
        cls, value: tuple[CoverageException, ...]
    ) -> tuple[CoverageException, ...]:
        ids = [item.exception_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("exception IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.exception_id))

    @field_validator("evidence")
    @classmethod
    def sort_unique_evidence(
        cls, value: tuple[ManifestEvidenceEntry, ...]
    ) -> tuple[ManifestEvidenceEntry, ...]:
        ids = [item.evidence_id for item in value]
        if not ids:
            raise ValueError("a release must contain evidence")
        if len(ids) != len(set(ids)):
            raise ValueError("manifest evidence IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.evidence_id))

    @field_validator("attestations")
    @classmethod
    def sort_unique_attestations(
        cls, value: tuple[AttestationReference, ...]
    ) -> tuple[AttestationReference, ...]:
        stages = [item.stage for item in value]
        if len(stages) != len(set(stages)):
            raise ValueError("attestation stages must be unique")
        return tuple(sorted(value, key=lambda item: item.stage.value))

    @model_validator(mode="after")
    def validate_temporal_and_coverage_links(self) -> CorpusReleaseManifestContent:
        if self.cutoff_at > self.created_at:
            raise ValueError("release cutoff cannot be after manifest creation")
        inventory_pairs = {
            (snapshot.trust_root_id, item_id)
            for snapshot in self.inventory_snapshots
            for item_id in snapshot.excepted_item_ids
        }
        exception_pairs = {
            (exception.trust_root_id, exception.inventory_item_id)
            for exception in self.exceptions
        }
        if inventory_pairs != exception_pairs:
            raise ValueError("exception register must exactly match excepted inventory items")
        return self


class CorpusReleaseManifest(CanonicalModel):
    content: CorpusReleaseManifestContent
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_manifest_digest(self) -> CorpusReleaseManifest:
        expected = canonical_sha256(self.content)
        if self.manifest_sha256 != expected:
            raise ValueError("manifest_sha256 does not match canonical manifest content")
        return self

    @classmethod
    def seal(cls, content: CorpusReleaseManifestContent) -> CorpusReleaseManifest:
        return cls(content=content, manifest_sha256=canonical_sha256(content))


class ActivationDecisionContent(CanonicalModel):
    schema_version: Literal[ACTIVATION_CONTRACT_VERSION] = ACTIVATION_CONTRACT_VERSION
    corpus_release_id: str = Field(min_length=1)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    release_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    qdrant_collection: str = Field(min_length=1)
    index_point_count: int = Field(ge=0)
    index_attestation_sha256: str = Field(pattern=SHA256_PATTERN)
    benchmark_acceptance_sha256: str = Field(pattern=SHA256_PATTERN)
    decision: Literal["ACTIVATE"] = "ACTIVATE"
    decided_at: datetime


class SignedActivationDecision(CanonicalModel):
    content: ActivationDecisionContent
    statement_sha256: str = Field(pattern=SHA256_PATTERN)
    signature_sha256: str = Field(pattern=SHA256_PATTERN)
    signer_identity: str = Field(min_length=1)
    signing_key_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def verify_statement_digest(self) -> SignedActivationDecision:
        if self.statement_sha256 != canonical_sha256(self.content):
            raise ValueError("statement_sha256 does not match activation decision content")
        return self

    @classmethod
    def seal(
        cls,
        content: ActivationDecisionContent,
        *,
        signature_sha256: str,
        signer_identity: str,
        signing_key_id: str,
    ) -> SignedActivationDecision:
        return cls(
            content=content,
            statement_sha256=canonical_sha256(content),
            signature_sha256=signature_sha256,
            signer_identity=signer_identity,
            signing_key_id=signing_key_id,
        )


class CorpusReleaseBundle(CanonicalModel):
    manifest: CorpusReleaseManifest
    evidence: tuple[CorpusEvidenceRecord, ...]

    @field_validator("evidence")
    @classmethod
    def sort_bundle_evidence(
        cls, value: tuple[CorpusEvidenceRecord, ...]
    ) -> tuple[CorpusEvidenceRecord, ...]:
        ids = [item.evidence_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("bundle evidence IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.evidence_id))

    @model_validator(mode="after")
    def verify_bundle_integrity(self) -> CorpusReleaseBundle:
        release_id = self.manifest.content.corpus_release_id
        if any(item.corpus_release_id != release_id for item in self.evidence):
            raise ValueError("all evidence must belong to the manifest release")
        actual = {
            item.evidence_id: (item.source_version_id, item.sha256) for item in self.evidence
        }
        declared = {
            item.evidence_id: (item.source_version_id, item.evidence_sha256)
            for item in self.manifest.content.evidence
        }
        if actual != declared:
            raise ValueError("manifest evidence entries do not match bundled evidence")
        return self

    def activation_blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if any(not snapshot.complete for snapshot in self.manifest.content.inventory_snapshots):
            blockers.append("INVENTORY_NOT_RECONCILED")
        if any(
            item.verification.approval_status is not EvidenceApprovalStatus.APPROVED
            for item in self.evidence
        ):
            blockers.append("EVIDENCE_NOT_APPROVED")
        stages = {item.stage for item in self.manifest.content.attestations}
        if missing := REQUIRED_RELEASE_ATTESTATIONS - stages:
            blockers.extend(
                f"MISSING_ATTESTATION:{stage.value}"
                for stage in sorted(missing, key=lambda item: item.value)
            )
        return tuple(blockers)


class CorpusReleaseRecord(CanonicalModel):
    corpus_release_id: str
    contract_version: str
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    state: ReleaseState
    previous_release_id: str | None = None
    qdrant_collection: str
    cutoff_at: datetime
    evidence_count: int = Field(ge=0)
    index_status: Literal["NOT_BUILT", "VALIDATED"]
    index_point_count: int | None = Field(default=None, ge=0)
    index_attestation_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    index_validated_at: datetime | None = None
    benchmark_acceptance_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    benchmark_accepted_at: datetime | None = None
    benchmark_valid_until: datetime | None = None
    validated_at: datetime | None = None
    activated_at: datetime | None = None
    activated_by: str | None = None


class ActiveCorpusRelease(CanonicalModel):
    corpus_release_id: str
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    qdrant_collection: str
    activated_at: datetime
