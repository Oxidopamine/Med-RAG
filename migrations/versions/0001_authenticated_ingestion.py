"""Create the authenticated source-ingestion registry.

Revision ID: 0001_authenticated_ingestion
Revises:
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_authenticated_ingestion"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "publishers",
        sa.Column("publisher_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "publisher_domains",
        sa.Column("domain", sa.String(253), primary_key=True),
        sa.Column(
            "publisher_id",
            sa.String(64),
            sa.ForeignKey("publishers.publisher_id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_publisher_domains_publisher_id", "publisher_domains", ["publisher_id"])
    op.create_table(
        "sources",
        sa.Column("source_id", sa.String(64), primary_key=True),
        sa.Column(
            "publisher_id",
            sa.String(64),
            sa.ForeignKey("publishers.publisher_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("source_class", sa.String(8), nullable=False),
        sa.Column("jurisdiction", sa.String(32), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("license_render_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sources_publisher_id", "sources", ["publisher_id"])
    op.create_table(
        "source_versions",
        sa.Column("source_version_id", sa.String(64), primary_key=True),
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("sources.source_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version_label", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("approved_for_retrieval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "version_label"),
    )
    op.create_index("ix_source_versions_source_id", "source_versions", ["source_id"])
    op.create_table(
        "source_relationships",
        sa.Column("source_relationship_id", sa.String(64), primary_key=True),
        sa.Column(
            "from_source_version_id",
            sa.String(64),
            sa.ForeignKey("source_versions.source_version_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "to_source_version_id",
            sa.String(64),
            sa.ForeignKey("source_versions.source_version_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("relationship_type", sa.String(32), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("affected_section_ids", sa.JSON(), nullable=False),
        sa.Column("affected_recommendation_ids", sa.JSON(), nullable=False),
        sa.Column("affected_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("relationship_reason", sa.Text(), nullable=True),
    )
    op.create_table(
        "source_artifacts",
        sa.Column("artifact_id", sa.String(64), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "source_acquisitions",
        sa.Column("acquisition_id", sa.String(64), primary_key=True),
        sa.Column(
            "source_version_id",
            sa.String(64),
            sa.ForeignKey("source_versions.source_version_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "artifact_id",
            sa.String(64),
            sa.ForeignKey("source_artifacts.artifact_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("requested_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=False),
        sa.Column("publisher_domain", sa.String(253), nullable=False),
        sa.Column("expected_sha256", sa.String(64), nullable=True),
        sa.Column("http_etag", sa.Text(), nullable=True),
        sa.Column("http_last_modified", sa.Text(), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "extraction_runs",
        sa.Column("extraction_run_id", sa.String(64), primary_key=True),
        sa.Column(
            "acquisition_id",
            sa.String(64),
            sa.ForeignKey("source_acquisitions.acquisition_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("extractor_name", sa.String(100), nullable=False),
        sa.Column("extractor_version", sa.String(100), nullable=False),
        sa.Column("trust_status", sa.String(32), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("span_count", sa.Integer(), nullable=False),
        sa.Column("diagnostics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "extracted_spans",
        sa.Column("extracted_span_id", sa.String(64), primary_key=True),
        sa.Column(
            "extraction_run_id",
            sa.String(64),
            sa.ForeignKey("extraction_runs.extraction_run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pdf_page", sa.Integer(), nullable=False),
        sa.Column("block_index", sa.Integer(), nullable=False),
        sa.Column("line_index", sa.Integer(), nullable=False),
        sa.Column("span_index", sa.Integer(), nullable=False),
        sa.Column("text_exact", sa.Text(), nullable=False),
        sa.Column("bbox_left", sa.Float(), nullable=False),
        sa.Column("bbox_top", sa.Float(), nullable=False),
        sa.Column("bbox_right", sa.Float(), nullable=False),
        sa.Column("bbox_bottom", sa.Float(), nullable=False),
        sa.Column("font_name", sa.String(300), nullable=True),
        sa.Column("font_size", sa.Float(), nullable=True),
        sa.Column("font_flags", sa.Integer(), nullable=True),
        sa.UniqueConstraint(
            "extraction_run_id", "pdf_page", "block_index", "line_index", "span_index"
        ),
    )
    op.create_index("ix_extracted_spans_extraction_run_id", "extracted_spans", ["extraction_run_id"])
    op.create_table(
        "quarantine_events",
        sa.Column("quarantine_event_id", sa.String(64), primary_key=True),
        sa.Column(
            "source_version_id",
            sa.String(64),
            sa.ForeignKey("source_versions.source_version_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("acquisition_url", sa.Text(), nullable=True),
        sa.Column("reason_code", sa.String(100), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(200), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
    )
    op.create_index("ix_quarantine_events_source_version_id", "quarantine_events", ["source_version_id"])


def downgrade() -> None:
    op.drop_table("quarantine_events")
    op.drop_table("extracted_spans")
    op.drop_table("extraction_runs")
    op.drop_table("source_acquisitions")
    op.drop_table("source_artifacts")
    op.drop_table("source_relationships")
    op.drop_table("source_versions")
    op.drop_table("sources")
    op.drop_table("publisher_domains")
    op.drop_table("publishers")
