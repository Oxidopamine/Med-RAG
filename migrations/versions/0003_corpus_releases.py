"""Add immutable corpus releases, active pointer, and transactional outbox.

Revision ID: 0003_corpus_releases
Revises: 0002_registry_safety_constraints
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_corpus_releases"
down_revision = "0002_registry_safety_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "corpus_releases",
        sa.Column("corpus_release_id", sa.String(length=64), nullable=False),
        sa.Column("contract_version", sa.String(length=32), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("previous_release_id", sa.String(length=64), nullable=True),
        sa.Column("qdrant_collection", sa.String(length=255), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("index_status", sa.String(length=32), nullable=False),
        sa.Column("index_point_count", sa.Integer(), nullable=True),
        sa.Column("index_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_by", sa.String(length=300), nullable=True),
        sa.Column("activation_decision_sha256", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('CANDIDATE', 'VALIDATED', 'ACTIVE', 'SUPERSEDED', 'REJECTED')",
            name="ck_corpus_releases_state",
        ),
        sa.CheckConstraint(
            "index_status IN ('NOT_BUILT', 'VALIDATED')",
            name="ck_corpus_releases_index_status",
        ),
        sa.CheckConstraint(
            "length(manifest_sha256) = 64",
            name="ck_corpus_releases_manifest_sha256_length",
        ),
        sa.CheckConstraint(
            "index_point_count IS NULL OR index_point_count >= 0",
            name="ck_corpus_releases_index_point_count",
        ),
        sa.CheckConstraint(
            "(index_status = 'NOT_BUILT' AND index_validated_at IS NULL) OR "
            "(index_status = 'VALIDATED' AND index_validated_at IS NOT NULL)",
            name="ck_corpus_releases_index_validation",
        ),
        sa.CheckConstraint(
            "state NOT IN ('ACTIVE', 'SUPERSEDED') OR "
            "(activated_at IS NOT NULL AND activation_decision_sha256 IS NOT NULL)",
            name="ck_corpus_releases_activation",
        ),
        sa.CheckConstraint(
            "activation_decision_sha256 IS NULL OR length(activation_decision_sha256) = 64",
            name="ck_corpus_releases_activation_digest",
        ),
        sa.ForeignKeyConstraint(
            ["previous_release_id"],
            ["corpus_releases.corpus_release_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("corpus_release_id"),
        sa.UniqueConstraint("manifest_sha256"),
        sa.UniqueConstraint("qdrant_collection"),
    )
    op.create_table(
        "canonical_evidence",
        sa.Column("evidence_id", sa.String(length=64), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("source_version_id", sa.String(length=64), nullable=False),
        sa.Column("approval_status", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(evidence_sha256) = 64",
            name="ck_canonical_evidence_sha256_length",
        ),
        sa.CheckConstraint(
            "approval_status IN ('APPROVED', 'QUARANTINED')",
            name="ck_canonical_evidence_approval_status",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.source_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_version_id"], ["source_versions.source_version_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("evidence_id"),
        sa.UniqueConstraint("evidence_sha256"),
    )
    op.create_index(
        op.f("ix_canonical_evidence_source_id"),
        "canonical_evidence",
        ["source_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_canonical_evidence_source_version_id"),
        "canonical_evidence",
        ["source_version_id"],
        unique=False,
    )
    op.create_table(
        "corpus_release_evidence",
        sa.Column("corpus_release_id", sa.String(length=64), nullable=False),
        sa.Column("evidence_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["corpus_release_id"], ["corpus_releases.corpus_release_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["canonical_evidence.evidence_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("corpus_release_id", "evidence_id"),
    )
    op.create_table(
        "corpus_release_exceptions",
        sa.Column("exception_id", sa.String(length=64), nullable=False),
        sa.Column("corpus_release_id", sa.String(length=64), nullable=False),
        sa.Column("trust_root_id", sa.String(length=128), nullable=False),
        sa.Column("inventory_item_id", sa.String(length=512), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("statement_sha256", sa.String(length=64), nullable=False),
        sa.Column("signature_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(statement_sha256) = 64 AND length(signature_sha256) = 64",
            name="ck_corpus_release_exceptions_digests",
        ),
        sa.CheckConstraint(
            "reason IN ('ACCESS', 'LICENSING', 'ACQUISITION', 'VALIDATION')",
            name="ck_corpus_release_exceptions_reason",
        ),
        sa.ForeignKeyConstraint(
            ["corpus_release_id"], ["corpus_releases.corpus_release_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("exception_id"),
    )
    op.create_index(
        op.f("ix_corpus_release_exceptions_corpus_release_id"),
        "corpus_release_exceptions",
        ["corpus_release_id"],
        unique=False,
    )
    op.create_table(
        "active_corpus_release",
        sa.Column("singleton_key", sa.Integer(), nullable=False),
        sa.Column("corpus_release_id", sa.String(length=64), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_by", sa.String(length=300), nullable=False),
        sa.CheckConstraint("singleton_key = 1", name="ck_active_corpus_release_singleton"),
        sa.CheckConstraint(
            "length(manifest_sha256) = 64",
            name="ck_active_corpus_release_manifest_sha256_length",
        ),
        sa.ForeignKeyConstraint(
            ["corpus_release_id"], ["corpus_releases.corpus_release_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("singleton_key"),
        sa.UniqueConstraint("corpus_release_id"),
    )
    op.create_table(
        "outbox_events",
        sa.Column("outbox_event_id", sa.String(length=64), nullable=False),
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("deduplication_key", sa.String(length=300), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("outbox_event_id"),
        sa.UniqueConstraint("deduplication_key"),
    )
    op.create_index(
        op.f("ix_outbox_events_aggregate_id"),
        "outbox_events",
        ["aggregate_id"],
        unique=False,
    )
    op.execute(
        """
        CREATE FUNCTION reject_immutable_corpus_row_change()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'immutable corpus content cannot be changed';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in (
        "canonical_evidence",
        "corpus_release_evidence",
        "corpus_release_exceptions",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reject_immutable_corpus_row_change()
            """
        )
    op.execute(
        """
        CREATE TRIGGER trg_corpus_releases_delete_immutable
        BEFORE DELETE ON corpus_releases
        FOR EACH ROW EXECUTE FUNCTION reject_immutable_corpus_row_change()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_corpus_release_content_change()
        RETURNS trigger AS $$
        BEGIN
          IF OLD.contract_version IS DISTINCT FROM NEW.contract_version
             OR OLD.manifest_sha256 IS DISTINCT FROM NEW.manifest_sha256
             OR OLD.manifest::jsonb IS DISTINCT FROM NEW.manifest::jsonb
             OR OLD.previous_release_id IS DISTINCT FROM NEW.previous_release_id
             OR OLD.qdrant_collection IS DISTINCT FROM NEW.qdrant_collection
             OR OLD.cutoff_at IS DISTINCT FROM NEW.cutoff_at
             OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
            RAISE EXCEPTION 'immutable corpus release content cannot be changed';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_corpus_releases_content_immutable
        BEFORE UPDATE ON corpus_releases
        FOR EACH ROW EXECUTE FUNCTION reject_corpus_release_content_change()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_corpus_releases_content_immutable ON corpus_releases"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_corpus_release_content_change()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_corpus_releases_delete_immutable ON corpus_releases"
    )
    for table_name in (
        "corpus_release_exceptions",
        "corpus_release_evidence",
        "canonical_evidence",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS reject_immutable_corpus_row_change()")
    op.drop_index(op.f("ix_outbox_events_aggregate_id"), table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_table("active_corpus_release")
    op.drop_index(
        op.f("ix_corpus_release_exceptions_corpus_release_id"),
        table_name="corpus_release_exceptions",
    )
    op.drop_table("corpus_release_exceptions")
    op.drop_table("corpus_release_evidence")
    op.drop_index(
        op.f("ix_canonical_evidence_source_version_id"),
        table_name="canonical_evidence",
    )
    op.drop_index(
        op.f("ix_canonical_evidence_source_id"), table_name="canonical_evidence"
    )
    op.drop_table("canonical_evidence")
    op.drop_table("corpus_releases")
