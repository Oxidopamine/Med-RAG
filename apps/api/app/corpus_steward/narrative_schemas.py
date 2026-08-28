"""Contracts for safe analysis of authoritative narrative source documents.

The peer of `structured_schemas` for the narrative topology. A DAK candidate's source
artifact is a FHIR package whose interior is enumerable as resources; a narrative
candidate's source artifact is a guideline PDF whose interior is enumerable as *source
units* - pages, with anchors. Both stages answer the same question before materialization
is allowed to run: what is inside these bytes, according to an independent processor, and
does the publisher's own declaration agree with the operator's policy?

## This stage is a census, not an extractor

`NarrativeDocumentAnalysis` carries `unit_count` and `unit_inventory_sha256` - the digest
of the ordered `(source_unit_id, sha256(content_exact))` pairs - and **no clinical text**.
Materialization later recomputes that inventory from the bytes it actually extracts and
must agree with the digest signed here. Without that recomputation the report is a rubber
stamp; with it, the relationship is exactly the one the FHIR path already has, where
`resource_inventory_sha256` is an independent prior statement the later stage is checked
against.

## What the checks preserve from the structured stage

`docs/narrative-only-materialization.md` names four jobs the structured report does. Three
have direct analogues here and are carried by the check codes below:

* `DOCUMENT_SAFETY` and `DOCUMENT_IDENTITY` - an independent, signed statement about the
  interior of the acquired bytes, produced under a declared processor name and version.
* `UNIT_COVERAGE` - the enumerable unit census, digest-bound.
* `LICENSE_POLICY` - the publisher-side cross-check. A WHO guideline PDF carries its
  licence in XMP `dc:rights` / `xmpRights:WebStatement`, so the operator's per-asset policy
  is checked against the document's own declaration rather than against a stub.
* `NARRATIVE_AUTHORITY` - clinical content may be promoted only from an asset the trust
  root licenses for evidence materialization.

The fourth job - the `STRUCTURAL_MAPPING_ONLY` role boundary - has no analogue and must not
be faked. In the DAK topology the structural asset is not the clinical authority, so the
boundary runs between two artifacts. Here one artifact is both, and the boundary that
carries the safety property is `NARRATIVE_AUTHORITY`, which is unaffected.
"""

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

NARRATIVE_PROCESSOR_NAME = "narrative-document-native"
NARRATIVE_PROCESSOR_VERSION = "1.0.0"


class NarrativeRunState(str, Enum):
    VALIDATED = "VALIDATED"
    BLOCKED = "BLOCKED"


class NarrativeCheckOutcome(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"


class NarrativeCheckCode(str, Enum):
    DOCUMENT_SAFETY = "DOCUMENT_SAFETY"
    DOCUMENT_IDENTITY = "DOCUMENT_IDENTITY"
    UNIT_COVERAGE = "UNIT_COVERAGE"
    LICENSE_POLICY = "LICENSE_POLICY"
    NARRATIVE_AUTHORITY = "NARRATIVE_AUTHORITY"


class NarrativeDocumentDeclaration(CanonicalModel):
    """What the document says about itself, read without extracting clinical content.

    The licence cross-check analogue. `declared_license_id` is read from XMP
    `dc:rights` / `xmpRights:WebStatement` where present; a document that declares nothing
    leaves it null, which is a WARN rather than a BLOCK - silence is not a contradiction.
    """

    pdf_version: str | None = None
    encrypted: bool = False
    has_embedded_files: bool = False
    has_javascript: bool = False
    page_count: int = Field(ge=0)
    title: str | None = None
    producer: str | None = None
    creation_date: str | None = None
    declared_license_id: str | None = None
    declared_license_statement: str | None = None


class NarrativeDocumentAnalysis(CanonicalModel):
    """Structure and identity of one narrative asset. No clinical text."""

    asset_id: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    media_type: str = Field(min_length=1)
    byte_size: int = Field(gt=0)
    unit_count: int = Field(ge=0)
    unit_inventory_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    declaration: NarrativeDocumentDeclaration

    @model_validator(mode="after")
    def require_inventory_digest_for_units(self) -> NarrativeDocumentAnalysis:
        if self.unit_count == 0 and self.unit_inventory_sha256 is not None:
            raise ValueError("empty unit inventories cannot declare a digest")
        if self.unit_count > 0 and self.unit_inventory_sha256 is None:
            raise ValueError("non-empty unit inventories require a digest")
        return self


class NarrativeValidationCheck(CanonicalModel):
    code: NarrativeCheckCode
    outcome: NarrativeCheckOutcome
    summary: str = Field(min_length=1)
    details: dict[str, object] = Field(default_factory=dict)


class NarrativeAnalysisReportContent(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    narrative_run_id: str = Field(min_length=1)
    reconciliation_candidate_id: str = Field(min_length=1)
    trust_root_id: str = Field(min_length=1)
    trust_root_sha256: str = Field(pattern=SHA256_PATTERN)
    inventory_item_id: str = Field(min_length=1)
    source_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    structured_input_run_id: str | None = None
    input_closure_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    processor_name: Literal[NARRATIVE_PROCESSOR_NAME] = NARRATIVE_PROCESSOR_NAME
    processor_version: str = Field(default=NARRATIVE_PROCESSOR_VERSION, min_length=1)
    processed_at: datetime
    documents: tuple[NarrativeDocumentAnalysis, ...] = ()
    unit_count_total: int = Field(ge=0)
    checks: tuple[NarrativeValidationCheck, ...]
    promotion_eligible: bool
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @field_validator("documents")
    @classmethod
    def sort_unique_documents(
        cls, value: tuple[NarrativeDocumentAnalysis, ...]
    ) -> tuple[NarrativeDocumentAnalysis, ...]:
        ids = [item.asset_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("narrative document asset IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.asset_id))

    @field_validator("blockers", "warnings")
    @classmethod
    def sort_unique_diagnostics(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("narrative report diagnostics must be unique")
        return tuple(sorted(value))

    @field_validator("checks")
    @classmethod
    def require_unique_checks(
        cls, value: tuple[NarrativeValidationCheck, ...]
    ) -> tuple[NarrativeValidationCheck, ...]:
        codes = [item.code for item in value]
        if len(codes) != len(set(codes)):
            raise ValueError("narrative validation checks must be unique")
        return tuple(sorted(value, key=lambda item: item.code.value))

    @model_validator(mode="after")
    def verify_gate(self) -> NarrativeAnalysisReportContent:
        blocking = any(
            check.outcome is NarrativeCheckOutcome.BLOCK for check in self.checks
        )
        # A report that ran no checks must not be promotable. Without this, `checks=()`
        # satisfies "no BLOCK outcome" vacuously and a report asserting that document
        # safety, licence policy and narrative authority all passed could be sealed having
        # run none of them - fail-open, in a pipeline whose whole contract is fail-closed.
        missing = tuple(
            code.value for code in NarrativeCheckCode
            if code not in {check.code for check in self.checks}
        )
        if self.promotion_eligible and missing:
            raise ValueError(
                "promotion eligibility requires every declared check to have run; "
                f"missing {sorted(missing)}"
            )
        if self.promotion_eligible != (not blocking and not self.blockers):
            raise ValueError("promotion eligibility must match deterministic blockers")
        if self.unit_count_total != sum(item.unit_count for item in self.documents):
            raise ValueError("unit_count_total must equal the sum of document unit counts")
        if (self.structured_input_run_id is None) != (self.input_closure_sha256 is None):
            raise ValueError("narrative input run and closure digest must be bound together")
        return self


class NarrativeAnalysisReport(CanonicalModel):
    content: NarrativeAnalysisReportContent
    report_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_digest(self) -> NarrativeAnalysisReport:
        if self.report_sha256 != canonical_sha256(self.content):
            raise ValueError("narrative report digest does not match canonical content")
        return self

    @classmethod
    def seal(cls, content: NarrativeAnalysisReportContent) -> NarrativeAnalysisReport:
        return cls(content=content, report_sha256=canonical_sha256(content))


class NarrativeStageAttestationContent(CanonicalModel):
    schema_version: Literal[STEWARD_CONTRACT_VERSION] = STEWARD_CONTRACT_VERSION
    narrative_run_id: str = Field(min_length=1)
    reconciliation_candidate_id: str = Field(min_length=1)
    trust_root_id: str = Field(min_length=1)
    trust_root_sha256: str = Field(pattern=SHA256_PATTERN)
    inventory_item_id: str = Field(min_length=1)
    source_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    structured_input_run_id: str | None = None
    input_closure_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    processor_name: Literal[NARRATIVE_PROCESSOR_NAME] = NARRATIVE_PROCESSOR_NAME
    processor_version: str = Field(default=NARRATIVE_PROCESSOR_VERSION, min_length=1)
    report_sha256: str = Field(pattern=SHA256_PATTERN)
    completed_at: datetime

    @model_validator(mode="after")
    def verify_input_binding(self) -> NarrativeStageAttestationContent:
        if (self.structured_input_run_id is None) != (self.input_closure_sha256 is None):
            raise ValueError("attestation input run and closure digest must be bound together")
        return self


class NarrativeAnalysisResult(CanonicalModel):
    state: NarrativeRunState
    report: NarrativeAnalysisReport
    attestation: VerifiedAttestationReference
    report_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
