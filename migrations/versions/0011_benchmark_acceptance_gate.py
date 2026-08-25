"""Bind sealed benchmark acceptance to release activation.

Revision ID: 0011_benchmark_acceptance_gate
Revises: 0010_automated_evidence_qa
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_benchmark_acceptance_gate"
down_revision = "0010_automated_evidence_qa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_cryptographic_attestations_purpose",
        "cryptographic_attestations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_cryptographic_attestations_purpose",
        "cryptographic_attestations",
        "purpose IN ('STAGE', 'EXCEPTION', 'BENCHMARK_ACCEPTANCE', 'ACTIVATION')",
    )
    op.add_column(
        "corpus_releases",
        sa.Column("benchmark_acceptance_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "corpus_releases",
        sa.Column("benchmark_accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "corpus_releases",
        sa.Column("benchmark_valid_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_corpus_releases_benchmark_acceptance_sha256",
        "corpus_releases",
        ["benchmark_acceptance_sha256"],
    )
    op.create_check_constraint(
        "ck_corpus_releases_benchmark_acceptance",
        "corpus_releases",
        "(benchmark_acceptance_sha256 IS NULL AND benchmark_accepted_at IS NULL "
        "AND benchmark_valid_until IS NULL) OR "
        "(length(benchmark_acceptance_sha256) = 64 "
        "AND benchmark_accepted_at IS NOT NULL AND benchmark_valid_until IS NOT NULL)",
    )
    op.create_table(
        "benchmark_acceptances",
        sa.Column("acceptance_id", sa.String(length=64), primary_key=True),
        sa.Column("statement_sha256", sa.String(length=64), nullable=False, unique=True),
        sa.Column("signature_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "corpus_release_id",
            sa.String(length=64),
            sa.ForeignKey("corpus_releases.corpus_release_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("benchmark_suite_sha256", sa.String(length=64), nullable=False),
        sa.Column("benchmark_report_sha256", sa.String(length=64), nullable=False),
        sa.Column("candidate_configuration_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("vector_batch_sha256", sa.String(length=64), nullable=False),
        sa.Column("qdrant_collection", sa.String(length=255), nullable=False),
        sa.Column("index_attestation_sha256", sa.String(length=64), nullable=False),
        sa.Column("runner_version", sa.String(length=100), nullable=False),
        sa.Column("signing_key_id", sa.String(length=300), nullable=False),
        sa.Column("signer_identity", sa.String(length=300), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(statement_sha256) = 64 AND length(signature_sha256) = 64 "
            "AND length(benchmark_suite_sha256) = 64 "
            "AND length(benchmark_report_sha256) = 64 "
            "AND length(candidate_configuration_sha256) = 64 "
            "AND length(manifest_sha256) = 64 "
            "AND length(vector_batch_sha256) = 64 "
            "AND length(index_attestation_sha256) = 64",
            name="ck_benchmark_acceptance_digests",
        ),
        sa.CheckConstraint(
            "valid_until > accepted_at",
            name="ck_benchmark_acceptance_window",
        ),
    )


def downgrade() -> None:
    op.drop_table("benchmark_acceptances")
    op.drop_constraint(
        "ck_corpus_releases_benchmark_acceptance", "corpus_releases", type_="check"
    )
    op.drop_constraint(
        "uq_corpus_releases_benchmark_acceptance_sha256",
        "corpus_releases",
        type_="unique",
    )
    op.drop_column("corpus_releases", "benchmark_valid_until")
    op.drop_column("corpus_releases", "benchmark_accepted_at")
    op.drop_column("corpus_releases", "benchmark_acceptance_sha256")
    op.drop_constraint(
        "ck_cryptographic_attestations_purpose",
        "cryptographic_attestations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_cryptographic_attestations_purpose",
        "cryptographic_attestations",
        "purpose IN ('STAGE', 'EXCEPTION', 'ACTIVATION')",
    )
