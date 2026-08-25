"""Add immutable narrative and dependency input closures.

Revision ID: 0007_structured_input_closures
Revises: 0006_structured_fhir_packages
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_structured_input_closures"
down_revision = "0006_structured_fhir_packages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_steward_artifacts_kind", "steward_artifacts", type_="check")
    op.create_check_constraint(
        "ck_steward_artifacts_kind",
        "steward_artifacts",
        "kind IN ('INVENTORY_RESPONSE', 'INVENTORY_MANIFEST', 'SOURCE', "
        "'RELEASE_CANDIDATE', 'STRUCTURED_REPORT', 'DEPENDENCY_METADATA', "
        "'DEPENDENCY_PACKAGE', 'NARRATIVE_SOURCE', 'INPUT_CLOSURE_REPORT')",
    )
    op.create_table(
        "structured_input_runs",
        sa.Column("input_run_id", sa.String(64), primary_key=True),
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
        sa.Column("resolver_name", sa.String(100), nullable=False),
        sa.Column("resolver_version", sa.String(100), nullable=False),
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
        sa.Column("dependency_count", sa.Integer(), nullable=False),
        sa.Column("narrative_artifact_count", sa.Integer(), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('RESOLVED', 'BLOCKED')",
            name="ck_structured_input_runs_state",
        ),
        sa.CheckConstraint(
            "length(report_sha256) = 64",
            name="ck_structured_input_runs_report_digest",
        ),
        sa.CheckConstraint(
            "dependency_count >= 0 AND narrative_artifact_count >= 0",
            name="ck_structured_input_runs_counts",
        ),
        sa.UniqueConstraint(
            "reconciliation_candidate_id",
            "inventory_item_id",
            "resolver_name",
            "resolver_version",
        ),
    )
    op.create_index(
        "ix_structured_input_runs_reconciliation_candidate_id",
        "structured_input_runs",
        ["reconciliation_candidate_id"],
    )
    op.create_table(
        "structured_dependency_packages",
        sa.Column(
            "input_run_id",
            sa.String(64),
            sa.ForeignKey("structured_input_runs.input_run_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("package_id", sa.String(255), primary_key=True),
        sa.Column("version", sa.String(200), primary_key=True),
        sa.Column("direct", sa.Boolean(), nullable=False),
        sa.Column("minimum_depth", sa.Integer(), nullable=False),
        sa.Column(
            "registry_metadata_artifact_sha256",
            sa.String(64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "package_artifact_sha256",
            sa.String(64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("registry_url", sa.Text(), nullable=False),
        sa.Column("tarball_url", sa.Text(), nullable=False),
        sa.Column("registry_sha1", sa.String(40), nullable=True),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "length(registry_metadata_artifact_sha256) = 64 AND "
            "length(package_artifact_sha256) = 64 AND length(manifest_sha256) = 64",
            name="ck_structured_dependency_packages_digests",
        ),
        sa.CheckConstraint(
            "byte_size > 0 AND minimum_depth >= 1",
            name="ck_structured_dependency_packages_sizes",
        ),
    )
    op.create_table(
        "structured_narrative_artifacts",
        sa.Column(
            "input_run_id",
            sa.String(64),
            sa.ForeignKey("structured_input_runs.input_run_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("link_id", sa.String(200), primary_key=True),
        sa.Column("asset_id", sa.String(200), primary_key=True),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("configured_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(200), nullable=False),
        sa.Column(
            "artifact_sha256",
            sa.String(64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "role IN ('PRIMARY', 'ANNEX')",
            name="ck_structured_narrative_artifacts_role",
        ),
        sa.CheckConstraint(
            "length(artifact_sha256) = 64 AND byte_size > 0",
            name="ck_structured_narrative_artifacts_digest",
        ),
    )
    for table_name in (
        "structured_input_runs",
        "structured_dependency_packages",
        "structured_narrative_artifacts",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reject_steward_immutable_change()
            """
        )
    op.add_column(
        "structured_package_runs",
        sa.Column("structured_input_run_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "structured_package_runs",
        sa.Column("input_closure_sha256", sa.String(64), nullable=True),
    )
    op.create_foreign_key(
        "fk_structured_package_runs_input_run",
        "structured_package_runs",
        "structured_input_runs",
        ["structured_input_run_id"],
        ["input_run_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_structured_package_runs_input_closure",
        "structured_package_runs",
        "(structured_input_run_id IS NULL AND input_closure_sha256 IS NULL) OR "
        "(structured_input_run_id IS NOT NULL AND length(input_closure_sha256) = 64)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_structured_package_runs_input_closure",
        "structured_package_runs",
        type_="check",
    )
    op.drop_constraint(
        "fk_structured_package_runs_input_run",
        "structured_package_runs",
        type_="foreignkey",
    )
    op.drop_column("structured_package_runs", "input_closure_sha256")
    op.drop_column("structured_package_runs", "structured_input_run_id")
    for table_name in (
        "structured_narrative_artifacts",
        "structured_dependency_packages",
        "structured_input_runs",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.drop_table("structured_narrative_artifacts")
    op.drop_table("structured_dependency_packages")
    op.drop_index(
        "ix_structured_input_runs_reconciliation_candidate_id",
        table_name="structured_input_runs",
    )
    op.drop_table("structured_input_runs")
    op.drop_constraint("ck_steward_artifacts_kind", "steward_artifacts", type_="check")
    op.create_check_constraint(
        "ck_steward_artifacts_kind",
        "steward_artifacts",
        "kind IN ('INVENTORY_RESPONSE', 'INVENTORY_MANIFEST', 'SOURCE', "
        "'RELEASE_CANDIDATE', 'STRUCTURED_REPORT')",
    )
