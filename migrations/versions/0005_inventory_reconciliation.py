"""Add trust roots, reconciliation ledger, artifacts, and verified attestations.

Revision ID: 0005_inventory_reconciliation
Revises: 0004_index_attestations
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_inventory_reconciliation"
down_revision = "0004_index_attestations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "steward_signing_keys",
        sa.Column("key_id", sa.String(200), primary_key=True),
        sa.Column("algorithm", sa.String(32), nullable=False),
        sa.Column("signer_identity", sa.String(300), nullable=False),
        sa.Column("purposes", sa.JSON(), nullable=False),
        sa.Column("public_key_pem", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("algorithm = 'ED25519'", name="ck_steward_signing_keys_algorithm"),
    )
    op.create_table(
        "trust_roots",
        sa.Column("trust_root_id", sa.String(128), primary_key=True),
        sa.Column("publisher_id", sa.String(64), nullable=False),
        sa.Column("publisher_name", sa.String(300), nullable=False),
        sa.Column("allowed_domains", sa.JSON(), nullable=False),
        sa.Column("jurisdictions", sa.JSON(), nullable=False),
        sa.Column("product_families", sa.JSON(), nullable=False),
        sa.Column("licensing_policy", sa.JSON(), nullable=False),
        sa.Column("polling_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("connector_name", sa.String(100), nullable=False),
        sa.Column("connector_version", sa.String(100), nullable=False),
        sa.Column("connector_config", sa.JSON(), nullable=False),
        sa.Column("trusted_stage_key_ids", sa.JSON(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("definition_sha256", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(definition_sha256) = 64", name="ck_trust_roots_definition_digest"
        ),
        sa.CheckConstraint(
            "polling_interval_seconds >= 300", name="ck_trust_roots_polling_interval"
        ),
    )
    op.create_table(
        "steward_artifacts",
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(200), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(sha256) = 64", name="ck_steward_artifacts_digest"),
        sa.CheckConstraint("byte_size > 0", name="ck_steward_artifacts_size"),
        sa.CheckConstraint(
            "kind IN ('INVENTORY_RESPONSE', 'INVENTORY_MANIFEST', 'SOURCE', "
            "'RELEASE_CANDIDATE')",
            name="ck_steward_artifacts_kind",
        ),
    )
    op.create_table(
        "reconciliation_jobs",
        sa.Column("job_id", sa.String(64), primary_key=True),
        sa.Column(
            "trust_root_id",
            sa.String(128),
            sa.ForeignKey("trust_roots.trust_root_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(300), nullable=False),
        sa.Column("trust_root_sha256", sa.String(64), nullable=False),
        sa.Column("connector_name", sa.String(100), nullable=False),
        sa.Column("connector_version", sa.String(100), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("current_stage", sa.String(64), nullable=True),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('PENDING', 'RUNNING', 'BLOCKED', 'FAILED', 'COMPLETED')",
            name="ck_reconciliation_jobs_state",
        ),
        sa.UniqueConstraint("trust_root_id", "idempotency_key"),
    )
    op.create_index(
        "ix_reconciliation_jobs_trust_root_id",
        "reconciliation_jobs",
        ["trust_root_id"],
    )
    op.create_table(
        "reconciliation_job_attempts",
        sa.Column("attempt_id", sa.String(64), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(64),
            sa.ForeignKey("reconciliation_jobs.job_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("resumed_from_stage", sa.String(64), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_number >= 1", name="ck_reconciliation_attempt_number"),
        sa.CheckConstraint(
            "state IN ('RUNNING', 'FAILED', 'BLOCKED', 'COMPLETED')",
            name="ck_reconciliation_attempt_state",
        ),
        sa.UniqueConstraint("job_id", "attempt_number"),
    )
    op.create_index(
        "ix_reconciliation_job_attempts_job_id",
        "reconciliation_job_attempts",
        ["job_id"],
    )
    op.create_table(
        "cryptographic_attestations",
        sa.Column("attestation_id", sa.String(64), primary_key=True),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("predicate_type", sa.String(200), nullable=False),
        sa.Column("statement_sha256", sa.String(64), nullable=False),
        sa.Column("signature_sha256", sa.String(64), nullable=False),
        sa.Column("signature_base64", sa.Text(), nullable=False),
        sa.Column(
            "signing_key_id",
            sa.String(200),
            sa.ForeignKey("steward_signing_keys.key_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("signer_identity", sa.String(300), nullable=False),
        sa.Column("statement", sa.JSON(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('STAGE', 'EXCEPTION', 'ACTIVATION')",
            name="ck_cryptographic_attestations_purpose",
        ),
        sa.CheckConstraint(
            "length(statement_sha256) = 64 AND length(signature_sha256) = 64",
            name="ck_cryptographic_attestations_digests",
        ),
        sa.UniqueConstraint("statement_sha256", "signature_sha256", "purpose"),
    )
    op.create_index(
        "ix_cryptographic_attestations_statement_sha256",
        "cryptographic_attestations",
        ["statement_sha256"],
    )
    op.create_table(
        "reconciliation_stages",
        sa.Column("stage_run_id", sa.String(64), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(64),
            sa.ForeignKey("reconciliation_jobs.job_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_sha256", sa.String(64), nullable=True),
        sa.Column("output_sha256", sa.String(64), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column(
            "attestation_id",
            sa.String(64),
            sa.ForeignKey("cryptographic_attestations.attestation_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('PENDING', 'RUNNING', 'FAILED', 'COMPLETED')",
            name="ck_reconciliation_stages_state",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_reconciliation_stage_attempts"),
        sa.UniqueConstraint("job_id", "stage"),
    )
    op.create_index(
        "ix_reconciliation_stages_job_id", "reconciliation_stages", ["job_id"]
    )
    op.create_table(
        "reconciliation_inventories",
        sa.Column("inventory_id", sa.String(64), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(64),
            sa.ForeignKey("reconciliation_jobs.job_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "trust_root_id",
            sa.String(128),
            sa.ForeignKey("trust_roots.trust_root_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "inventory_artifact_sha256",
            sa.String(64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("item_count >= 0", name="ck_reconciliation_inventory_count"),
        sa.CheckConstraint(
            "length(inventory_artifact_sha256) = 64",
            name="ck_reconciliation_inventory_digest",
        ),
    )
    op.create_index(
        "ix_reconciliation_inventories_trust_root_id",
        "reconciliation_inventories",
        ["trust_root_id"],
    )
    op.create_table(
        "reconciliation_inventory_items",
        sa.Column(
            "inventory_id",
            sa.String(64),
            sa.ForeignKey("reconciliation_inventories.inventory_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("item_id", sa.String(512), primary_key=True),
        sa.Column("fingerprint_sha256", sa.String(64), nullable=False),
        sa.Column("item", sa.JSON(), nullable=False),
        sa.Column("change_kind", sa.String(32), nullable=False),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column(
            "artifact_sha256",
            sa.String(64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("requested_url", sa.Text(), nullable=True),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocker", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "change_kind IN ('NEW', 'CHANGED', 'UNCHANGED')",
            name="ck_reconciliation_inventory_item_change",
        ),
        sa.CheckConstraint(
            "disposition IN ('INCLUDED', 'EXCEPTED', 'BLOCKED')",
            name="ck_reconciliation_inventory_item_disposition",
        ),
    )
    op.create_table(
        "reconciliation_exceptions",
        sa.Column("exception_id", sa.String(64), primary_key=True),
        sa.Column(
            "trust_root_id",
            sa.String(128),
            sa.ForeignKey("trust_roots.trust_root_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("inventory_item_id", sa.String(512), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("statement_sha256", sa.String(64), nullable=False),
        sa.Column("signature_sha256", sa.String(64), nullable=False),
        sa.Column(
            "attestation_id",
            sa.String(64),
            sa.ForeignKey("cryptographic_attestations.attestation_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "reason IN ('ACCESS', 'LICENSING', 'ACQUISITION', 'VALIDATION')",
            name="ck_reconciliation_exceptions_reason",
        ),
        sa.CheckConstraint(
            "length(statement_sha256) = 64 AND length(signature_sha256) = 64",
            name="ck_reconciliation_exceptions_digests",
        ),
    )
    op.create_index(
        "ix_reconciliation_exceptions_trust_root_id",
        "reconciliation_exceptions",
        ["trust_root_id"],
    )
    op.create_index(
        "ix_reconciliation_exceptions_inventory_item_id",
        "reconciliation_exceptions",
        ["inventory_item_id"],
    )
    op.create_table(
        "reconciliation_release_candidates",
        sa.Column("candidate_id", sa.String(64), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(64),
            sa.ForeignKey("reconciliation_jobs.job_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("candidate_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "artifact_sha256",
            sa.String(64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "stage_attestation_id",
            sa.String(64),
            sa.ForeignKey("cryptographic_attestations.attestation_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(candidate_sha256) = 64", name="ck_reconciliation_candidate_digest"
        ),
    )

    op.execute(
        """
        CREATE FUNCTION reject_steward_immutable_change()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'immutable steward record cannot be changed';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in (
        "steward_artifacts",
        "cryptographic_attestations",
        "reconciliation_exceptions",
        "reconciliation_release_candidates",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reject_steward_immutable_change()
            """
        )
    op.execute(
        """
        CREATE FUNCTION reject_steward_signing_key_material_change()
        RETURNS trigger AS $$
        BEGIN
          IF OLD.algorithm IS DISTINCT FROM NEW.algorithm
             OR OLD.signer_identity IS DISTINCT FROM NEW.signer_identity
             OR OLD.purposes::jsonb IS DISTINCT FROM NEW.purposes::jsonb
             OR OLD.public_key_pem IS DISTINCT FROM NEW.public_key_pem
             OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
            RAISE EXCEPTION 'steward signing key material cannot be changed';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_steward_signing_keys_material_immutable
        BEFORE UPDATE ON steward_signing_keys
        FOR EACH ROW EXECUTE FUNCTION reject_steward_signing_key_material_change()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_steward_signing_keys_material_immutable "
        "ON steward_signing_keys"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_steward_signing_key_material_change()")
    for table_name in (
        "reconciliation_release_candidates",
        "reconciliation_exceptions",
        "cryptographic_attestations",
        "steward_artifacts",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS reject_steward_immutable_change()")
    op.drop_table("reconciliation_release_candidates")
    op.drop_index(
        "ix_reconciliation_exceptions_inventory_item_id",
        table_name="reconciliation_exceptions",
    )
    op.drop_index(
        "ix_reconciliation_exceptions_trust_root_id",
        table_name="reconciliation_exceptions",
    )
    op.drop_table("reconciliation_exceptions")
    op.drop_table("reconciliation_inventory_items")
    op.drop_index(
        "ix_reconciliation_inventories_trust_root_id",
        table_name="reconciliation_inventories",
    )
    op.drop_table("reconciliation_inventories")
    op.drop_index("ix_reconciliation_stages_job_id", table_name="reconciliation_stages")
    op.drop_table("reconciliation_stages")
    op.drop_index(
        "ix_cryptographic_attestations_statement_sha256",
        table_name="cryptographic_attestations",
    )
    op.drop_table("cryptographic_attestations")
    op.drop_index(
        "ix_reconciliation_job_attempts_job_id",
        table_name="reconciliation_job_attempts",
    )
    op.drop_table("reconciliation_job_attempts")
    op.drop_index(
        "ix_reconciliation_jobs_trust_root_id", table_name="reconciliation_jobs"
    )
    op.drop_table("reconciliation_jobs")
    op.drop_table("steward_artifacts")
    op.drop_table("trust_roots")
    op.drop_table("steward_signing_keys")
