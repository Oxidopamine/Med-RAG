"""Contracts for safe processing of authoritative structured source packages."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.corpus_steward.schemas import (
    STEWARD_CONTRACT_VERSION,
    VerifiedAttestationReference,
)
from app.schemas.corpus import SHA256_PATTERN, canonical_sha256
from app.schemas.domain import CanonicalModel

FHIR_PACKAGE_PROCESSOR_NAME = "fhir-package-native"
FHIR_PACKAGE_PROCESSOR_VERSION = "1.1.0"


class StructuredRunState(str, Enum):
    VALIDATED = "VALIDATED"
    BLOCKED = "BLOCKED"


class StructuredCheckOutcome(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"


class StructuredCheckCode(str, Enum):
    ARCHIVE_SAFETY = "ARCHIVE_SAFETY"
    PACKAGE_MANIFEST = "PACKAGE_MANIFEST"
    IMPLEMENTATION_GUIDE_BINDING = "IMPLEMENTATION_GUIDE_BINDING"
    RESOURCE_IDENTITY = "RESOURCE_IDENTITY"
    DECLARED_RESOURCE_COVERAGE = "DECLARED_RESOURCE_COVERAGE"
    RESOURCE_NARRATIVE_COVERAGE = "RESOURCE_NARRATIVE_COVERAGE"
    FHIR_VERSION_POLICY = "FHIR_VERSION_POLICY"
    LIFECYCLE_POLICY = "LIFECYCLE_POLICY"
    LICENSE_POLICY = "LICENSE_POLICY"
    NARRATIVE_AUTHORITY = "NARRATIVE_AUTHORITY"
    NARRATIVE_ARTIFACT_COVERAGE = "NARRATIVE_ARTIFACT_COVERAGE"
    DEPENDENCY_COVERAGE = "DEPENDENCY_COVERAGE"


class NarrativeLinkStatus(str, Enum):
    DECLARED_IN_PACKAGE = "DECLARED_IN_PACKAGE"
    CONFIGURED_NOT_DECLARED = "CONFIGURED_NOT_DECLARED"


class FHIRPackageDependency(CanonicalModel):
    package_id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class FHIRPackageManifest(CanonicalModel):
    package_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    canonical: str = Field(min_length=1)
    title: str = Field(min_length=1)
    package_type: str = Field(min_length=1)
    fhir_versions: tuple[str, ...] = Field(min_length=1)
    license: str = Field(min_length=1)
    dependencies: tuple[FHIRPackageDependency, ...] = ()
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("fhir_versions")
    @classmethod
    def sort_unique_versions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("FHIR versions must be unique")
        return tuple(sorted(value))

    @field_validator("dependencies")
    @classmethod
    def sort_unique_dependencies(
        cls, value: tuple[FHIRPackageDependency, ...]
    ) -> tuple[FHIRPackageDependency, ...]:
        ids = [item.package_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("FHIR package dependencies must be unique")
        return tuple(sorted(value, key=lambda item: item.package_id))


class FHIRImplementationGuideMetadata(CanonicalModel):
    resource_id: str = Field(min_length=1)
    package_id: str = Field(min_length=1)
    canonical_url: str = Field(min_length=1)
    version: str = Field(min_length=1)
    name: str = Field(min_length=1)
    title: str = Field(min_length=1)
    status: str = Field(min_length=1)
    experimental: bool
    publisher: str = Field(min_length=1)
    license: str = Field(min_length=1)
    fhir_versions: tuple[str, ...] = Field(min_length=1)
    profiles: tuple[str, ...] = ()
    declared_resource_count: int = Field(ge=0)
    content_sha256: str = Field(pattern=SHA256_PATTERN)


class FHIRResourceIndexEntry(CanonicalModel):
    resource_key: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    logical_reference: str | None = None
    canonical_url: str | None = None
    version: str | None = None
    status: str | None = None
    experimental: bool | None = None
    profiles: tuple[str, ...] = ()
    member_path: str = Field(min_length=1)
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    byte_size: int = Field(gt=0)
    narrative_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    declared_in_implementation_guide: bool
    is_example: bool


class NarrativeAuthorityLink(CanonicalModel):
    link_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    identifier: str | None = None
    version: str | None = None
    status: NarrativeLinkStatus


class StructuredValidationCheck(CanonicalModel):
    code: StructuredCheckCode
    outcome: StructuredCheckOutcome
    details: str = Field(min_length=1)
    diagnostics: dict[str, object] = Field(default_factory=dict)


class StructuredPackageReportContent(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    structured_run_id: str = Field(min_length=1)
    reconciliation_candidate_id: str = Field(min_length=1)
    trust_root_id: str = Field(min_length=1)
    trust_root_sha256: str = Field(pattern=SHA256_PATTERN)
    inventory_item_id: str = Field(min_length=1)
    source_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    structured_input_run_id: str | None = None
    input_closure_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    processor_name: Literal[FHIR_PACKAGE_PROCESSOR_NAME] = FHIR_PACKAGE_PROCESSOR_NAME
    processor_version: str = Field(
        default=FHIR_PACKAGE_PROCESSOR_VERSION, min_length=1
    )
    processed_at: datetime
    package_manifest: FHIRPackageManifest | None = None
    implementation_guide: FHIRImplementationGuideMetadata | None = None
    resource_inventory_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    resource_count: int = Field(ge=0)
    example_count: int = Field(ge=0)
    narrative_count: int = Field(ge=0)
    resolved_dependency_count: int = Field(ge=0, default=0)
    narrative_artifact_count: int = Field(ge=0, default=0)
    resource_type_counts: dict[str, int] = Field(default_factory=dict)
    narrative_authorities: tuple[NarrativeAuthorityLink, ...] = ()
    checks: tuple[StructuredValidationCheck, ...]
    promotion_eligible: bool
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @field_validator("narrative_authorities")
    @classmethod
    def sort_unique_narrative_authorities(
        cls, value: tuple[NarrativeAuthorityLink, ...]
    ) -> tuple[NarrativeAuthorityLink, ...]:
        ids = [item.link_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("controlling narrative link IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.link_id))

    @field_validator("blockers", "warnings")
    @classmethod
    def sort_unique_diagnostics(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("structured report diagnostics must be unique")
        return tuple(sorted(value))

    @field_validator("checks")
    @classmethod
    def require_unique_checks(
        cls, value: tuple[StructuredValidationCheck, ...]
    ) -> tuple[StructuredValidationCheck, ...]:
        codes = [item.code for item in value]
        if len(codes) != len(set(codes)):
            raise ValueError("structured validation checks must be unique")
        return tuple(sorted(value, key=lambda item: item.code.value))

    @model_validator(mode="after")
    def verify_gate(self) -> StructuredPackageReportContent:
        blocking_checks = any(
            check.outcome is StructuredCheckOutcome.BLOCK for check in self.checks
        )
        if self.promotion_eligible != (not blocking_checks and not self.blockers):
            raise ValueError("promotion eligibility must match deterministic blockers")
        if self.resource_count == 0 and self.resource_inventory_sha256 is not None:
            raise ValueError("empty resource inventories cannot declare a digest")
        if self.resource_count > 0 and self.resource_inventory_sha256 is None:
            raise ValueError("non-empty resource inventories require a digest")
        if not 0 <= self.example_count <= self.resource_count:
            raise ValueError("example count cannot exceed resource count")
        if not 0 <= self.narrative_count <= self.resource_count:
            raise ValueError("narrative count cannot exceed resource count")
        if any(count < 1 for count in self.resource_type_counts.values()):
            raise ValueError("resource type counts must be positive")
        if sum(self.resource_type_counts.values()) != self.resource_count:
            raise ValueError("resource type counts must sum to resource count")
        if (self.structured_input_run_id is None) != (
            self.input_closure_sha256 is None
        ):
            raise ValueError("structured input run and closure digest must be bound together")
        return self


class StructuredPackageReport(CanonicalModel):
    content: StructuredPackageReportContent
    report_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> StructuredPackageReport:
        if self.report_sha256 != canonical_sha256(self.content):
            raise ValueError("structured report digest does not match canonical content")
        return self

    @classmethod
    def seal(cls, content: StructuredPackageReportContent) -> StructuredPackageReport:
        return cls(content=content, report_sha256=canonical_sha256(content))


class StructuredStageAttestationContent(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    structured_run_id: str = Field(min_length=1)
    reconciliation_candidate_id: str = Field(min_length=1)
    trust_root_id: str = Field(min_length=1)
    trust_root_sha256: str = Field(pattern=SHA256_PATTERN)
    inventory_item_id: str = Field(min_length=1)
    source_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    structured_input_run_id: str | None = None
    input_closure_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    processor_name: Literal[FHIR_PACKAGE_PROCESSOR_NAME] = FHIR_PACKAGE_PROCESSOR_NAME
    processor_version: str = Field(
        default=FHIR_PACKAGE_PROCESSOR_VERSION, min_length=1
    )
    report_sha256: str = Field(pattern=SHA256_PATTERN)
    completed_at: datetime

    @model_validator(mode="after")
    def verify_input_binding(self) -> StructuredStageAttestationContent:
        if (self.structured_input_run_id is None) != (
            self.input_closure_sha256 is None
        ):
            raise ValueError("attestation input run and closure digest must be bound together")
        return self


class StructuredProcessingResult(CanonicalModel):
    state: StructuredRunState
    report: StructuredPackageReport
    attestation: VerifiedAttestationReference
    report_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
