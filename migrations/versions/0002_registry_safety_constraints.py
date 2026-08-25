"""Add fail-closed constraints to canonical ingestion records.

Revision ID: 0002_registry_safety_constraints
Revises: 0001_authenticated_ingestion
Create Date: 2026-08-25
"""

from alembic import op

revision = "0002_registry_safety_constraints"
down_revision = "0001_authenticated_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_sources_source_class",
        "sources",
        "source_class IN ('E1', 'E2', 'E3', 'E4', 'E5', 'E6', 'E7')",
    )
    op.create_check_constraint(
        "ck_source_versions_status",
        "source_versions",
        "status IN ('DISCOVERED', 'DOWNLOADED', 'PARSING', 'QA_REQUIRED', "
        "'APPROVED', 'EFFECTIVE', 'PARTIALLY_SUPERSEDED', 'SUPERSEDED', "
        "'WITHDRAWN', 'ARCHIVED', 'REJECTED', 'QUARANTINED')",
    )
    op.create_check_constraint(
        "ck_source_versions_effective_range",
        "source_versions",
        "effective_from IS NULL OR effective_to IS NULL OR effective_from <= effective_to",
    )
    op.create_check_constraint(
        "ck_source_versions_approval_status",
        "source_versions",
        "approved_for_retrieval = false OR status IN "
        "('APPROVED', 'EFFECTIVE', 'PARTIALLY_SUPERSEDED')",
    )
    op.create_check_constraint(
        "ck_source_relationships_distinct_versions",
        "source_relationships",
        "from_source_version_id <> to_source_version_id",
    )
    op.create_check_constraint(
        "ck_source_relationships_valid_range",
        "source_relationships",
        "valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to",
    )
    op.create_check_constraint(
        "ck_source_artifacts_sha256_length",
        "source_artifacts",
        "length(sha256) = 64",
    )
    op.create_check_constraint(
        "ck_source_artifacts_byte_size", "source_artifacts", "byte_size > 0"
    )
    op.create_check_constraint(
        "ck_source_artifacts_media_type",
        "source_artifacts",
        "media_type = 'application/pdf'",
    )
    op.create_check_constraint(
        "ck_source_acquisitions_expected_sha256_length",
        "source_acquisitions",
        "expected_sha256 IS NULL OR length(expected_sha256) = 64",
    )
    op.create_check_constraint(
        "ck_extraction_runs_trust_status",
        "extraction_runs",
        "trust_status IN ('UNVERIFIED', 'VERIFIED_NATIVE', 'VERIFIED_CROSS_PARSER', "
        "'HUMAN_VERIFIED', 'SUSPECT', 'QUARANTINED')",
    )
    op.create_check_constraint(
        "ck_extraction_runs_page_count", "extraction_runs", "page_count >= 1"
    )
    op.create_check_constraint(
        "ck_extraction_runs_span_count", "extraction_runs", "span_count >= 0"
    )
    op.create_check_constraint(
        "ck_extracted_spans_pdf_page", "extracted_spans", "pdf_page >= 1"
    )
    op.create_check_constraint(
        "ck_extracted_spans_bbox",
        "extracted_spans",
        "bbox_right > bbox_left AND bbox_bottom > bbox_top",
    )
    op.create_check_constraint(
        "ck_quarantine_events_resolution",
        "quarantine_events",
        "(resolved_at IS NULL AND resolved_by IS NULL AND resolution_note IS NULL) OR "
        "(resolved_at IS NOT NULL AND resolved_by IS NOT NULL AND resolution_note IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_quarantine_events_resolution", "quarantine_events", type_="check"
    )
    op.drop_constraint("ck_extracted_spans_bbox", "extracted_spans", type_="check")
    op.drop_constraint("ck_extracted_spans_pdf_page", "extracted_spans", type_="check")
    op.drop_constraint("ck_extraction_runs_span_count", "extraction_runs", type_="check")
    op.drop_constraint("ck_extraction_runs_page_count", "extraction_runs", type_="check")
    op.drop_constraint("ck_extraction_runs_trust_status", "extraction_runs", type_="check")
    op.drop_constraint(
        "ck_source_acquisitions_expected_sha256_length",
        "source_acquisitions",
        type_="check",
    )
    op.drop_constraint("ck_source_artifacts_media_type", "source_artifacts", type_="check")
    op.drop_constraint("ck_source_artifacts_byte_size", "source_artifacts", type_="check")
    op.drop_constraint(
        "ck_source_artifacts_sha256_length", "source_artifacts", type_="check"
    )
    op.drop_constraint(
        "ck_source_relationships_valid_range", "source_relationships", type_="check"
    )
    op.drop_constraint(
        "ck_source_relationships_distinct_versions",
        "source_relationships",
        type_="check",
    )
    op.drop_constraint(
        "ck_source_versions_approval_status", "source_versions", type_="check"
    )
    op.drop_constraint(
        "ck_source_versions_effective_range", "source_versions", type_="check"
    )
    op.drop_constraint("ck_source_versions_status", "source_versions", type_="check")
    op.drop_constraint("ck_sources_source_class", "sources", type_="check")
