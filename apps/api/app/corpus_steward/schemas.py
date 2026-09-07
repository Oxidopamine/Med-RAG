"""Canonical contracts for trust roots and inventory reconciliation.

These records deliberately stop at immutable source acquisition. Evidence extraction and
retrieval indexing consume a successful reconciliation candidate in later phases.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.corpus import SHA256_PATTERN, canonical_sha256
from app.schemas.domain import CanonicalModel, SourceStatus

STEWARD_CONTRACT_VERSION = "1.0.0"


class LicensingPolicy(CanonicalModel):
    license_id: str = Field(min_length=1)
    policy_url: str = Field(min_length=1)
    acquisition_allowed: bool
    redistribution_allowed: bool = False
    credential_reference: str | None = None
    notes: str | None = None


class LicensedAssetKind(str, Enum):
    INVENTORY_SOURCE = "INVENTORY_SOURCE"
    NARRATIVE_SOURCE = "NARRATIVE_SOURCE"
    DEPENDENCY_SOURCE = "DEPENDENCY_SOURCE"


class AssetLicensingPolicy(LicensingPolicy):
    """License and permitted processing operations for one logical source asset."""

    asset_id: str = Field(min_length=1, max_length=200)
    asset_kind: LicensedAssetKind
    source_declared_license_id: str | None = None
    evidence_materialization_allowed: bool = False
    render_allowed: bool = False


class TrustRootDefinition(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    trust_root_id: str = Field(min_length=1, max_length=128)
    publisher_id: str = Field(min_length=1, max_length=64)
    publisher_name: str = Field(min_length=1, max_length=300)
    allowed_domains: tuple[str, ...] = Field(min_length=1)
    jurisdictions: tuple[str, ...] = Field(min_length=1)
    product_families: tuple[str, ...] = Field(min_length=1)
    # ``licensing_policy`` is retained only so historical signed revisions remain
    # readable. New trust roots must use ``asset_licensing`` and may not combine the
    # two representations.
    licensing_policy: LicensingPolicy | None = None
    asset_licensing: tuple[AssetLicensingPolicy, ...] = ()
    polling_interval_seconds: int = Field(ge=300)
    connector_name: str = Field(min_length=1, max_length=100)
    connector_version: str = Field(min_length=1, max_length=100)
    connector_config: dict[str, Any] = Field(default_factory=dict)
    trusted_stage_key_ids: tuple[str, ...] = Field(min_length=1)
    enabled: bool = True

    @field_validator("asset_licensing")
    @classmethod
    def sort_unique_asset_licenses(
        cls, value: tuple[AssetLicensingPolicy, ...]
    ) -> tuple[AssetLicensingPolicy, ...]:
        ids = [item.asset_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("asset licensing policies must have unique asset IDs")
        return tuple(sorted(value, key=lambda item: item.asset_id))

    @model_validator(mode="after")
    def require_one_license_representation(self) -> TrustRootDefinition:
        if self.licensing_policy is None and not self.asset_licensing:
            raise ValueError("trust root requires per-asset licensing")
        if self.licensing_policy is not None and self.asset_licensing:
            raise ValueError("global and per-asset licensing cannot be combined")
        return self

    def license_for_asset(self, asset_id: str) -> AssetLicensingPolicy:
        matches = [item for item in self.asset_licensing if item.asset_id == asset_id]
        if len(matches) != 1:
            raise ValueError(f"asset has no unique licensing policy: {asset_id}")
        return matches[0]

    @field_validator(
        "allowed_domains",
        "jurisdictions",
        "product_families",
        "trusted_stage_key_ids",
    )
    @classmethod
    def sort_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(set(value)))
        if len(normalized) != len(value):
            raise ValueError("trust-root values must be unique")
        return normalized

    @field_validator("allowed_domains")
    @classmethod
    def normalize_domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        domains = tuple(domain.lower().rstrip(".") for domain in value)
        if any("://" in domain or "/" in domain for domain in domains):
            raise ValueError("allowed domains must be hostnames, not URLs")
        return domains

    @property
    def sha256(self) -> str:
        payload = self.model_dump(mode="json")
        # Preserve the digest of signed 1.0.0 revisions created before per-asset
        # licensing was introduced. The new default field must not retroactively
        # alter their canonical byte representation.
        if self.licensing_policy is not None:
            payload.pop("asset_licensing", None)
        return canonical_sha256(payload)


class ArtifactKind(str, Enum):
    INVENTORY_RESPONSE = "INVENTORY_RESPONSE"
    INVENTORY_MANIFEST = "INVENTORY_MANIFEST"
    SOURCE = "SOURCE"
    RELEASE_CANDIDATE = "RELEASE_CANDIDATE"
    STRUCTURED_REPORT = "STRUCTURED_REPORT"
    DEPENDENCY_METADATA = "DEPENDENCY_METADATA"
    DEPENDENCY_PACKAGE = "DEPENDENCY_PACKAGE"
    NARRATIVE_SOURCE = "NARRATIVE_SOURCE"
    NARRATIVE_ANALYSIS_REPORT = "NARRATIVE_ANALYSIS_REPORT"
    INPUT_CLOSURE_REPORT = "INPUT_CLOSURE_REPORT"
    AUTHORITY_BINDING = "AUTHORITY_BINDING"
    EVIDENCE_RECORD = "EVIDENCE_RECORD"
    MATERIALIZATION_REPORT = "MATERIALIZATION_REPORT"
    CORPUS_RELEASE_CANDIDATE = "CORPUS_RELEASE_CANDIDATE"
    EVIDENCE_ARTIFACT_MANIFEST = "EVIDENCE_ARTIFACT_MANIFEST"
    QA_DECISION_BATCH = "QA_DECISION_BATCH"
    CANONICAL_EVIDENCE_RECORD = "CANONICAL_EVIDENCE_RECORD"
    CORPUS_RELEASE_BUNDLE = "CORPUS_RELEASE_BUNDLE"
    RELEASE_ASSEMBLY = "RELEASE_ASSEMBLY"
    BENCHMARK_ACCESS_POLICY = "BENCHMARK_ACCESS_POLICY"
    BENCHMARK_ADJUDICATION_POLICY = "BENCHMARK_ADJUDICATION_POLICY"
    BENCHMARK_THRESHOLD_POLICY = "BENCHMARK_THRESHOLD_POLICY"
    BENCHMARK_REVIEW_DECISION = "BENCHMARK_REVIEW_DECISION"
    BENCHMARK_RESOLUTION = "BENCHMARK_RESOLUTION"
    BENCHMARK_ADJUDICATION_RECORD = "BENCHMARK_ADJUDICATION_RECORD"
    BENCHMARK_GENERATION_POLICY = "BENCHMARK_GENERATION_POLICY"
    BENCHMARK_GENERATION_RECORD = "BENCHMARK_GENERATION_RECORD"
    BENCHMARK_SUITE = "BENCHMARK_SUITE"


class ReconciliationStage(str, Enum):
    ENUMERATE_INVENTORY = "ENUMERATE_INVENTORY"
    DIFF_INVENTORY = "DIFF_INVENTORY"
    FETCH_ARTIFACTS = "FETCH_ARTIFACTS"
    RECONCILE = "RECONCILE"
    RELEASE_CANDIDATE = "RELEASE_CANDIDATE"


STAGE_ORDER = (
    ReconciliationStage.ENUMERATE_INVENTORY,
    ReconciliationStage.DIFF_INVENTORY,
    ReconciliationStage.FETCH_ARTIFACTS,
    ReconciliationStage.RECONCILE,
    ReconciliationStage.RELEASE_CANDIDATE,
)


class JobState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


class StageState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


class InventoryChangeKind(str, Enum):
    NEW = "NEW"
    CHANGED = "CHANGED"
    UNCHANGED = "UNCHANGED"
    REMOVED = "REMOVED"


class ReconciliationDisposition(str, Enum):
    INCLUDED = "INCLUDED"
    EXCEPTED = "EXCEPTED"
    BLOCKED = "BLOCKED"


class AttestationPurpose(str, Enum):
    STAGE = "STAGE"
    EXCEPTION = "EXCEPTION"
    ACTIVATION = "ACTIVATION"


class BenchmarkAttestationPurpose(str, Enum):
    """Separate registry purpose kept out of frozen pre-benchmark contracts."""

    BENCHMARK_ACCEPTANCE = "BENCHMARK_ACCEPTANCE"


class InventoryItem(CanonicalModel):
    item_id: str = Field(min_length=1, max_length=512)
    title: str = Field(min_length=1, max_length=1000)
    version: str = Field(min_length=1, max_length=200)
    lifecycle_status: SourceStatus
    canonical_url: str = Field(min_length=1)
    artifact_url: str = Field(min_length=1)
    media_type: str = Field(min_length=1, max_length=200)
    publisher_metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def fingerprint_sha256(self) -> str:
        return canonical_sha256(self)


class PreservedResponse(CanonicalModel):
    requested_url: str = Field(min_length=1)
    final_url: str = Field(min_length=1)
    status_code: int = Field(ge=200, le=399)
    media_type: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    byte_size: int = Field(ge=0)
    fetched_at: datetime


class InventoryEnumeration(CanonicalModel):
    cutoff_at: datetime
    items: tuple[InventoryItem, ...]
    responses: tuple[PreservedResponse, ...] = Field(min_length=1)
    inventory_artifact_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)

    @field_validator("items")
    @classmethod
    def sort_unique_items(cls, value: tuple[InventoryItem, ...]) -> tuple[InventoryItem, ...]:
        ids = [item.item_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("connector inventory item IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.item_id))


class InventoryChange(CanonicalModel):
    item_id: str = Field(min_length=1)
    change: InventoryChangeKind
    previous_fingerprint_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    current_fingerprint_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)


class SourceArtifactReference(CanonicalModel):
    item_id: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    byte_size: int = Field(gt=0)
    media_type: str = Field(min_length=1)
    requested_url: str = Field(min_length=1)
    final_url: str = Field(min_length=1)
    etag: str | None = None
    last_modified: str | None = None
    fetched_at: datetime
    reused_after_not_modified: bool = False


class CoverageExceptionContent(CanonicalModel):
    trust_root_id: str = Field(min_length=1)
    inventory_item_id: str = Field(min_length=1)
    reason: Literal["ACCESS", "LICENSING", "ACQUISITION", "VALIDATION"]
    details: str = Field(min_length=1)
    approved_at: datetime
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_expiration(self) -> CoverageExceptionContent:
        if self.expires_at is not None and self.expires_at <= self.approved_at:
            raise ValueError("coverage exception expiration must follow approval")
        return self


class SignedExceptionReference(CanonicalModel):
    exception_id: str = Field(min_length=1)
    content: CoverageExceptionContent
    statement_sha256: str = Field(pattern=SHA256_PATTERN)
    signature_sha256: str = Field(pattern=SHA256_PATTERN)
    signer_identity: str = Field(min_length=1)
    signing_key_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def verify_digest(self) -> SignedExceptionReference:
        if self.statement_sha256 != canonical_sha256(self.content):
            raise ValueError("exception statement digest does not match its content")
        return self


class StageAttestationContent(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    job_id: str = Field(min_length=1)
    trust_root_id: str = Field(min_length=1)
    trust_root_sha256: str = Field(pattern=SHA256_PATTERN)
    connector_name: str = Field(min_length=1)
    connector_version: str = Field(min_length=1)
    stage: ReconciliationStage
    input_sha256: str = Field(pattern=SHA256_PATTERN)
    output_sha256: str = Field(pattern=SHA256_PATTERN)
    completed_at: datetime


class SignatureEnvelope(CanonicalModel):
    algorithm: Literal["ED25519"] = "ED25519"
    key_id: str = Field(min_length=1, max_length=200)
    signer_identity: str = Field(min_length=1, max_length=300)
    statement_sha256: str = Field(pattern=SHA256_PATTERN)
    signature_base64: str = Field(min_length=1)
    signature_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_signature_encoding(self) -> SignatureEnvelope:
        try:
            signature = base64.b64decode(self.signature_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("signature_base64 is not valid base64") from error
        if len(signature) != 64:
            raise ValueError("an Ed25519 signature must be 64 bytes")
        if hashlib.sha256(signature).hexdigest() != self.signature_sha256:
            raise ValueError("signature_sha256 does not match signature bytes")
        return self


class VerifiedAttestationReference(CanonicalModel):
    attestation_id: str = Field(min_length=1)
    purpose: AttestationPurpose
    predicate_type: str = Field(min_length=1)
    statement_sha256: str = Field(pattern=SHA256_PATTERN)
    signature_sha256: str = Field(pattern=SHA256_PATTERN)
    signer_identity: str = Field(min_length=1)
    signing_key_id: str = Field(min_length=1)
    verified_at: datetime


class BenchmarkAttestationReference(CanonicalModel):
    attestation_id: str = Field(min_length=1)
    purpose: BenchmarkAttestationPurpose
    predicate_type: str = Field(min_length=1)
    statement_sha256: str = Field(pattern=SHA256_PATTERN)
    signature_sha256: str = Field(pattern=SHA256_PATTERN)
    signer_identity: str = Field(min_length=1)
    signing_key_id: str = Field(min_length=1)
    verified_at: datetime


class ReconciliationSnapshot(CanonicalModel):
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
            raise ValueError("reconciliation item IDs must be unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def verify_accounting(self) -> ReconciliationSnapshot:
        expected = set(self.expected_item_ids)
        included = set(self.included_item_ids)
        excepted = set(self.excepted_item_ids)
        if included & excepted:
            raise ValueError("inventory items cannot be included and excepted")
        if not included | excepted <= expected:
            raise ValueError("reconciled items must belong to the official inventory")
        if self.complete != (included | excepted == expected):
            raise ValueError("complete must exactly match inventory accounting")
        return self


class ReconciliationCandidateContent(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    candidate_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    created_at: datetime
    trust_root_sha256: str = Field(pattern=SHA256_PATTERN)
    snapshot: ReconciliationSnapshot
    changes: tuple[InventoryChange, ...]
    source_artifacts: tuple[SourceArtifactReference, ...]
    exceptions: tuple[SignedExceptionReference, ...]
    stage_attestations: tuple[VerifiedAttestationReference, ...]

    @model_validator(mode="after")
    def verify_release_gate(self) -> ReconciliationCandidateContent:
        if not self.snapshot.complete:
            raise ValueError("a release candidate requires complete inventory accounting")
        expected = set(self.snapshot.expected_item_ids)
        artifacts = {item.item_id for item in self.source_artifacts}
        excepted = {item.content.inventory_item_id for item in self.exceptions}
        if artifacts != set(self.snapshot.included_item_ids):
            raise ValueError("source artifacts must exactly match included inventory items")
        if excepted != set(self.snapshot.excepted_item_ids):
            raise ValueError("signed exceptions must exactly match excepted inventory items")
        if artifacts | excepted != expected:
            raise ValueError("candidate does not account for the complete official inventory")
        required = {
            ReconciliationStage.ENUMERATE_INVENTORY.value,
            ReconciliationStage.DIFF_INVENTORY.value,
            ReconciliationStage.FETCH_ARTIFACTS.value,
            ReconciliationStage.RECONCILE.value,
        }
        predicates = {item.predicate_type.rsplit("/", 1)[-1] for item in self.stage_attestations}
        if not required <= predicates:
            raise ValueError("candidate is missing verified stage attestations")
        return self


class ReconciliationReleaseCandidate(CanonicalModel):
    content: ReconciliationCandidateContent
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> ReconciliationReleaseCandidate:
        if self.candidate_sha256 != canonical_sha256(self.content):
            raise ValueError("candidate digest does not match canonical content")
        return self

    @classmethod
    def seal(cls, content: ReconciliationCandidateContent) -> ReconciliationReleaseCandidate:
        return cls(content=content, candidate_sha256=canonical_sha256(content))


class ReconciliationReport(CanonicalModel):
    job_id: str
    trust_root_id: str
    state: JobState
    inventory_count: int = Field(ge=0)
    new_count: int = Field(ge=0)
    changed_count: int = Field(ge=0)
    removed_count: int = Field(ge=0)
    included_count: int = Field(ge=0)
    excepted_count: int = Field(ge=0)
    blockers: tuple[str, ...] = ()
    release_candidate: ReconciliationReleaseCandidate | None = None
