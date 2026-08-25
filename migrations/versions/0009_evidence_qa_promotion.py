"""Add immutable evidence QA decisions and release promotion workflow.

Revision ID: 0009_evidence_qa_promotion
Revises: 0008_authority_materialization
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_evidence_qa_promotion"
down_revision = "0008_authority_materialization"
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
        "'CORPUS_RELEASE_CANDIDATE', 'EVIDENCE_ARTIFACT_MANIFEST', "
        "'QA_DECISION_BATCH', 'CANONICAL_EVIDENCE_RECORD', "
        "'CORPUS_RELEASE_BUNDLE')",
    )
    op.create_table(
        "corpus_qa_runs",
        sa.Column("qa_run_id", sa.String(64), primary_key=True),
        sa.Column(
            "corpus_release_candidate_id", sa.String(64), nullable=False, unique=True
        ),
        sa.Column(
            "materialization_run_id",
            sa.String(64),
            sa.ForeignKey(
                "materialization_runs.materialization_run_id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("corpus_release_candidate_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column(
            "evidence_manifest_sha256", sa.String(64), nullable=False, unique=True
        ),
        sa.Column("evidence_manifest", sa.JSON(), nullable=False),
        sa.Column(
            "evidence_manifest_artifact_sha256",
            sa.String(64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "evidence_manifest_attestation_id",
            sa.String(64),
            sa.ForeignKey(
                "cryptographic_attestations.attestation_id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("decision_batch_sha256", sa.String(64), nullable=True, unique=True),
        sa.Column("decision_batch", sa.JSON(), nullable=True),
        sa.Column(
            "decision_batch_artifact_sha256",
            sa.String(64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "decision_batch_attestation_id",
            sa.String(64),
            sa.ForeignKey(
                "cryptographic_attestations.attestation_id", ondelete="RESTRICT"
            ),
            nullable=True,
        ),
        sa.Column("approved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "quarantined_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "corpus_release_id",
            sa.String(64),
            sa.ForeignKey("corpus_releases.corpus_release_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("bundle_sha256", sa.String(64), nullable=True, unique=True),
        sa.Column(
            "bundle_artifact_sha256",
            sa.String(64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('AWAITING_DECISIONS', 'VALIDATED')",
            name="ck_corpus_qa_runs_state",
        ),
        sa.CheckConstraint(
            "evidence_count > 0 AND approved_count >= 0 AND quarantined_count >= 0",
            name="ck_corpus_qa_runs_counts",
        ),
        sa.CheckConstraint(
            "length(corpus_release_candidate_sha256) = 64 "
            "AND length(evidence_manifest_sha256) = 64",
            name="ck_corpus_qa_runs_digests",
        ),
    )
    op.create_table(
        "evidence_anchor_replays",
        sa.Column(
            "qa_run_id",
            sa.String(64),
            sa.ForeignKey("corpus_qa_runs.qa_run_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("evidence_id", sa.String(64), primary_key=True),
        sa.Column("materialized_evidence_sha256", sa.String(64), nullable=False),
        sa.Column("source_artifact_sha256", sa.String(64), nullable=False),
        sa.Column("replay_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('PASS', 'FAIL')", name="ck_evidence_anchor_replays_outcome"
        ),
        sa.CheckConstraint(
            "length(materialized_evidence_sha256) = 64 "
            "AND length(source_artifact_sha256) = 64 "
            "AND length(replay_sha256) = 64",
            name="ck_evidence_anchor_replays_digests",
        ),
    )
    op.create_table(
        "evidence_qa_decisions",
        sa.Column(
            "qa_run_id",
            sa.String(64),
            sa.ForeignKey("corpus_qa_runs.qa_run_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("evidence_id", sa.String(64), primary_key=True),
        sa.Column("materialized_evidence_sha256", sa.String(64), nullable=False),
        sa.Column("decision_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("disposition", sa.String(16), nullable=False),
        sa.Column("reviewer_identity", sa.String(300), nullable=False),
        sa.Column(
            "batch_attestation_id",
            sa.String(64),
            sa.ForeignKey(
                "cryptographic_attestations.attestation_id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "disposition IN ('APPROVE', 'QUARANTINE')",
            name="ck_evidence_qa_decisions_disposition",
        ),
        sa.CheckConstraint(
            "length(materialized_evidence_sha256) = 64 AND length(decision_sha256) = 64",
            name="ck_evidence_qa_decisions_digests",
        ),
    )
    for table_name in ("evidence_anchor_replays", "evidence_qa_decisions"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reject_steward_immutable_change()
            """
        )
    op.execute(
        """
        CREATE FUNCTION reject_qa_run_provenance_change()
        RETURNS trigger AS $$
        BEGIN
          IF OLD.qa_run_id IS DISTINCT FROM NEW.qa_run_id
             OR OLD.corpus_release_candidate_id IS DISTINCT FROM NEW.corpus_release_candidate_id
             OR OLD.materialization_run_id IS DISTINCT FROM NEW.materialization_run_id
             OR OLD.corpus_release_candidate_sha256 IS DISTINCT FROM NEW.corpus_release_candidate_sha256
             OR OLD.evidence_manifest_sha256 IS DISTINCT FROM NEW.evidence_manifest_sha256
             OR OLD.evidence_manifest::jsonb IS DISTINCT FROM NEW.evidence_manifest::jsonb
             OR OLD.evidence_manifest_artifact_sha256 IS DISTINCT FROM NEW.evidence_manifest_artifact_sha256
             OR OLD.evidence_manifest_attestation_id IS DISTINCT FROM NEW.evidence_manifest_attestation_id
             OR OLD.evidence_count IS DISTINCT FROM NEW.evidence_count
             OR OLD.started_at IS DISTINCT FROM NEW.started_at THEN
            RAISE EXCEPTION 'immutable QA provenance cannot be changed';
          END IF;
          IF OLD.state = 'VALIDATED' AND OLD IS DISTINCT FROM NEW THEN
            RAISE EXCEPTION 'validated QA run cannot be changed';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_corpus_qa_runs_provenance_immutable
        BEFORE UPDATE ON corpus_qa_runs
        FOR EACH ROW EXECUTE FUNCTION reject_qa_run_provenance_change()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_corpus_qa_runs_delete_immutable
        BEFORE DELETE ON corpus_qa_runs
        FOR EACH ROW EXECUTE FUNCTION reject_steward_immutable_change()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_corpus_qa_runs_delete_immutable ON corpus_qa_runs"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_corpus_qa_runs_provenance_immutable ON corpus_qa_runs"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_qa_run_provenance_change()")
    for table_name in ("evidence_qa_decisions", "evidence_anchor_replays"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.drop_table("evidence_qa_decisions")
    op.drop_table("evidence_anchor_replays")
    op.drop_table("corpus_qa_runs")
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
