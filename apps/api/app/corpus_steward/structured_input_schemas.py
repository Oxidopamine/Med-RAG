"""Contracts for immutable narrative and FHIR dependency input closures."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.corpus_steward.schemas import (
    STEWARD_CONTRACT_VERSION,
    VerifiedAttestationReference,
)
from app.corpus_steward.structured_schemas import FHIRPackageDependency
from app.schemas.corpus import SHA256_PATTERN, canonical_sha256
from app.schemas.domain import CanonicalModel

STRUCTURED_INPUT_RESOLVER_NAME = "authoritative-input-closure"
STRUCTURED_INPUT_RESOLVER_VERSION = "1.2.0"


class StructuredInputState(str, Enum):
    RESOLVED = "RESOLVED"
    BLOCKED = "BLOCKED"


class StructuredInputCheckCode(str, Enum):
    SOURCE_PACKAGE = "SOURCE_PACKAGE"
    NARRATIVE_ASSETS = "NARRATIVE_ASSETS"
    DEPENDENCY_CLOSURE = "DEPENDENCY_CLOSURE"


class StructuredInputCheckOutcome(str, Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"


class NarrativeAssetRole(str, Enum):
    PRIMARY = "PRIMARY"
    ANNEX = "ANNEX"


class NarrativeAssetDefinition(CanonicalModel):
    asset_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=1000)
    role: NarrativeAssetRole
    url: str = Field(min_length=1)
    media_type: str = Field(min_length=1, max_length=200)
    expected_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)


class NarrativeAuthorityDefinition(CanonicalModel):
    link_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=1000)
    url: str = Field(min_length=1)
    identifier: str | None = None
    version: str | None = None
    assets: tuple[NarrativeAssetDefinition, ...] = Field(min_length=1)

    @field_validator("assets")
    @classmethod
    def sort_unique_assets(
        cls, value: tuple[NarrativeAssetDefinition, ...]
    ) -> tuple[NarrativeAssetDefinition, ...]:
        ids = [item.asset_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("narrative asset IDs must be unique within a link")
        if sum(item.role is NarrativeAssetRole.PRIMARY for item in value) != 1:
            raise ValueError("each narrative authority requires exactly one primary asset")
        return tuple(sorted(value, key=lambda item: item.asset_id))


class DependencyRegistryPolicy(CanonicalModel):
    base_url: str = Field(min_length=1)
    allowed_domains: tuple[str, ...] = Field(min_length=1)
    version_url_template: str | None = None
    max_packages: int = Field(ge=1, le=500)
    max_depth: int = Field(ge=1, le=50)
    max_total_bytes: int = Field(ge=1)
    concurrency: int = Field(ge=1, le=16, default=4)

    @field_validator("allowed_domains")
    @classmethod
    def sort_unique_domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(domain.lower().rstrip(".") for domain in value))
        if len(normalized) != len(set(normalized)):
            raise ValueError("dependency registry domains must be unique")
        return normalized

    @field_validator("version_url_template")
    @classmethod
    def validate_version_url_template(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            value.count("{package_id}") != 1
            or value.count("{version}") != 1
        ):
            raise ValueError(
                "dependency version URL template requires package_id and version placeholders"
            )
        return value

    def package_url(self, package_id: str, version: str, fallback: str) -> str:
        if self.version_url_template is None:
            return fallback
        return self.version_url_template.replace("{package_id}", package_id).replace(
            "{version}", version
        )


class FHIRDependencyPackageManifest(CanonicalModel):
    package_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    title: str | None = None
    package_type: str | None = None
    canonical: str | None = None
    fhir_versions: tuple[str, ...] = ()
    license: str | None = None
    dependencies: tuple[FHIRPackageDependency, ...] = ()
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("fhir_versions")
    @classmethod
    def sort_unique_versions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("dependency FHIR versions must be unique")
        return tuple(sorted(value))

    @field_validator("dependencies")
    @classmethod
    def sort_unique_dependencies(
        cls, value: tuple[FHIRPackageDependency, ...]
    ) -> tuple[FHIRPackageDependency, ...]:
        ids = [item.package_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("dependency package manifest IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.package_id))


class DependencyRequirement(CanonicalModel):
    parent_package: str = Field(min_length=1)
    package_id: str = Field(min_length=1)
    version_spec: str = Field(min_length=1)
    resolved_version: str = Field(min_length=1)
    depth: int = Field(ge=1)
    direct: bool

    @property
    def requirement_key(self) -> str:
        return f"{self.parent_package}->{self.package_id}#{self.version_spec}"


class ResolvedDependencyPackage(CanonicalModel):
    package_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    registry_metadata_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    package_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    byte_size: int = Field(gt=0)
    registry_url: str = Field(min_length=1)
    tarball_url: str = Field(min_length=1)
    registry_sha1: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    fetched_at: datetime
    etag: str | None = None
    last_modified: str | None = None
    manifest: FHIRDependencyPackageManifest

    @model_validator(mode="after")
    def verify_identity(self) -> ResolvedDependencyPackage:
        if (
            self.package_id != self.manifest.package_id
            or self.version != self.manifest.version
        ):
            raise ValueError("resolved dependency identity differs from its manifest")
        return self


class ResolvedNarrativeArtifact(CanonicalModel):
    link_id: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    role: NarrativeAssetRole
    configured_url: str = Field(min_length=1)
    final_url: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    byte_size: int = Field(gt=0)
    fetched_at: datetime
    etag: str | None = None
    last_modified: str | None = None


class StructuredInputIssue(CanonicalModel):
    subject: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    details: str = Field(min_length=1)


class StructuredInputCheck(CanonicalModel):
    code: StructuredInputCheckCode
    outcome: StructuredInputCheckOutcome
    details: str = Field(min_length=1)
    diagnostics: dict[str, object] = Field(default_factory=dict)


class StructuredInputClosureContent(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    input_run_id: str = Field(min_length=1)
    reconciliation_candidate_id: str = Field(min_length=1)
    trust_root_id: str = Field(min_length=1)
    trust_root_sha256: str = Field(pattern=SHA256_PATTERN)
    inventory_item_id: str = Field(min_length=1)
    source_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    resolver_name: Literal[STRUCTURED_INPUT_RESOLVER_NAME] = (
        STRUCTURED_INPUT_RESOLVER_NAME
    )
    resolver_version: str = Field(
        default=STRUCTURED_INPUT_RESOLVER_VERSION, min_length=1
    )
    completed_at: datetime
    direct_dependencies: tuple[FHIRPackageDependency, ...] = ()
    requirements: tuple[DependencyRequirement, ...] = ()
    dependency_packages: tuple[ResolvedDependencyPackage, ...] = ()
    dependency_inventory_sha256: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    expected_narrative_asset_ids: tuple[str, ...] = ()
    narrative_artifacts: tuple[ResolvedNarrativeArtifact, ...] = ()
    narrative_inventory_sha256: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    issues: tuple[StructuredInputIssue, ...] = ()
    checks: tuple[StructuredInputCheck, ...]
    complete: bool
    blockers: tuple[str, ...] = ()

    @field_validator(
        "direct_dependencies", "dependency_packages", "requirements", mode="after"
    )
    @classmethod
    def sort_closure_records(cls, value):
        if not value:
            return value
        first = value[0]
        if isinstance(
            first, (FHIRPackageDependency, ResolvedDependencyPackage)
        ):
            keys = [(item.package_id, item.version) for item in value]
        else:
            keys = [item.requirement_key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("structured input closure records must be unique")
        return tuple(item for _, item in sorted(zip(keys, value, strict=True)))

    @field_validator("expected_narrative_asset_ids")
    @classmethod
    def sort_unique_asset_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("expected narrative asset IDs must be unique")
        return tuple(sorted(value))

    @field_validator("narrative_artifacts")
    @classmethod
    def sort_unique_narratives(
        cls, value: tuple[ResolvedNarrativeArtifact, ...]
    ) -> tuple[ResolvedNarrativeArtifact, ...]:
        keys = [(item.link_id, item.asset_id) for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("resolved narrative artifacts must be unique")
        return tuple(item for _, item in sorted(zip(keys, value, strict=True)))

    @field_validator("issues")
    @classmethod
    def sort_unique_issues(
        cls, value: tuple[StructuredInputIssue, ...]
    ) -> tuple[StructuredInputIssue, ...]:
        keys = [(item.subject, item.reason_code) for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("structured input issues must be unique")
        return tuple(item for _, item in sorted(zip(keys, value, strict=True)))

    @field_validator("checks")
    @classmethod
    def sort_unique_checks(
        cls, value: tuple[StructuredInputCheck, ...]
    ) -> tuple[StructuredInputCheck, ...]:
        codes = [item.code for item in value]
        if len(codes) != len(set(codes)):
            raise ValueError("structured input checks must be unique")
        return tuple(sorted(value, key=lambda item: item.code.value))

    @field_validator("blockers")
    @classmethod
    def sort_unique_blockers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("structured input blockers must be unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def verify_closure(self) -> StructuredInputClosureContent:
        blocked = any(
            check.outcome is StructuredInputCheckOutcome.BLOCK
            for check in self.checks
        )
        if self.complete != (not blocked and not self.blockers and not self.issues):
            raise ValueError("input closure completeness must match its checks and issues")
        dependency_payload = [
            item.model_dump(mode="json") for item in self.dependency_packages
        ]
        expected_dependency_digest = (
            canonical_sha256({"dependencies": dependency_payload})
            if dependency_payload
            else None
        )
        if self.dependency_inventory_sha256 != expected_dependency_digest:
            raise ValueError("dependency inventory digest is inconsistent")
        narrative_payload = [
            item.model_dump(mode="json") for item in self.narrative_artifacts
        ]
        expected_narrative_digest = (
            canonical_sha256({"narratives": narrative_payload})
            if narrative_payload
            else None
        )
        if self.narrative_inventory_sha256 != expected_narrative_digest:
            raise ValueError("narrative inventory digest is inconsistent")
        return self


class StructuredInputClosureReport(CanonicalModel):
    content: StructuredInputClosureContent
    report_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> StructuredInputClosureReport:
        if self.report_sha256 != canonical_sha256(self.content):
            raise ValueError("structured input report digest is inconsistent")
        return self

    @classmethod
    def seal(
        cls, content: StructuredInputClosureContent
    ) -> StructuredInputClosureReport:
        return cls(content=content, report_sha256=canonical_sha256(content))


class StructuredInputAttestationContent(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    input_run_id: str = Field(min_length=1)
    reconciliation_candidate_id: str = Field(min_length=1)
    trust_root_id: str = Field(min_length=1)
    trust_root_sha256: str = Field(pattern=SHA256_PATTERN)
    inventory_item_id: str = Field(min_length=1)
    source_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    resolver_name: Literal[STRUCTURED_INPUT_RESOLVER_NAME] = (
        STRUCTURED_INPUT_RESOLVER_NAME
    )
    resolver_version: str = Field(
        default=STRUCTURED_INPUT_RESOLVER_VERSION, min_length=1
    )
    report_sha256: str = Field(pattern=SHA256_PATTERN)
    completed_at: datetime


class StructuredInputResolutionResult(CanonicalModel):
    state: StructuredInputState
    report: StructuredInputClosureReport
    attestation: VerifiedAttestationReference
    report_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
