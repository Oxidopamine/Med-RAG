"""Add automated source-derived benchmark and one-shot execution ledgers.

Revision ID: 0013_automated_source_benchmarks
Revises: 0012_benchmark_adjudication
Create Date: 2026-08-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_automated_source_benchmarks"
down_revision = "0012_benchmark_adjudication"
branch_labels = None
depends_on = None


_ARTIFACT_KINDS_0012 = (
    "'INVENTORY_RESPONSE', 'INVENTORY_MANIFEST', 'SOURCE', 'RELEASE_CANDIDATE', "
    "'STRUCTURED_REPORT', 'DEPENDENCY_METADATA', 'DEPENDENCY_PACKAGE', "
    "'NARRATIVE_SOURCE', 'INPUT_CLOSURE_REPORT', 'AUTHORITY_BINDING', "
    "'EVIDENCE_RECORD', 'MATERIALIZATION_REPORT', 'CORPUS_RELEASE_CANDIDATE', "
    "'EVIDENCE_ARTIFACT_MANIFEST', 'QA_DECISION_BATCH', 'CANONICAL_EVIDENCE_RECORD', "
    "'CORPUS_RELEASE_BUNDLE', 'BENCHMARK_ACCESS_POLICY', "
    "'BENCHMARK_ADJUDICATION_POLICY', 'BENCHMARK_THRESHOLD_POLICY', "
    "'BENCHMARK_REVIEW_DECISION', 'BENCHMARK_RESOLUTION', "
    "'BENCHMARK_ADJUDICATION_RECORD', 'BENCHMARK_SUITE'"
)


def upgrade() -> None:
    op.drop_constraint("ck_steward_artifacts_kind", "steward_artifacts", type_="check")
    op.create_check_constraint(
        "ck_steward_artifacts_kind",
        "steward_artifacts",
        f"kind IN ({_ARTIFACT_KINDS_0012}, 'BENCHMARK_GENERATION_POLICY', "
        "'BENCHMARK_GENERATION_RECORD')",
    )
    op.create_table(
        "benchmark_generation_policies",
        sa.Column("policy_sha256", sa.String(length=64), primary_key=True),
        sa.Column("policy_id", sa.String(length=100), nullable=False),
        sa.Column("revision", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "artifact_sha256",
            sa.String(length=64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("policy_id", "revision"),
        sa.CheckConstraint(
            "length(policy_sha256) = 64",
            name="ck_benchmark_generation_policies_digest",
        ),
    )
    op.create_table(
        "benchmark_generation_records",
        sa.Column("generation_record_sha256", sa.String(length=64), primary_key=True),
        sa.Column("generation_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "generation_policy_sha256",
            sa.String(length=64),
            sa.ForeignKey("benchmark_generation_policies.policy_sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "access_policy_sha256",
            sa.String(length=64),
            sa.ForeignKey("benchmark_policy_artifacts.policy_sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "threshold_policy_sha256",
            sa.String(length=64),
            sa.ForeignKey("benchmark_policy_artifacts.policy_sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "corpus_release_id",
            sa.String(length=64),
            sa.ForeignKey("corpus_releases.corpus_release_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("candidate_configuration_sha256", sa.String(length=64), nullable=True),
        sa.Column("partition_assignment_sha256", sa.String(length=64), nullable=False),
        sa.Column("development_case_count", sa.Integer(), nullable=False),
        sa.Column("sealed_holdout_case_count", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "artifact_sha256",
            sa.String(length=64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("generated_by", sa.String(length=300), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(generation_record_sha256) = 64 "
            "AND length(generation_policy_sha256) = 64 "
            "AND length(access_policy_sha256) = 64 "
            "AND length(threshold_policy_sha256) = 64 "
            "AND length(manifest_sha256) = 64 "
            "AND (candidate_configuration_sha256 IS NULL "
            "OR length(candidate_configuration_sha256) = 64) "
            "AND length(partition_assignment_sha256) = 64",
            name="ck_benchmark_generation_records_digests",
        ),
        sa.CheckConstraint(
            "development_case_count > 0 AND sealed_holdout_case_count > 0",
            name="ck_benchmark_generation_records_counts",
        ),
    )
    op.create_table(
        "automated_benchmark_suite_builds",
        sa.Column("suite_sha256", sa.String(length=64), primary_key=True),
        sa.Column("benchmark_id", sa.String(length=100), nullable=False),
        sa.Column("suite_partition", sa.String(length=32), nullable=False),
        sa.Column(
            "generation_record_sha256",
            sa.String(length=64),
            sa.ForeignKey(
                "benchmark_generation_records.generation_record_sha256",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("generation_policy_sha256", sa.String(length=64), nullable=False),
        sa.Column("access_policy_sha256", sa.String(length=64), nullable=False),
        sa.Column("threshold_policy_sha256", sa.String(length=64), nullable=False),
        sa.Column("candidate_configuration_sha256", sa.String(length=64), nullable=True),
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
        sa.Column("built_by", sa.String(length=300), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("benchmark_id", "suite_partition"),
        sa.UniqueConstraint("generation_record_sha256", "suite_partition"),
        sa.CheckConstraint(
            "suite_partition IN ('DEVELOPMENT', 'SEALED_HOLDOUT')",
            name="ck_automated_benchmark_suite_builds_partition",
        ),
        sa.CheckConstraint(
            "length(suite_sha256) = 64 "
            "AND length(generation_record_sha256) = 64 "
            "AND length(generation_policy_sha256) = 64 "
            "AND length(access_policy_sha256) = 64 "
            "AND length(threshold_policy_sha256) = 64 "
            "AND (candidate_configuration_sha256 IS NULL "
            "OR length(candidate_configuration_sha256) = 64) "
            "AND length(manifest_sha256) = 64",
            name="ck_automated_benchmark_suite_builds_digests",
        ),
        sa.CheckConstraint("case_count > 0", name="ck_automated_benchmark_suite_builds_case_count"),
    )
    op.create_table(
        "benchmark_execution_runs",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        sa.Column("suite_sha256", sa.String(length=64), nullable=False),
        sa.Column("suite_partition", sa.String(length=32), nullable=False),
        sa.Column("candidate_configuration_sha256", sa.String(length=64), nullable=False),
        sa.Column("vector_batch_sha256", sa.String(length=64), nullable=False),
        sa.Column("actor_identity", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("report_sha256", sa.String(length=64), nullable=True),
        sa.Column("report_payload", sa.JSON(), nullable=True),
        sa.Column("failure_class", sa.String(length=200), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "suite_partition IN ('DEVELOPMENT', 'SEALED_HOLDOUT', 'SYNTHETIC')",
            name="ck_benchmark_execution_runs_partition",
        ),
        sa.CheckConstraint(
            "status IN ('STARTED', 'COMPLETED', 'FAILED')",
            name="ck_benchmark_execution_runs_status",
        ),
        sa.CheckConstraint(
            "length(suite_sha256) = 64 "
            "AND length(candidate_configuration_sha256) = 64 "
            "AND length(vector_batch_sha256) = 64 "
            "AND (report_sha256 IS NULL OR length(report_sha256) = 64)",
            name="ck_benchmark_execution_runs_digests",
        ),
    )
    op.create_index(
        "ix_benchmark_execution_runs_suite_sha256",
        "benchmark_execution_runs",
        ["suite_sha256"],
    )
    op.create_index(
        "uq_benchmark_execution_runs_holdout_once",
        "benchmark_execution_runs",
        ["suite_sha256"],
        unique=True,
        postgresql_where=sa.text("suite_partition = 'SEALED_HOLDOUT'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_benchmark_execution_runs_holdout_once", table_name="benchmark_execution_runs"
    )
    op.drop_index("ix_benchmark_execution_runs_suite_sha256", table_name="benchmark_execution_runs")
    op.drop_table("benchmark_execution_runs")
    op.drop_table("automated_benchmark_suite_builds")
    op.drop_table("benchmark_generation_records")
    op.drop_table("benchmark_generation_policies")
    op.drop_constraint("ck_steward_artifacts_kind", "steward_artifacts", type_="check")
    op.create_check_constraint(
        "ck_steward_artifacts_kind",
        "steward_artifacts",
        f"kind IN ({_ARTIFACT_KINDS_0012})",
    )
