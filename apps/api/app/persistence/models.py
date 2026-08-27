from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PublisherRow(Base):
    __tablename__ = "publishers"

    publisher_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublisherDomainRow(Base):
    __tablename__ = "publisher_domains"

    domain: Mapped[str] = mapped_column(String(253), primary_key=True)
    publisher_id: Mapped[str] = mapped_column(
        ForeignKey("publishers.publisher_id", ondelete="CASCADE"), nullable=False, index=True
    )


class SourceRow(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(
            "source_class IN ('E1', 'E2', 'E3', 'E4', 'E5', 'E6', 'E7')",
            name="ck_sources_source_class",
        ),
    )

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    publisher_id: Mapped[str] = mapped_column(
        ForeignKey("publishers.publisher_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_class: Mapped[str] = mapped_column(String(8), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    license_render_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceVersionRow(Base):
    __tablename__ = "source_versions"
    __table_args__ = (
        UniqueConstraint("source_id", "version_label"),
        CheckConstraint(
            "status IN ('DISCOVERED', 'DOWNLOADED', 'PARSING', 'QA_REQUIRED', "
            "'APPROVED', 'EFFECTIVE', 'PARTIALLY_SUPERSEDED', 'SUPERSEDED', "
            "'WITHDRAWN', 'ARCHIVED', 'REJECTED', 'QUARANTINED')",
            name="ck_source_versions_status",
        ),
        CheckConstraint(
            "effective_from IS NULL OR effective_to IS NULL OR effective_from <= effective_to",
            name="ck_source_versions_effective_range",
        ),
        CheckConstraint(
            "approved_for_retrieval = false OR status IN "
            "('APPROVED', 'EFFECTIVE', 'PARTIALLY_SUPERSEDED')",
            name="ck_source_versions_approval_status",
        ),
    )

    source_version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.source_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version_label: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    approved_for_retrieval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceRelationshipRow(Base):
    __tablename__ = "source_relationships"
    __table_args__ = (
        CheckConstraint(
            "from_source_version_id <> to_source_version_id",
            name="ck_source_relationships_distinct_versions",
        ),
        CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to",
            name="ck_source_relationships_valid_range",
        ),
    )

    source_relationship_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    from_source_version_id: Mapped[str] = mapped_column(
        ForeignKey("source_versions.source_version_id", ondelete="RESTRICT"), nullable=False
    )
    to_source_version_id: Mapped[str] = mapped_column(
        ForeignKey("source_versions.source_version_id", ondelete="RESTRICT"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(String(32), nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    affected_section_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    affected_recommendation_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    affected_evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    relationship_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ArtifactRow(Base):
    __tablename__ = "source_artifacts"
    __table_args__ = (
        CheckConstraint("length(sha256) = 64", name="ck_source_artifacts_sha256_length"),
        CheckConstraint("byte_size > 0", name="ck_source_artifacts_byte_size"),
        CheckConstraint("media_type = 'application/pdf'", name="ck_source_artifacts_media_type"),
    )

    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AcquisitionRow(Base):
    __tablename__ = "source_acquisitions"
    __table_args__ = (
        CheckConstraint(
            "expected_sha256 IS NULL OR length(expected_sha256) = 64",
            name="ck_source_acquisitions_expected_sha256_length",
        ),
    )

    acquisition_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_version_id: Mapped[str] = mapped_column(
        ForeignKey("source_versions.source_version_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("source_artifacts.artifact_id", ondelete="RESTRICT"), nullable=False
    )
    requested_url: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str] = mapped_column(Text, nullable=False)
    publisher_domain: Mapped[str] = mapped_column(String(253), nullable=False)
    expected_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    http_etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_last_modified: Mapped[str | None] = mapped_column(Text, nullable=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExtractionRunRow(Base):
    __tablename__ = "extraction_runs"
    __table_args__ = (
        CheckConstraint(
            "trust_status IN ('UNVERIFIED', 'VERIFIED_NATIVE', "
            "'VERIFIED_CROSS_PARSER', 'HUMAN_VERIFIED', 'SUSPECT', 'QUARANTINED')",
            name="ck_extraction_runs_trust_status",
        ),
        CheckConstraint("page_count >= 1", name="ck_extraction_runs_page_count"),
        CheckConstraint("span_count >= 0", name="ck_extraction_runs_span_count"),
    )

    extraction_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    acquisition_id: Mapped[str] = mapped_column(
        ForeignKey("source_acquisitions.acquisition_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    extractor_name: Mapped[str] = mapped_column(String(100), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(100), nullable=False)
    trust_status: Mapped[str] = mapped_column(String(32), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    span_count: Mapped[int] = mapped_column(Integer, nullable=False)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExtractedSpanRow(Base):
    __tablename__ = "extracted_spans"
    __table_args__ = (
        UniqueConstraint(
            "extraction_run_id", "pdf_page", "block_index", "line_index", "span_index"
        ),
        CheckConstraint("pdf_page >= 1", name="ck_extracted_spans_pdf_page"),
        CheckConstraint(
            "bbox_right > bbox_left AND bbox_bottom > bbox_top",
            name="ck_extracted_spans_bbox",
        ),
    )

    extracted_span_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    extraction_run_id: Mapped[str] = mapped_column(
        ForeignKey("extraction_runs.extraction_run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pdf_page: Mapped[int] = mapped_column(Integer, nullable=False)
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    line_index: Mapped[int] = mapped_column(Integer, nullable=False)
    span_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text_exact: Mapped[str] = mapped_column(Text, nullable=False)
    bbox_left: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_top: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_right: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_bottom: Mapped[float] = mapped_column(Float, nullable=False)
    font_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    font_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    font_flags: Mapped[int | None] = mapped_column(Integer, nullable=True)


class QuarantineEventRow(Base):
    __tablename__ = "quarantine_events"
    __table_args__ = (
        CheckConstraint(
            "(resolved_at IS NULL AND resolved_by IS NULL AND resolution_note IS NULL) OR "
            "(resolved_at IS NOT NULL AND resolved_by IS NOT NULL "
            "AND resolution_note IS NOT NULL)",
            name="ck_quarantine_events_resolution",
        ),
    )

    quarantine_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_version_id: Mapped[str] = mapped_column(
        ForeignKey("source_versions.source_version_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    acquisition_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class CorpusReleaseRow(Base):
    __tablename__ = "corpus_releases"
    __table_args__ = (
        CheckConstraint(
            "state IN ('CANDIDATE', 'VALIDATED', 'ACTIVE', 'SUPERSEDED', 'REJECTED')",
            name="ck_corpus_releases_state",
        ),
        CheckConstraint(
            "index_status IN ('NOT_BUILT', 'VALIDATED')",
            name="ck_corpus_releases_index_status",
        ),
        CheckConstraint(
            "length(manifest_sha256) = 64",
            name="ck_corpus_releases_manifest_sha256_length",
        ),
        CheckConstraint(
            "index_point_count IS NULL OR index_point_count >= 0",
            name="ck_corpus_releases_index_point_count",
        ),
        CheckConstraint(
            "(index_status = 'NOT_BUILT' AND index_validated_at IS NULL "
            "AND index_attestation_sha256 IS NULL) OR "
            "(index_status = 'VALIDATED' AND index_validated_at IS NOT NULL "
            "AND length(index_attestation_sha256) = 64)",
            name="ck_corpus_releases_index_validation",
        ),
        CheckConstraint(
            "state NOT IN ('ACTIVE', 'SUPERSEDED') OR "
            "(activated_at IS NOT NULL AND activation_decision_sha256 IS NOT NULL)",
            name="ck_corpus_releases_activation",
        ),
        CheckConstraint(
            "activation_decision_sha256 IS NULL OR length(activation_decision_sha256) = 64",
            name="ck_corpus_releases_activation_digest",
        ),
        CheckConstraint(
            "(benchmark_acceptance_sha256 IS NULL AND benchmark_accepted_at IS NULL "
            "AND benchmark_valid_until IS NULL) OR "
            "(length(benchmark_acceptance_sha256) = 64 "
            "AND benchmark_accepted_at IS NOT NULL AND benchmark_valid_until IS NOT NULL)",
            name="ck_corpus_releases_benchmark_acceptance",
        ),
    )

    corpus_release_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_release_id: Mapped[str | None] = mapped_column(
        ForeignKey("corpus_releases.corpus_release_id", ondelete="RESTRICT"), nullable=True
    )
    qdrant_collection: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    index_status: Mapped[str] = mapped_column(String(32), nullable=False)
    index_point_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    index_attestation_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    index_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    benchmark_acceptance_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    benchmark_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    benchmark_valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_by: Mapped[str | None] = mapped_column(String(300), nullable=True)
    activation_decision_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BenchmarkAcceptanceRow(Base):
    __tablename__ = "benchmark_acceptances"
    __table_args__ = (
        CheckConstraint(
            "length(statement_sha256) = 64 AND length(signature_sha256) = 64 "
            "AND length(benchmark_suite_sha256) = 64 "
            "AND length(benchmark_report_sha256) = 64 "
            "AND length(candidate_configuration_sha256) = 64 "
            "AND length(manifest_sha256) = 64 "
            "AND length(vector_batch_sha256) = 64 "
            "AND length(index_attestation_sha256) = 64",
            name="ck_benchmark_acceptance_digests",
        ),
        CheckConstraint(
            "valid_until > accepted_at",
            name="ck_benchmark_acceptance_window",
        ),
    )

    acceptance_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    statement_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    signature_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    corpus_release_id: Mapped[str] = mapped_column(
        ForeignKey("corpus_releases.corpus_release_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    benchmark_suite_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    benchmark_report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_configuration_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    vector_batch_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    qdrant_collection: Mapped[str] = mapped_column(String(255), nullable=False)
    index_attestation_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    runner_version: Mapped[str] = mapped_column(String(100), nullable=False)
    signing_key_id: Mapped[str] = mapped_column(String(300), nullable=False)
    signer_identity: Mapped[str] = mapped_column(String(300), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CanonicalEvidenceRow(Base):
    __tablename__ = "canonical_evidence"
    __table_args__ = (
        CheckConstraint(
            "length(evidence_sha256) = 64",
            name="ck_canonical_evidence_sha256_length",
        ),
        CheckConstraint(
            "approval_status IN ('APPROVED', 'QUARANTINED')",
            name="ck_canonical_evidence_approval_status",
        ),
    )

    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.source_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_version_id: Mapped[str] = mapped_column(
        ForeignKey("source_versions.source_version_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    approval_status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CorpusReleaseEvidenceRow(Base):
    __tablename__ = "corpus_release_evidence"

    corpus_release_id: Mapped[str] = mapped_column(
        ForeignKey("corpus_releases.corpus_release_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("canonical_evidence.evidence_id", ondelete="RESTRICT"),
        primary_key=True,
    )


class CorpusReleaseExceptionRow(Base):
    __tablename__ = "corpus_release_exceptions"
    __table_args__ = (
        CheckConstraint(
            "length(statement_sha256) = 64 AND length(signature_sha256) = 64",
            name="ck_corpus_release_exceptions_digests",
        ),
        CheckConstraint(
            "reason IN ('ACCESS', 'LICENSING', 'ACQUISITION', 'VALIDATION')",
            name="ck_corpus_release_exceptions_reason",
        ),
    )

    exception_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    corpus_release_id: Mapped[str] = mapped_column(
        ForeignKey("corpus_releases.corpus_release_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    trust_root_id: Mapped[str] = mapped_column(String(128), nullable=False)
    inventory_item_id: Mapped[str] = mapped_column(String(512), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    statement_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ActiveCorpusReleaseRow(Base):
    __tablename__ = "active_corpus_release"
    __table_args__ = (
        CheckConstraint("singleton_key = 1", name="ck_active_corpus_release_singleton"),
        CheckConstraint(
            "length(manifest_sha256) = 64",
            name="ck_active_corpus_release_manifest_sha256_length",
        ),
    )

    singleton_key: Mapped[int] = mapped_column(Integer, primary_key=True)
    corpus_release_id: Mapped[str] = mapped_column(
        ForeignKey("corpus_releases.corpus_release_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_by: Mapped[str] = mapped_column(String(300), nullable=False)


class OutboxEventRow(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (UniqueConstraint("deduplication_key"),)

    outbox_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(300), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StewardSigningKeyRow(Base):
    __tablename__ = "steward_signing_keys"
    __table_args__ = (
        CheckConstraint("algorithm = 'ED25519'", name="ck_steward_signing_keys_algorithm"),
    )

    key_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    signer_identity: Mapped[str] = mapped_column(String(300), nullable=False)
    purposes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    public_key_pem: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TrustRootRow(Base):
    __tablename__ = "trust_roots"
    __table_args__ = (
        CheckConstraint("length(definition_sha256) = 64", name="ck_trust_roots_definition_digest"),
        CheckConstraint("polling_interval_seconds >= 300", name="ck_trust_roots_polling_interval"),
    )

    trust_root_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    publisher_id: Mapped[str] = mapped_column(String(64), nullable=False)
    publisher_name: Mapped[str] = mapped_column(String(300), nullable=False)
    allowed_domains: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    jurisdictions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    product_families: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    licensing_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    polling_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    connector_name: Mapped[str] = mapped_column(String(100), nullable=False)
    connector_version: Mapped[str] = mapped_column(String(100), nullable=False)
    connector_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    trusted_stage_key_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    definition_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StewardArtifactRow(Base):
    __tablename__ = "steward_artifacts"
    __table_args__ = (
        CheckConstraint("length(sha256) = 64", name="ck_steward_artifacts_digest"),
        CheckConstraint("byte_size > 0", name="ck_steward_artifacts_size"),
        CheckConstraint(
            "kind IN ('INVENTORY_RESPONSE', 'INVENTORY_MANIFEST', 'SOURCE', "
            "'RELEASE_CANDIDATE', 'STRUCTURED_REPORT', 'DEPENDENCY_METADATA', "
            "'DEPENDENCY_PACKAGE', 'NARRATIVE_SOURCE', 'INPUT_CLOSURE_REPORT', "
            "'AUTHORITY_BINDING', 'EVIDENCE_RECORD', 'MATERIALIZATION_REPORT', "
            "'CORPUS_RELEASE_CANDIDATE', 'EVIDENCE_ARTIFACT_MANIFEST', "
            "'QA_DECISION_BATCH', 'CANONICAL_EVIDENCE_RECORD', "
            "'CORPUS_RELEASE_BUNDLE', 'BENCHMARK_ACCESS_POLICY', "
            "'BENCHMARK_ADJUDICATION_POLICY', 'BENCHMARK_THRESHOLD_POLICY', "
            "'BENCHMARK_REVIEW_DECISION', 'BENCHMARK_RESOLUTION', "
            "'BENCHMARK_ADJUDICATION_RECORD', 'BENCHMARK_SUITE', "
            "'BENCHMARK_GENERATION_POLICY', 'BENCHMARK_GENERATION_RECORD')",
            name="ck_steward_artifacts_kind",
        ),
    )

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(200), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReconciliationJobRow(Base):
    __tablename__ = "reconciliation_jobs"
    __table_args__ = (
        UniqueConstraint("trust_root_id", "idempotency_key"),
        CheckConstraint(
            "state IN ('PENDING', 'RUNNING', 'BLOCKED', 'FAILED', 'COMPLETED')",
            name="ck_reconciliation_jobs_state",
        ),
    )

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trust_root_id: Mapped[str] = mapped_column(
        ForeignKey("trust_roots.trust_root_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(300), nullable=False)
    trust_root_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    connector_name: Mapped[str] = mapped_column(String(100), nullable=False)
    connector_version: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    current_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    blockers: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReconciliationJobAttemptRow(Base):
    __tablename__ = "reconciliation_job_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt_number"),
        CheckConstraint("attempt_number >= 1", name="ck_reconciliation_attempt_number"),
        CheckConstraint(
            "state IN ('RUNNING', 'FAILED', 'BLOCKED', 'COMPLETED')",
            name="ck_reconciliation_attempt_state",
        ),
    )

    attempt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("reconciliation_jobs.job_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    resumed_from_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CryptographicAttestationRow(Base):
    __tablename__ = "cryptographic_attestations"
    __table_args__ = (
        UniqueConstraint("statement_sha256", "signature_sha256", "purpose"),
        CheckConstraint(
            "purpose IN ('STAGE', 'EXCEPTION', 'BENCHMARK_ACCEPTANCE', 'ACTIVATION')",
            name="ck_cryptographic_attestations_purpose",
        ),
        CheckConstraint(
            "length(statement_sha256) = 64 AND length(signature_sha256) = 64",
            name="ck_cryptographic_attestations_digests",
        ),
    )

    attestation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    predicate_type: Mapped[str] = mapped_column(String(200), nullable=False)
    statement_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    signature_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_base64: Mapped[str] = mapped_column(Text, nullable=False)
    signing_key_id: Mapped[str] = mapped_column(
        ForeignKey("steward_signing_keys.key_id", ondelete="RESTRICT"), nullable=False
    )
    signer_identity: Mapped[str] = mapped_column(String(300), nullable=False)
    statement: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReconciliationStageRow(Base):
    __tablename__ = "reconciliation_stages"
    __table_args__ = (
        UniqueConstraint("job_id", "stage"),
        CheckConstraint(
            "state IN ('PENDING', 'RUNNING', 'FAILED', 'COMPLETED')",
            name="ck_reconciliation_stages_state",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_reconciliation_stage_attempts"),
    )

    stage_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("reconciliation_jobs.job_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    attestation_id: Mapped[str | None] = mapped_column(
        ForeignKey("cryptographic_attestations.attestation_id", ondelete="RESTRICT"),
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReconciliationInventoryRow(Base):
    __tablename__ = "reconciliation_inventories"
    __table_args__ = (
        CheckConstraint("item_count >= 0", name="ck_reconciliation_inventory_count"),
        CheckConstraint(
            "length(inventory_artifact_sha256) = 64",
            name="ck_reconciliation_inventory_digest",
        ),
    )

    inventory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("reconciliation_jobs.job_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    trust_root_id: Mapped[str] = mapped_column(
        ForeignKey("trust_roots.trust_root_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    inventory_artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=False
    )
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReconciliationInventoryItemRow(Base):
    __tablename__ = "reconciliation_inventory_items"
    __table_args__ = (
        CheckConstraint(
            "change_kind IN ('NEW', 'CHANGED', 'UNCHANGED')",
            name="ck_reconciliation_inventory_item_change",
        ),
        CheckConstraint(
            "disposition IN ('INCLUDED', 'EXCEPTED', 'BLOCKED')",
            name="ck_reconciliation_inventory_item_disposition",
        ),
    )

    inventory_id: Mapped[str] = mapped_column(
        ForeignKey("reconciliation_inventories.inventory_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    item_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    fingerprint_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    item: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    change_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_sha256: Mapped[str | None] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=True
    )
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    blocker: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReconciliationExceptionRow(Base):
    __tablename__ = "reconciliation_exceptions"
    __table_args__ = (
        CheckConstraint(
            "reason IN ('ACCESS', 'LICENSING', 'ACQUISITION', 'VALIDATION')",
            name="ck_reconciliation_exceptions_reason",
        ),
        CheckConstraint(
            "length(statement_sha256) = 64 AND length(signature_sha256) = 64",
            name="ck_reconciliation_exceptions_digests",
        ),
    )

    exception_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trust_root_id: Mapped[str] = mapped_column(
        ForeignKey("trust_roots.trust_root_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    inventory_item_id: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    statement_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    attestation_id: Mapped[str] = mapped_column(
        ForeignKey("cryptographic_attestations.attestation_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReconciliationReleaseCandidateRow(Base):
    __tablename__ = "reconciliation_release_candidates"
    __table_args__ = (
        CheckConstraint("length(candidate_sha256) = 64", name="ck_reconciliation_candidate_digest"),
    )

    candidate_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("reconciliation_jobs.job_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    candidate_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=False
    )
    stage_attestation_id: Mapped[str] = mapped_column(
        ForeignKey("cryptographic_attestations.attestation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TrustRootRevisionRow(Base):
    __tablename__ = "trust_root_revisions"
    __table_args__ = (
        CheckConstraint(
            "length(definition_sha256) = 64",
            name="ck_trust_root_revisions_definition_digest",
        ),
    )

    definition_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    trust_root_id: Mapped[str] = mapped_column(
        ForeignKey("trust_roots.trust_root_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StructuredPackageRunRow(Base):
    __tablename__ = "structured_package_runs"
    __table_args__ = (
        UniqueConstraint(
            "reconciliation_candidate_id",
            "inventory_item_id",
            "processor_name",
            "processor_version",
        ),
        CheckConstraint(
            "state IN ('VALIDATED', 'BLOCKED')",
            name="ck_structured_package_runs_state",
        ),
        CheckConstraint(
            "length(report_sha256) = 64", name="ck_structured_package_runs_report_digest"
        ),
        CheckConstraint(
            "(structured_input_run_id IS NULL AND input_closure_sha256 IS NULL) OR "
            "(structured_input_run_id IS NOT NULL AND length(input_closure_sha256) = 64)",
            name="ck_structured_package_runs_input_closure",
        ),
        CheckConstraint("resource_count >= 0", name="ck_structured_package_runs_resources"),
    )

    structured_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    reconciliation_candidate_id: Mapped[str] = mapped_column(
        ForeignKey("reconciliation_release_candidates.candidate_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    trust_root_id: Mapped[str] = mapped_column(
        ForeignKey("trust_roots.trust_root_id", ondelete="RESTRICT"), nullable=False
    )
    trust_root_sha256: Mapped[str] = mapped_column(
        ForeignKey("trust_root_revisions.definition_sha256", ondelete="RESTRICT"),
        nullable=False,
    )
    inventory_item_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source_artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=False
    )
    structured_input_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("structured_input_runs.input_run_id", ondelete="RESTRICT"),
        nullable=True,
    )
    input_closure_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processor_name: Mapped[str] = mapped_column(String(100), nullable=False)
    processor_version: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    report_artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=False
    )
    attestation_id: Mapped[str] = mapped_column(
        ForeignKey("cryptographic_attestations.attestation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    resource_count: Mapped[int] = mapped_column(Integer, nullable=False)
    blockers: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StructuredFHIRResourceRow(Base):
    __tablename__ = "structured_fhir_resources"
    __table_args__ = (
        CheckConstraint(
            "length(content_sha256) = 64 AND "
            "(narrative_sha256 IS NULL OR length(narrative_sha256) = 64)",
            name="ck_structured_fhir_resources_digests",
        ),
        CheckConstraint("byte_size > 0", name="ck_structured_fhir_resources_size"),
    )

    structured_run_id: Mapped[str] = mapped_column(
        ForeignKey("structured_package_runs.structured_run_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    resource_key: Mapped[str] = mapped_column(String(600), primary_key=True)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    logical_reference: Mapped[str | None] = mapped_column(String(600), nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    experimental: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    profiles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    member_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    narrative_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    declared_in_implementation_guide: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_example: Mapped[bool] = mapped_column(Boolean, nullable=False)


class StructuredNarrativeLinkRow(Base):
    __tablename__ = "structured_narrative_links"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DECLARED_IN_PACKAGE', 'CONFIGURED_NOT_DECLARED')",
            name="ck_structured_narrative_links_status",
        ),
    )

    structured_run_id: Mapped[str] = mapped_column(
        ForeignKey("structured_package_runs.structured_run_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    link_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    identifier: Mapped[str | None] = mapped_column(String(300), nullable=True)
    version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False)


class StructuredInputRunRow(Base):
    __tablename__ = "structured_input_runs"
    __table_args__ = (
        UniqueConstraint(
            "reconciliation_candidate_id",
            "inventory_item_id",
            "resolver_name",
            "resolver_version",
        ),
        CheckConstraint(
            "state IN ('RESOLVED', 'BLOCKED')",
            name="ck_structured_input_runs_state",
        ),
        CheckConstraint(
            "length(report_sha256) = 64",
            name="ck_structured_input_runs_report_digest",
        ),
        CheckConstraint(
            "dependency_count >= 0 AND narrative_artifact_count >= 0",
            name="ck_structured_input_runs_counts",
        ),
    )

    input_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    reconciliation_candidate_id: Mapped[str] = mapped_column(
        ForeignKey("reconciliation_release_candidates.candidate_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    trust_root_id: Mapped[str] = mapped_column(
        ForeignKey("trust_roots.trust_root_id", ondelete="RESTRICT"), nullable=False
    )
    trust_root_sha256: Mapped[str] = mapped_column(
        ForeignKey("trust_root_revisions.definition_sha256", ondelete="RESTRICT"),
        nullable=False,
    )
    inventory_item_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source_artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=False
    )
    resolver_name: Mapped[str] = mapped_column(String(100), nullable=False)
    resolver_version: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    report_artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=False
    )
    attestation_id: Mapped[str] = mapped_column(
        ForeignKey("cryptographic_attestations.attestation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    dependency_count: Mapped[int] = mapped_column(Integer, nullable=False)
    narrative_artifact_count: Mapped[int] = mapped_column(Integer, nullable=False)
    blockers: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StructuredDependencyPackageRow(Base):
    __tablename__ = "structured_dependency_packages"
    __table_args__ = (
        CheckConstraint(
            "length(registry_metadata_artifact_sha256) = 64 AND "
            "length(package_artifact_sha256) = 64 AND length(manifest_sha256) = 64",
            name="ck_structured_dependency_packages_digests",
        ),
        CheckConstraint(
            "byte_size > 0 AND minimum_depth >= 1",
            name="ck_structured_dependency_packages_sizes",
        ),
    )

    input_run_id: Mapped[str] = mapped_column(
        ForeignKey("structured_input_runs.input_run_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    package_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    version: Mapped[str] = mapped_column(String(200), primary_key=True)
    direct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    minimum_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    registry_metadata_artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=False
    )
    package_artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=False
    )
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    registry_url: Mapped[str] = mapped_column(Text, nullable=False)
    tarball_url: Mapped[str] = mapped_column(Text, nullable=False)
    registry_sha1: Mapped[str | None] = mapped_column(String(40), nullable=True)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(Text, nullable=True)


class StructuredNarrativeArtifactRow(Base):
    __tablename__ = "structured_narrative_artifacts"
    __table_args__ = (
        CheckConstraint(
            "role IN ('PRIMARY', 'ANNEX')",
            name="ck_structured_narrative_artifacts_role",
        ),
        CheckConstraint(
            "length(artifact_sha256) = 64 AND byte_size > 0",
            name="ck_structured_narrative_artifacts_digest",
        ),
    )

    input_run_id: Mapped[str] = mapped_column(
        ForeignKey("structured_input_runs.input_run_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    link_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    configured_url: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(String(200), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=False
    )
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(Text, nullable=True)


class MaterializationRunRow(Base):
    __tablename__ = "materialization_runs"
    __table_args__ = (
        UniqueConstraint(
            "reconciliation_candidate_id", "materializer_name", "materializer_version"
        ),
        CheckConstraint(
            "state IN ('READY_FOR_QA', 'BLOCKED')",
            name="ck_materialization_runs_state",
        ),
        CheckConstraint(
            "length(authority_binding_sha256) = 64 AND length(report_sha256) = 64",
            name="ck_materialization_runs_digests",
        ),
        CheckConstraint("evidence_count >= 0", name="ck_materialization_runs_evidence_count"),
    )

    materialization_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    reconciliation_candidate_id: Mapped[str] = mapped_column(
        ForeignKey("reconciliation_release_candidates.candidate_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    input_run_id: Mapped[str] = mapped_column(
        ForeignKey("structured_input_runs.input_run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    structured_run_id: Mapped[str] = mapped_column(
        ForeignKey("structured_package_runs.structured_run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    materializer_name: Mapped[str] = mapped_column(String(100), nullable=False)
    materializer_version: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    authority_binding_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    authority_binding: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    authority_binding_artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=False
    )
    authority_attestation_id: Mapped[str] = mapped_column(
        ForeignKey("cryptographic_attestations.attestation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    report_artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=False
    )
    corpus_candidate_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    corpus_candidate: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    corpus_candidate_artifact_sha256: Mapped[str | None] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=True
    )
    corpus_candidate_attestation_id: Mapped[str | None] = mapped_column(
        ForeignKey("cryptographic_attestations.attestation_id", ondelete="RESTRICT"),
        nullable=True,
    )
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    blockers: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MaterializedEvidenceRow(Base):
    __tablename__ = "materialized_evidence_records"
    __table_args__ = (
        UniqueConstraint("evidence_sha256"),
        CheckConstraint(
            "length(evidence_sha256) = 64 AND length(source_artifact_sha256) = 64",
            name="ck_materialized_evidence_digests",
        ),
    )

    materialization_run_id: Mapped[str] = mapped_column(
        ForeignKey("materialization_runs.materialization_run_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    source_unit_id: Mapped[str] = mapped_column(String(500), nullable=False)
    source_artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=False
    )
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=False
    )


class CorpusQARunRow(Base):
    __tablename__ = "corpus_qa_runs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('PREPARED', 'VALIDATED')",
            name="ck_corpus_qa_runs_state",
        ),
        CheckConstraint(
            "evidence_count > 0 AND approved_count >= 0 AND quarantined_count >= 0",
            name="ck_corpus_qa_runs_counts",
        ),
        CheckConstraint(
            "length(corpus_release_candidate_sha256) = 64 "
            "AND length(evidence_manifest_sha256) = 64",
            name="ck_corpus_qa_runs_digests",
        ),
    )

    qa_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    corpus_release_candidate_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    materialization_run_id: Mapped[str] = mapped_column(
        ForeignKey("materialization_runs.materialization_run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    corpus_release_candidate_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    evidence_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evidence_manifest_artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=False
    )
    evidence_manifest_attestation_id: Mapped[str] = mapped_column(
        ForeignKey("cryptographic_attestations.attestation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_batch_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    decision_batch: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    decision_batch_artifact_sha256: Mapped[str | None] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=True
    )
    decision_batch_attestation_id: Mapped[str | None] = mapped_column(
        ForeignKey("cryptographic_attestations.attestation_id", ondelete="RESTRICT"),
        nullable=True,
    )
    approved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quarantined_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    corpus_release_id: Mapped[str | None] = mapped_column(
        ForeignKey("corpus_releases.corpus_release_id", ondelete="RESTRICT"), nullable=True
    )
    bundle_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    bundle_artifact_sha256: Mapped[str | None] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvidenceAnchorReplayRow(Base):
    __tablename__ = "evidence_anchor_replays"
    __table_args__ = (
        CheckConstraint("outcome IN ('PASS', 'FAIL')", name="ck_evidence_anchor_replays_outcome"),
        CheckConstraint(
            "length(materialized_evidence_sha256) = 64 "
            "AND length(source_artifact_sha256) = 64 "
            "AND length(replay_sha256) = 64",
            name="ck_evidence_anchor_replays_digests",
        ),
    )

    qa_run_id: Mapped[str] = mapped_column(
        ForeignKey("corpus_qa_runs.qa_run_id", ondelete="RESTRICT"), primary_key=True
    )
    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    materialized_evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    replay_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    replayed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvidenceQADecisionRow(Base):
    __tablename__ = "evidence_qa_decisions"
    __table_args__ = (
        CheckConstraint(
            "disposition IN ('APPROVE', 'QUARANTINE')",
            name="ck_evidence_qa_decisions_disposition",
        ),
        CheckConstraint(
            "length(materialized_evidence_sha256) = 64 AND length(decision_sha256) = 64",
            name="ck_evidence_qa_decisions_digests",
        ),
    )

    qa_run_id: Mapped[str] = mapped_column(
        ForeignKey("corpus_qa_runs.qa_run_id", ondelete="RESTRICT"), primary_key=True
    )
    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    materialized_evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_authority: Mapped[str] = mapped_column(String(300), nullable=False)
    batch_attestation_id: Mapped[str] = mapped_column(
        ForeignKey("cryptographic_attestations.attestation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BenchmarkPolicyArtifactRow(Base):
    __tablename__ = "benchmark_policy_artifacts"
    __table_args__ = (
        UniqueConstraint("kind", "policy_id", "revision"),
        CheckConstraint(
            "kind IN ('ACCESS', 'ADJUDICATION_PROCESS', 'THRESHOLD')",
            name="ck_benchmark_policy_artifacts_kind",
        ),
        CheckConstraint(
            "length(policy_sha256) = 64",
            name="ck_benchmark_policy_artifacts_digest",
        ),
    )

    policy_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(100), nullable=False)
    revision: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    sealed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BenchmarkReviewDecisionRow(Base):
    __tablename__ = "benchmark_review_decisions"
    __table_args__ = (
        UniqueConstraint("case_id", "adjudicator_identity"),
        CheckConstraint(
            "suite_partition IN ('DEVELOPMENT', 'SEALED_HOLDOUT')",
            name="ck_benchmark_review_decisions_partition",
        ),
        CheckConstraint(
            "length(decision_sha256) = 64",
            name="ck_benchmark_review_decisions_digest",
        ),
    )

    decision_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    review_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    case_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    suite_partition: Mapped[str] = mapped_column(String(32), nullable=False)
    adjudicator_identity: Mapped[str] = mapped_column(String(300), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BenchmarkDisagreementResolutionRow(Base):
    __tablename__ = "benchmark_disagreement_resolutions"
    __table_args__ = (
        CheckConstraint(
            "length(resolution_sha256) = 64",
            name="ck_benchmark_disagreement_resolutions_digest",
        ),
    )

    resolution_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    resolution_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    case_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BenchmarkAdjudicationRecordRow(Base):
    __tablename__ = "benchmark_adjudication_records"
    __table_args__ = (
        CheckConstraint(
            "length(adjudication_record_sha256) = 64 "
            "AND length(access_policy_sha256) = 64 "
            "AND length(adjudication_process_sha256) = 64",
            name="ck_benchmark_adjudication_records_digests",
        ),
        CheckConstraint(
            "development_case_count >= 0 AND sealed_holdout_case_count >= 0 "
            "AND development_case_count + sealed_holdout_case_count > 0",
            name="ck_benchmark_adjudication_records_counts",
        ),
    )

    adjudication_record_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    adjudication_record_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    access_policy_sha256: Mapped[str] = mapped_column(
        ForeignKey("benchmark_policy_artifacts.policy_sha256", ondelete="RESTRICT"),
        nullable=False,
    )
    adjudication_process_sha256: Mapped[str] = mapped_column(
        ForeignKey("benchmark_policy_artifacts.policy_sha256", ondelete="RESTRICT"),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    development_case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sealed_holdout_case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sealed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BenchmarkAdjudicationCaseRow(Base):
    __tablename__ = "benchmark_adjudication_cases"
    __table_args__ = (
        CheckConstraint(
            "suite_partition IN ('DEVELOPMENT', 'SEALED_HOLDOUT')",
            name="ck_benchmark_adjudication_cases_partition",
        ),
        CheckConstraint(
            "length(case_sha256) = 64",
            name="ck_benchmark_adjudication_cases_digest",
        ),
    )

    adjudication_record_sha256: Mapped[str] = mapped_column(
        ForeignKey(
            "benchmark_adjudication_records.adjudication_record_sha256",
            ondelete="RESTRICT",
        ),
        primary_key=True,
    )
    case_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    suite_partition: Mapped[str] = mapped_column(String(32), nullable=False)
    case_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    review_decision_sha256s: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    resolution_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class BenchmarkSuiteBuildRow(Base):
    __tablename__ = "benchmark_suite_builds"
    __table_args__ = (
        UniqueConstraint("benchmark_id", "suite_partition"),
        Index(
            "uq_benchmark_suite_builds_holdout_once",
            "adjudication_record_sha256",
            unique=True,
            sqlite_where=text("suite_partition = 'SEALED_HOLDOUT'"),
            postgresql_where=text("suite_partition = 'SEALED_HOLDOUT'"),
        ),
        CheckConstraint(
            "suite_partition IN ('DEVELOPMENT', 'SEALED_HOLDOUT')",
            name="ck_benchmark_suite_builds_partition",
        ),
        CheckConstraint(
            "length(suite_sha256) = 64 "
            "AND length(adjudication_record_sha256) = 64 "
            "AND length(access_policy_sha256) = 64 "
            "AND length(adjudication_process_sha256) = 64 "
            "AND length(threshold_policy_sha256) = 64 "
            "AND length(candidate_configuration_sha256) = 64 "
            "AND length(manifest_sha256) = 64",
            name="ck_benchmark_suite_builds_digests",
        ),
        CheckConstraint("case_count > 0", name="ck_benchmark_suite_builds_case_count"),
    )

    suite_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    benchmark_id: Mapped[str] = mapped_column(String(100), nullable=False)
    suite_partition: Mapped[str] = mapped_column(String(32), nullable=False)
    adjudication_record_sha256: Mapped[str] = mapped_column(
        ForeignKey(
            "benchmark_adjudication_records.adjudication_record_sha256",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    access_policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    adjudication_process_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    threshold_policy_sha256: Mapped[str] = mapped_column(
        ForeignKey("benchmark_policy_artifacts.policy_sha256", ondelete="RESTRICT"),
        nullable=False,
    )
    candidate_configuration_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    corpus_release_id: Mapped[str] = mapped_column(
        ForeignKey("corpus_releases.corpus_release_id", ondelete="RESTRICT"),
        nullable=False,
    )
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    built_by: Mapped[str] = mapped_column(String(300), nullable=False)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BenchmarkAccessEventRow(Base):
    __tablename__ = "benchmark_access_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('BUILD_DEVELOPMENT_SUITE', 'BUILD_SEALED_HOLDOUT_SUITE')",
            name="ck_benchmark_access_events_action",
        ),
        CheckConstraint(
            "length(access_policy_sha256) = 64 AND length(resource_sha256) = 64",
            name="ck_benchmark_access_events_digests",
        ),
    )

    access_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    access_policy_sha256: Mapped[str] = mapped_column(
        ForeignKey("benchmark_policy_artifacts.policy_sha256", ondelete="RESTRICT"),
        nullable=False,
    )
    actor_identity: Mapped[str] = mapped_column(String(300), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BenchmarkGenerationPolicyRow(Base):
    __tablename__ = "benchmark_generation_policies"
    __table_args__ = (
        UniqueConstraint("policy_id", "revision"),
        CheckConstraint(
            "length(policy_sha256) = 64", name="ck_benchmark_generation_policies_digest"
        ),
    )

    policy_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(100), nullable=False)
    revision: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    sealed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BenchmarkGenerationRecordRow(Base):
    __tablename__ = "benchmark_generation_records"
    __table_args__ = (
        CheckConstraint(
            "length(generation_record_sha256) = 64 "
            "AND length(generation_policy_sha256) = 64 "
            "AND length(access_policy_sha256) = 64 "
            "AND length(threshold_policy_sha256) = 64 "
            "AND length(manifest_sha256) = 64 "
            "AND (candidate_configuration_sha256 IS NULL "
            "OR length(candidate_configuration_sha256) = 64) "
            "AND length(partition_assignment_sha256) = 64",
            name="ck_benchmark_generation_records_digests",
        ),
        CheckConstraint(
            "development_case_count > 0 AND sealed_holdout_case_count > 0",
            name="ck_benchmark_generation_records_counts",
        ),
    )

    generation_record_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    generation_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    generation_policy_sha256: Mapped[str] = mapped_column(
        ForeignKey("benchmark_generation_policies.policy_sha256", ondelete="RESTRICT"),
        nullable=False,
    )
    access_policy_sha256: Mapped[str] = mapped_column(
        ForeignKey("benchmark_policy_artifacts.policy_sha256", ondelete="RESTRICT"),
        nullable=False,
    )
    threshold_policy_sha256: Mapped[str] = mapped_column(
        ForeignKey("benchmark_policy_artifacts.policy_sha256", ondelete="RESTRICT"),
        nullable=False,
    )
    corpus_release_id: Mapped[str] = mapped_column(
        ForeignKey("corpus_releases.corpus_release_id", ondelete="RESTRICT"),
        nullable=False,
    )
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_configuration_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    partition_assignment_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    development_case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sealed_holdout_case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    generated_by: Mapped[str] = mapped_column(String(300), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AutomatedBenchmarkSuiteBuildRow(Base):
    __tablename__ = "automated_benchmark_suite_builds"
    __table_args__ = (
        UniqueConstraint("benchmark_id", "suite_partition"),
        UniqueConstraint("generation_record_sha256", "suite_partition"),
        CheckConstraint(
            "suite_partition IN ('DEVELOPMENT', 'SEALED_HOLDOUT')",
            name="ck_automated_benchmark_suite_builds_partition",
        ),
        CheckConstraint(
            "length(suite_sha256) = 64 "
            "AND length(generation_record_sha256) = 64 "
            "AND length(generation_policy_sha256) = 64 "
            "AND length(access_policy_sha256) = 64 "
            "AND length(threshold_policy_sha256) = 64 "
            "AND (candidate_configuration_sha256 IS NULL "
            "OR length(candidate_configuration_sha256) = 64) "
            "AND length(manifest_sha256) = 64",
            name="ck_automated_benchmark_suite_builds_digests",
        ),
        CheckConstraint("case_count > 0", name="ck_automated_benchmark_suite_builds_case_count"),
    )

    suite_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    benchmark_id: Mapped[str] = mapped_column(String(100), nullable=False)
    suite_partition: Mapped[str] = mapped_column(String(32), nullable=False)
    generation_record_sha256: Mapped[str] = mapped_column(
        ForeignKey("benchmark_generation_records.generation_record_sha256", ondelete="RESTRICT"),
        nullable=False,
    )
    generation_policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    access_policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    threshold_policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_configuration_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    corpus_release_id: Mapped[str] = mapped_column(
        ForeignKey("corpus_releases.corpus_release_id", ondelete="RESTRICT"),
        nullable=False,
    )
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    built_by: Mapped[str] = mapped_column(String(300), nullable=False)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BenchmarkDevelopmentSuiteDerivationRow(Base):
    __tablename__ = "benchmark_development_suite_derivations"
    __table_args__ = (
        UniqueConstraint("parent_suite_sha256", "candidate_configuration_sha256"),
        CheckConstraint(
            "length(suite_sha256) = 64 "
            "AND length(parent_suite_sha256) = 64 "
            "AND length(candidate_configuration_sha256) = 64 "
            "AND length(access_policy_sha256) = 64 "
            "AND length(manifest_sha256) = 64",
            name="ck_benchmark_development_suite_derivations_digests",
        ),
        CheckConstraint(
            "case_count > 0",
            name="ck_benchmark_development_suite_derivations_case_count",
        ),
    )

    suite_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_suite_sha256: Mapped[str] = mapped_column(
        ForeignKey("automated_benchmark_suite_builds.suite_sha256", ondelete="RESTRICT"),
        nullable=False,
    )
    candidate_configuration_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    access_policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    corpus_release_id: Mapped[str] = mapped_column(
        ForeignKey("corpus_releases.corpus_release_id", ondelete="RESTRICT"),
        nullable=False,
    )
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(
        ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    derived_by: Mapped[str] = mapped_column(String(300), nullable=False)
    derived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BenchmarkExecutionRunRow(Base):
    __tablename__ = "benchmark_execution_runs"
    __table_args__ = (
        Index(
            "uq_benchmark_execution_runs_holdout_once",
            "suite_sha256",
            unique=True,
            sqlite_where=text("suite_partition = 'SEALED_HOLDOUT'"),
            postgresql_where=text("suite_partition = 'SEALED_HOLDOUT'"),
        ),
        CheckConstraint(
            "suite_partition IN ('DEVELOPMENT', 'SEALED_HOLDOUT', 'SYNTHETIC')",
            name="ck_benchmark_execution_runs_partition",
        ),
        CheckConstraint(
            "status IN ('STARTED', 'COMPLETED', 'FAILED')",
            name="ck_benchmark_execution_runs_status",
        ),
        CheckConstraint(
            "length(suite_sha256) = 64 "
            "AND length(candidate_configuration_sha256) = 64 "
            "AND length(vector_batch_sha256) = 64 "
            "AND (report_sha256 IS NULL OR length(report_sha256) = 64)",
            name="ck_benchmark_execution_runs_digests",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    suite_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    suite_partition: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_configuration_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    vector_batch_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_identity: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    report_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    report_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    failure_class: Mapped[str | None] = mapped_column(String(200), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
