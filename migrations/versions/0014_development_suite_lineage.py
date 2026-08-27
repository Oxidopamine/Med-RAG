"""Add candidate-bound development benchmark suite derivation lineage.

Revision ID: 0014_development_suite_lineage
Revises: 0013_automated_source_benchmarks
Create Date: 2026-08-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_development_suite_lineage"
down_revision = "0013_automated_source_benchmarks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "benchmark_development_suite_derivations",
        sa.Column("suite_sha256", sa.String(length=64), primary_key=True),
        sa.Column(
            "parent_suite_sha256",
            sa.String(length=64),
            sa.ForeignKey(
                "automated_benchmark_suite_builds.suite_sha256",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("candidate_configuration_sha256", sa.String(length=64), nullable=False),
        sa.Column("access_policy_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "corpus_release_id",
            sa.String(length=64),
            sa.ForeignKey("corpus_releases.corpus_release_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "artifact_sha256",
            sa.String(length=64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("derived_by", sa.String(length=300), nullable=False),
        sa.Column("derived_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("parent_suite_sha256", "candidate_configuration_sha256"),
        sa.CheckConstraint(
            "length(suite_sha256) = 64 "
            "AND length(parent_suite_sha256) = 64 "
            "AND length(candidate_configuration_sha256) = 64 "
            "AND length(access_policy_sha256) = 64 "
            "AND length(manifest_sha256) = 64",
            name="ck_benchmark_development_suite_derivations_digests",
        ),
        sa.CheckConstraint(
            "case_count > 0",
            name="ck_benchmark_development_suite_derivations_case_count",
        ),
    )


def downgrade() -> None:
    op.drop_table("benchmark_development_suite_derivations")
