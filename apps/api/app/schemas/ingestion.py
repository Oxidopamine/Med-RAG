from datetime import date, datetime
from typing import Literal

from pydantic import Field, HttpUrl, field_validator, model_validator

from app.schemas.domain import CanonicalModel, EvidenceTrustStatus, SourceClass, SourceStatus


def _normalize_domain(value: str) -> str:
    candidate = value.strip().lower().rstrip(".")
    if "://" in candidate or "/" in candidate or "@" in candidate or ":" in candidate:
        raise ValueError("allowed domains must be hostnames without a URL scheme, path, or port")
    try:
        ascii_domain = candidate.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("allowed domain is not a valid IDNA hostname") from error
    labels = ascii_domain.split(".")
    if len(labels) < 2 or any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not all(character.isalnum() or character == "-" for character in label)
        for label in labels
    ):
        raise ValueError("allowed domain must be a valid fully qualified hostname")
    return ascii_domain


class PublisherCreate(CanonicalModel):
    name: str = Field(min_length=2, max_length=300)
    allowed_domains: frozenset[str] = Field(min_length=1)

    @field_validator("allowed_domains", mode="before")
    @classmethod
    def normalize_domains(cls, value: object) -> object:
        if isinstance(value, (list, tuple, set, frozenset)):
            return frozenset(_normalize_domain(str(domain)) for domain in value)
        return value


class PublisherRecord(CanonicalModel):
    publisher_id: str
    name: str
    allowed_domains: frozenset[str]
    created_at: datetime


class SourceCreate(CanonicalModel):
    publisher_id: str = Field(min_length=1)
    title: str = Field(min_length=2, max_length=1000)
    source_class: SourceClass
    jurisdiction: str = Field(min_length=2, max_length=32)
    canonical_url: HttpUrl
    license_excerpt_allowed: bool = False
    license_render_allowed: bool = False


class SourceRecord(CanonicalModel):
    source_id: str
    publisher_id: str
    title: str
    source_class: SourceClass
    jurisdiction: str
    canonical_url: HttpUrl
    license_excerpt_allowed: bool
    license_render_allowed: bool
    created_at: datetime


class SourceVersionCreate(CanonicalModel):
    source_id: str = Field(min_length=1)
    version_label: str = Field(min_length=1, max_length=200)
    effective_from: date | None = None
    effective_to: date | None = None

    @model_validator(mode="after")
    def validate_effective_range(self) -> "SourceVersionCreate":
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_from > self.effective_to
        ):
            raise ValueError("effective_from cannot be after effective_to")
        return self


class SourceVersionRecord(CanonicalModel):
    source_version_id: str
    source_id: str
    version_label: str
    status: SourceStatus
    effective_from: date | None = None
    effective_to: date | None = None
    approved_for_retrieval: bool
    created_at: datetime
    updated_at: datetime


class AcquisitionCreate(CanonicalModel):
    url: HttpUrl
    expected_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")

    @field_validator("expected_sha256")
    @classmethod
    def normalize_sha256(cls, value: str | None) -> str | None:
        return value.lower() if value is not None else None


class ArtifactRecord(CanonicalModel):
    artifact_id: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_size: int = Field(gt=0)
    media_type: Literal["application/pdf"] = "application/pdf"
    storage_key: str
    requested_url: HttpUrl
    final_url: HttpUrl
    publisher_domain: str
    acquired_at: datetime


class ExtractionTrustRecord(CanonicalModel):
    extraction_run_id: str
    extractor_name: str
    extractor_version: str
    trust_status: EvidenceTrustStatus
    page_count: int = Field(ge=1)
    span_count: int = Field(ge=0)
    diagnostics: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class QuarantineRecord(CanonicalModel):
    quarantine_event_id: str
    source_version_id: str
    acquisition_url: HttpUrl | None = None
    reason_code: str
    details: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution_note: str | None = None


class AcquisitionOutcome(CanonicalModel):
    source_version_id: str
    status: SourceStatus
    artifact: ArtifactRecord | None = None
    extraction: ExtractionTrustRecord | None = None
    quarantine: QuarantineRecord | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "AcquisitionOutcome":
        if self.status is SourceStatus.QUARANTINED and self.quarantine is None:
            raise ValueError("a quarantined acquisition requires a quarantine record")
        if self.status is not SourceStatus.QUARANTINED and (
            self.artifact is None or self.extraction is None
        ):
            raise ValueError(
                "a successful acquisition requires artifact and extraction records"
            )
        return self


class QuarantineResolution(CanonicalModel):
    resolved_by: str = Field(min_length=2, max_length=200)
    note: str = Field(min_length=5, max_length=2000)
