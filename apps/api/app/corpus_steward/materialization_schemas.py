"""Phase 3 contracts for authority composition and evidence materialization."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.corpus_steward.schemas import (
    STEWARD_CONTRACT_VERSION,
    AssetLicensingPolicy,
    VerifiedAttestationReference,
)
from app.schemas.corpus import SHA256_PATTERN, SourceAnchor, canonical_sha256
from app.schemas.domain import CanonicalModel, SourceStatus

MATERIALIZER_NAME = "authority-evidence-materializer"
MATERIALIZER_VERSION = "1.0.0"


class MaterializationState(str, Enum):
    READY_FOR_QA = "READY_FOR_QA"
    BLOCKED = "BLOCKED"


class AuthorityRole(str, Enum):
    CONTROLLING_CLINICAL_SOURCE = "CONTROLLING_CLINICAL_SOURCE"
    STRUCTURED_COMPANION = "STRUCTURED_COMPANION"


class AuthorizedUse(str, Enum):
    CLINICAL_EVIDENCE = "CLINICAL_EVIDENCE"
    STRUCTURAL_MAPPING_ONLY = "STRUCTURAL_MAPPING_ONLY"


class EvidenceQAStatus(str, Enum):
    AWAITING_QA = "AWAITING_QA"


class AuthorityAssetBinding(CanonicalModel):
    asset_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=1000)
    authority_role: AuthorityRole
    authorized_use: AuthorizedUse
    source_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    media_type: str = Field(min_length=1, max_length=200)
    source_uri: str = Field(min_length=1)
    lifecycle_status: SourceStatus
    experimental: bool = False
    clinical_content_promotable: bool
    licensing: AssetLicensingPolicy

    @model_validator(mode="after")
    def enforce_authority_boundary(self) -> AuthorityAssetBinding:
        if self.licensing.asset_id != self.asset_id:
            raise ValueError("authority asset and license asset IDs differ")
        if self.authority_role is AuthorityRole.CONTROLLING_CLINICAL_SOURCE:
            if self.authorized_use is not AuthorizedUse.CLINICAL_EVIDENCE:
                raise ValueError("the controlling source must authorize clinical evidence")
            if not self.clinical_content_promotable or self.experimental:
                raise ValueError("the controlling clinical source must be non-experimental")
        else:
            if self.authorized_use is not AuthorizedUse.STRUCTURAL_MAPPING_ONLY:
                raise ValueError("a structured companion is limited to structural mappings")
            if self.clinical_content_promotable:
                raise ValueError("structured companion clinical content cannot be promoted")
        return self


class AuthorityBindingContent(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    materialization_run_id: str = Field(min_length=1, max_length=64)
    reconciliation_candidate_id: str = Field(min_length=1, max_length=64)
    trust_root_id: str = Field(min_length=1, max_length=128)
    trust_root_sha256: str = Field(pattern=SHA256_PATTERN)
    licensing_trust_root_sha256: str = Field(pattern=SHA256_PATTERN)
    input_closure_sha256: str = Field(pattern=SHA256_PATTERN)
    structured_report_sha256: str = Field(pattern=SHA256_PATTERN)
    controlling_source_id: str = Field(min_length=1, max_length=200)
    structured_companion_id: str = Field(min_length=1, max_length=200)
    assets: tuple[AuthorityAssetBinding, ...] = Field(min_length=2)
    bound_at: datetime

    @field_validator("assets")
    @classmethod
    def sort_unique_assets(
        cls, value: tuple[AuthorityAssetBinding, ...]
    ) -> tuple[AuthorityAssetBinding, ...]:
        ids = [item.asset_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("authority binding asset IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.asset_id))

    @model_validator(mode="after")
    def require_one_controller_and_one_companion(self) -> AuthorityBindingContent:
        controllers = [
            item
            for item in self.assets
            if item.authority_role is AuthorityRole.CONTROLLING_CLINICAL_SOURCE
        ]
        companions = [
            item
            for item in self.assets
            if item.authority_role is AuthorityRole.STRUCTURED_COMPANION
        ]
        if not controllers or any(
            item.asset_id == self.structured_companion_id for item in controllers
        ):
            raise ValueError("authority binding requires controlling narrative assets")
        if {item.asset_id for item in controllers} != {
            item.asset_id
            for item in self.assets
            if item.authorized_use is AuthorizedUse.CLINICAL_EVIDENCE
        }:
            raise ValueError("only controlling assets may authorize clinical evidence")
        if len(companions) != 1 or companions[0].asset_id != self.structured_companion_id:
            raise ValueError("authority binding requires exactly one structured companion")
        if self.controlling_source_id in {item.asset_id for item in self.assets}:
            raise ValueError("controlling_source_id identifies the source set, not one asset")
        return self


class SignedAuthorityBinding(CanonicalModel):
    content: AuthorityBindingContent
    binding_sha256: str = Field(pattern=SHA256_PATTERN)
    attestation: VerifiedAttestationReference

    @model_validator(mode="after")
    def verify_digest_and_attestation(self) -> SignedAuthorityBinding:
        expected = canonical_sha256(self.content)
        if self.binding_sha256 != expected:
            raise ValueError("authority binding digest is inconsistent")
        if self.attestation.statement_sha256 != expected:
            raise ValueError("authority binding signature covers different content")
        return self


class MaterializedEvidenceContent(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    materialization_run_id: str = Field(min_length=1, max_length=64)
    evidence_id: str = Field(min_length=1, max_length=64)
    authority_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    asset_id: str = Field(min_length=1, max_length=200)
    source_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    source_unit_id: str = Field(min_length=1, max_length=500)
    source_title: str = Field(min_length=1, max_length=1000)
    source_version_id: str = Field(min_length=1, max_length=300)
    publisher_id: str = Field(min_length=1, max_length=64)
    jurisdiction: str = Field(min_length=1, max_length=32)
    language: str = Field(min_length=2, max_length=20)
    lifecycle_status: SourceStatus
    authority_role: Literal[AuthorityRole.CONTROLLING_CLINICAL_SOURCE] = (
        AuthorityRole.CONTROLLING_CLINICAL_SOURCE
    )
    qa_status: Literal[EvidenceQAStatus.AWAITING_QA] = EvidenceQAStatus.AWAITING_QA
    content_exact: str = Field(min_length=1)
    content_search: str = Field(min_length=1)
    anchors: tuple[SourceAnchor, ...] = Field(min_length=1)
    render_allowed: bool


class MaterializedEvidenceRecord(CanonicalModel):
    content: MaterializedEvidenceContent
    evidence_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> MaterializedEvidenceRecord:
        if self.evidence_sha256 != canonical_sha256(self.content):
            raise ValueError("materialized evidence digest is inconsistent")
        return self

    @classmethod
    def seal(cls, content: MaterializedEvidenceContent) -> MaterializedEvidenceRecord:
        return cls(content=content, evidence_sha256=canonical_sha256(content))


class MaterializedEvidenceArtifactEntry(CanonicalModel):
    """Compact report entry; full evidence lives in its content-addressed artifact."""

    evidence_id: str = Field(min_length=1, max_length=64)
    evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    source_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    asset_id: str = Field(min_length=1, max_length=200)
    source_unit_id: str = Field(min_length=1, max_length=500)


class AssetCoverage(CanonicalModel):
    asset_id: str = Field(min_length=1)
    source_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    expected_source_units: int = Field(ge=1)
    materialized_source_units: int = Field(ge=0)
    empty_source_units: int = Field(ge=0)
    evidence_ids: tuple[str, ...]
    complete: bool

    @field_validator("evidence_ids")
    @classmethod
    def sort_unique_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("asset coverage evidence IDs must be unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def verify_accounting(self) -> AssetCoverage:
        accounted = self.materialized_source_units + self.empty_source_units
        expected_complete = (
            accounted == self.expected_source_units
            and self.materialized_source_units == len(self.evidence_ids)
        )
        if self.complete != expected_complete:
            raise ValueError("asset coverage does not account for every source unit")
        return self


class StructuralMappingAttachment(CanonicalModel):
    asset_id: str = Field(min_length=1)
    structured_run_id: str = Field(min_length=1)
    structured_report_sha256: str = Field(pattern=SHA256_PATTERN)
    resource_inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    resource_count: int = Field(gt=0)
    implementation_guide_experimental: bool
    authorized_use: Literal[AuthorizedUse.STRUCTURAL_MAPPING_ONLY] = (
        AuthorizedUse.STRUCTURAL_MAPPING_ONLY
    )
    clinical_content_included: Literal[False] = False


class EvidenceCoverageReport(CanonicalModel):
    authority_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    assets: tuple[AssetCoverage, ...] = Field(min_length=1)
    expected_asset_ids: tuple[str, ...] = Field(min_length=1)
    evidence_count: int = Field(ge=0)
    complete: bool

    @field_validator("assets")
    @classmethod
    def sort_unique_assets(cls, value: tuple[AssetCoverage, ...]) -> tuple[AssetCoverage, ...]:
        ids = [item.asset_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("coverage assets must be unique")
        return tuple(sorted(value, key=lambda item: item.asset_id))

    @field_validator("expected_asset_ids")
    @classmethod
    def sort_unique_expected(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("expected coverage assets must be unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def verify_complete_coverage(self) -> EvidenceCoverageReport:
        actual = {item.asset_id for item in self.assets}
        expected = set(self.expected_asset_ids)
        count = sum(item.materialized_source_units for item in self.assets)
        is_complete = (
            actual == expected
            and self.evidence_count > 0
            and all(item.complete for item in self.assets)
        )
        if self.evidence_count != count:
            raise ValueError("coverage evidence count is inconsistent")
        if self.complete != is_complete:
            raise ValueError("coverage completeness is inconsistent")
        return self


class MaterializationReportContent(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    materialization_run_id: str = Field(min_length=1, max_length=64)
    reconciliation_candidate_id: str = Field(min_length=1, max_length=64)
    materializer_name: Literal[MATERIALIZER_NAME] = MATERIALIZER_NAME
    materializer_version: Literal[MATERIALIZER_VERSION] = MATERIALIZER_VERSION
    authority_binding: SignedAuthorityBinding
    structural_mapping: StructuralMappingAttachment
    evidence: tuple[MaterializedEvidenceArtifactEntry | MaterializedEvidenceRecord, ...]
    coverage: EvidenceCoverageReport
    completed_at: datetime
    ready_for_qa: bool
    blockers: tuple[str, ...] = ()

    @field_validator("evidence")
    @classmethod
    def sort_unique_evidence(
        cls,
        value: tuple[MaterializedEvidenceArtifactEntry | MaterializedEvidenceRecord, ...],
    ) -> tuple[MaterializedEvidenceArtifactEntry | MaterializedEvidenceRecord, ...]:
        ids = [
            item.content.evidence_id
            if isinstance(item, MaterializedEvidenceRecord)
            else item.evidence_id
            for item in value
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("materialized evidence IDs must be unique")
        return tuple(
            sorted(
                value,
                key=lambda item: (
                    item.content.evidence_id
                    if isinstance(item, MaterializedEvidenceRecord)
                    else item.evidence_id
                ),
            )
        )

    @field_validator("blockers")
    @classmethod
    def sort_unique_blockers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("materialization blockers must be unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def verify_gate(self) -> MaterializationReportContent:
        ids = {
            item.content.evidence_id
            if isinstance(item, MaterializedEvidenceRecord)
            else item.evidence_id
            for item in self.evidence
        }
        coverage_ids = {
            evidence_id for asset in self.coverage.assets for evidence_id in asset.evidence_ids
        }
        if ids != coverage_ids or len(ids) != self.coverage.evidence_count:
            raise ValueError("coverage and materialized evidence membership differ")
        expected_ready = self.coverage.complete and not self.blockers
        if self.ready_for_qa != expected_ready:
            raise ValueError("materialization QA readiness is inconsistent")
        return self


class MaterializationReport(CanonicalModel):
    content: MaterializationReportContent
    report_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> MaterializationReport:
        if self.report_sha256 != canonical_sha256(self.content):
            raise ValueError("materialization report digest is inconsistent")
        return self

    @classmethod
    def seal(cls, content: MaterializationReportContent) -> MaterializationReport:
        return cls(content=content, report_sha256=canonical_sha256(content))


class CorpusReleaseCandidateContent(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    corpus_release_candidate_id: str = Field(min_length=1, max_length=64)
    reconciliation_candidate_id: str = Field(min_length=1, max_length=64)
    materialization_run_id: str = Field(min_length=1, max_length=64)
    trust_root_sha256: str = Field(pattern=SHA256_PATTERN)
    materialization_report_sha256: str = Field(pattern=SHA256_PATTERN)
    authority_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_entries: tuple[tuple[str, str], ...] = Field(min_length=1)
    created_at: datetime
    qa_required: Literal[True] = True
    retrieval_eligible: Literal[False] = False

    @field_validator("evidence_entries")
    @classmethod
    def sort_unique_entries(cls, value: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        ids = [item[0] for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("corpus candidate evidence IDs must be unique")
        if any(len(item) != 2 for item in value):
            raise ValueError("evidence entries contain ID and SHA-256")
        return tuple(sorted(value))


class SignedCorpusReleaseCandidate(CanonicalModel):
    content: CorpusReleaseCandidateContent
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    attestation: VerifiedAttestationReference

    @model_validator(mode="after")
    def verify_digest_and_attestation(self) -> SignedCorpusReleaseCandidate:
        expected = canonical_sha256(self.content)
        if self.candidate_sha256 != expected:
            raise ValueError("corpus release candidate digest is inconsistent")
        if self.attestation.statement_sha256 != expected:
            raise ValueError("corpus release candidate signature covers different content")
        return self


class MaterializationResult(CanonicalModel):
    state: MaterializationState
    report: MaterializationReport
    corpus_release_candidate: SignedCorpusReleaseCandidate | None = None
    report_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_artifact_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_state(self) -> MaterializationResult:
        ready = self.state is MaterializationState.READY_FOR_QA
        if ready != (self.corpus_release_candidate is not None):
            raise ValueError("only ready materializations produce a corpus candidate")
        if ready != (self.candidate_artifact_sha256 is not None):
            raise ValueError("candidate artifact presence does not match state")
        return self
