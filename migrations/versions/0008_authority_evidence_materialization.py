"""Add signed authority composition and evidence materialization.

Revision ID: 0008_authority_materialization
Revises: 0007_structured_input_closures
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_authority_materialization"
down_revision = "0007_structured_input_closures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_steward_artifacts_kind", "steward_artifacts", type_="check")
    op.create_check_constraint(
        "ck_steward_artifacts_kind",
        "steward_artifacts",
        "kind IN ('INVENTORY_RESPONSE', 'INVENTORY_MANIFEST', 'SOURCE', "
        "'RELEASE_CANDIDATE', 'STRUCTURED_REPORT', 'DEPENDENCY_METADATA', "
        "'DEPENDENCY_PACKAGE', 'NARRATIVE_SOURCE', 'INPUT_CLOSURE_REPORT', "
        "'AUTHORITY_BINDING', 'EVIDENCE_RECORD', 'MATERIALIZATION_REPORT', "
        "'CORPUS_RELEASE_CANDIDATE')",
    )
    op.create_table(
        "materialization_runs",
        sa.Column("materialization_run_id", sa.String(64), primary_key=True),
        sa.Column(
            "reconciliation_candidate_id",
            sa.String(64),
            sa.ForeignKey(
                "reconciliation_release_candidates.candidate_id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column(
            "input_run_id",
            sa.String(64),
            sa.ForeignKey("structured_input_runs.input_run_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "structured_run_id",
            sa.String(64),
            sa.ForeignKey(
                "structured_package_runs.structured_run_id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("materializer_name", sa.String(100), nullable=False),
        sa.Column("materializer_version", sa.String(100), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("authority_binding_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("authority_binding", sa.JSON(), nullable=False),
        sa.Column(
            "authority_binding_artifact_sha256",
            sa.String(64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "authority_attestation_id",
            sa.String(64),
            sa.ForeignKey(
                "cryptographic_attestations.attestation_id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("report_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column(
            "report_artifact_sha256",
            sa.String(64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("corpus_candidate_sha256", sa.String(64), nullable=True),
        sa.Column("corpus_candidate", sa.JSON(), nullable=True),
        sa.Column(
            "corpus_candidate_artifact_sha256",
            sa.String(64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "corpus_candidate_attestation_id",
            sa.String(64),
            sa.ForeignKey(
                "cryptographic_attestations.attestation_id", ondelete="RESTRICT"
            ),
            nullable=True,
        ),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "reconciliation_candidate_id",
            "materializer_name",
            "materializer_version",
        ),
        sa.CheckConstraint(
            "state IN ('READY_FOR_QA', 'BLOCKED')",
            name="ck_materialization_runs_state",
        ),
        sa.CheckConstraint(
            "length(authority_binding_sha256) = 64 AND length(report_sha256) = 64",
            name="ck_materialization_runs_digests",
        ),
        sa.CheckConstraint(
            "evidence_count >= 0", name="ck_materialization_runs_evidence_count"
        ),
    )
    op.create_index(
        "ix_materialization_runs_reconciliation_candidate_id",
        "materialization_runs",
        ["reconciliation_candidate_id"],
    )
    op.create_table(
        "materialized_evidence_records",
        sa.Column(
            "materialization_run_id",
            sa.String(64),
            sa.ForeignKey(
                "materialization_runs.materialization_run_id", ondelete="RESTRICT"
            ),
            primary_key=True,
        ),
        sa.Column("evidence_id", sa.String(64), primary_key=True),
        sa.Column("asset_id", sa.String(200), nullable=False),
        sa.Column("source_unit_id", sa.String(500), nullable=False),
        sa.Column(
            "source_artifact_sha256",
            sa.String(64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("evidence_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "artifact_sha256",
            sa.String(64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(evidence_sha256) = 64 AND length(source_artifact_sha256) = 64",
            name="ck_materialized_evidence_digests",
        ),
    )
    op.create_index(
        "ix_materialized_evidence_records_asset_id",
        "materialized_evidence_records",
        ["asset_id"],
    )
    for table_name in ("materialization_runs", "materialized_evidence_records"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reject_steward_immutable_change()
            """
        )


def downgrade() -> None:
    for table_name in ("materialized_evidence_records", "materialization_runs"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.drop_index(
        "ix_materialized_evidence_records_asset_id",
        table_name="materialized_evidence_records",
    )
    op.drop_table("materialized_evidence_records")
    op.drop_index(
        "ix_materialization_runs_reconciliation_candidate_id",
        table_name="materialization_runs",
    )
    op.drop_table("materialization_runs")
    op.drop_constraint("ck_steward_artifacts_kind", "steward_artifacts", type_="check")
    op.create_check_constraint(
        "ck_steward_artifacts_kind",
        "steward_artifacts",
        "kind IN ('INVENTORY_RESPONSE', 'INVENTORY_MANIFEST', 'SOURCE', "
        "'RELEASE_CANDIDATE', 'STRUCTURED_REPORT', 'DEPENDENCY_METADATA', "
        "'DEPENDENCY_PACKAGE', 'NARRATIVE_SOURCE', 'INPUT_CLOSURE_REPORT')",
    )
