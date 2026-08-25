"""Add immutable trust-root revisions and structured FHIR package processing.

Revision ID: 0006_structured_fhir_packages
Revises: 0005_inventory_reconciliation
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_structured_fhir_packages"
down_revision = "0005_inventory_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_steward_artifacts_kind", "steward_artifacts", type_="check")
    op.create_check_constraint(
        "ck_steward_artifacts_kind",
        "steward_artifacts",
        "kind IN ('INVENTORY_RESPONSE', 'INVENTORY_MANIFEST', 'SOURCE', "
        "'RELEASE_CANDIDATE', 'STRUCTURED_REPORT')",
    )
    op.create_table(
        "trust_root_revisions",
        sa.Column("definition_sha256", sa.String(64), primary_key=True),
        sa.Column(
            "trust_root_id",
            sa.String(128),
            sa.ForeignKey("trust_roots.trust_root_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(definition_sha256) = 64",
            name="ck_trust_root_revisions_definition_digest",
        ),
    )
    op.create_index(
        "ix_trust_root_revisions_trust_root_id",
        "trust_root_revisions",
        ["trust_root_id"],
    )
    op.execute(
        """
        INSERT INTO trust_root_revisions (
          definition_sha256, trust_root_id, definition, registered_at
        )
        SELECT definition_sha256, trust_root_id, definition, updated_at
        FROM trust_roots
        """
    )
    op.create_table(
        "structured_package_runs",
        sa.Column("structured_run_id", sa.String(64), primary_key=True),
        sa.Column(
            "reconciliation_candidate_id",
            sa.String(64),
            sa.ForeignKey(
                "reconciliation_release_candidates.candidate_id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column(
            "trust_root_id",
            sa.String(128),
            sa.ForeignKey("trust_roots.trust_root_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "trust_root_sha256",
            sa.String(64),
            sa.ForeignKey("trust_root_revisions.definition_sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("inventory_item_id", sa.String(512), nullable=False),
        sa.Column(
            "source_artifact_sha256",
            sa.String(64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("processor_name", sa.String(100), nullable=False),
        sa.Column("processor_version", sa.String(100), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("report_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column(
            "report_artifact_sha256",
            sa.String(64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "attestation_id",
            sa.String(64),
            sa.ForeignKey("cryptographic_attestations.attestation_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("resource_count", sa.Integer(), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('VALIDATED', 'BLOCKED')",
            name="ck_structured_package_runs_state",
        ),
        sa.CheckConstraint(
            "length(report_sha256) = 64", name="ck_structured_package_runs_report_digest"
        ),
        sa.CheckConstraint(
            "resource_count >= 0", name="ck_structured_package_runs_resources"
        ),
        sa.UniqueConstraint(
            "reconciliation_candidate_id",
            "inventory_item_id",
            "processor_name",
            "processor_version",
        ),
    )
    op.create_index(
        "ix_structured_package_runs_reconciliation_candidate_id",
        "structured_package_runs",
        ["reconciliation_candidate_id"],
    )
    op.create_table(
        "structured_fhir_resources",
        sa.Column(
            "structured_run_id",
            sa.String(64),
            sa.ForeignKey("structured_package_runs.structured_run_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("resource_key", sa.String(600), primary_key=True),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.String(255), nullable=False),
        sa.Column("logical_reference", sa.String(600), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("version", sa.String(200), nullable=True),
        sa.Column("status", sa.String(100), nullable=True),
        sa.Column("experimental", sa.Boolean(), nullable=True),
        sa.Column("profiles", sa.JSON(), nullable=False),
        sa.Column("member_path", sa.String(1000), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("narrative_sha256", sa.String(64), nullable=True),
        sa.Column("declared_in_implementation_guide", sa.Boolean(), nullable=False),
        sa.Column("is_example", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "length(content_sha256) = 64 AND "
            "(narrative_sha256 IS NULL OR length(narrative_sha256) = 64)",
            name="ck_structured_fhir_resources_digests",
        ),
        sa.CheckConstraint("byte_size > 0", name="ck_structured_fhir_resources_size"),
    )
    op.create_index(
        "ix_structured_fhir_resources_resource_type",
        "structured_fhir_resources",
        ["resource_type"],
    )
    op.create_table(
        "structured_narrative_links",
        sa.Column(
            "structured_run_id",
            sa.String(64),
            sa.ForeignKey("structured_package_runs.structured_run_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("link_id", sa.String(200), primary_key=True),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("identifier", sa.String(300), nullable=True),
        sa.Column("version", sa.String(200), nullable=True),
        sa.Column("status", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "status IN ('DECLARED_IN_PACKAGE', 'CONFIGURED_NOT_DECLARED')",
            name="ck_structured_narrative_links_status",
        ),
    )
    for table_name in (
        "trust_root_revisions",
        "structured_package_runs",
        "structured_fhir_resources",
        "structured_narrative_links",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reject_steward_immutable_change()
            """
        )


def downgrade() -> None:
    for table_name in (
        "structured_narrative_links",
        "structured_fhir_resources",
        "structured_package_runs",
        "trust_root_revisions",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.drop_table("structured_narrative_links")
    op.drop_index(
        "ix_structured_fhir_resources_resource_type",
        table_name="structured_fhir_resources",
    )
    op.drop_table("structured_fhir_resources")
    op.drop_index(
        "ix_structured_package_runs_reconciliation_candidate_id",
        table_name="structured_package_runs",
    )
    op.drop_table("structured_package_runs")
    op.drop_index(
        "ix_trust_root_revisions_trust_root_id", table_name="trust_root_revisions"
    )
    op.drop_table("trust_root_revisions")
    op.drop_constraint("ck_steward_artifacts_kind", "steward_artifacts", type_="check")
    op.create_check_constraint(
        "ck_steward_artifacts_kind",
        "steward_artifacts",
        "kind IN ('INVENTORY_RESPONSE', 'INVENTORY_MANIFEST', 'SOURCE', "
        "'RELEASE_CANDIDATE')",
    )
