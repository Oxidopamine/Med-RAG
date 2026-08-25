"""Add immutable clinical benchmark adjudication and suite-build ledgers.

Revision ID: 0012_benchmark_adjudication
Revises: 0011_benchmark_acceptance_gate
Create Date: 2026-08-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_benchmark_adjudication"
down_revision = "0011_benchmark_acceptance_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_steward_artifacts_kind", "steward_artifacts", type_="check"
    )
    op.create_check_constraint(
        "ck_steward_artifacts_kind",
        "steward_artifacts",
        "kind IN ('INVENTORY_RESPONSE', 'INVENTORY_MANIFEST', 'SOURCE', "
        "'RELEASE_CANDIDATE', 'STRUCTURED_REPORT', 'DEPENDENCY_METADATA', "
        "'DEPENDENCY_PACKAGE', 'NARRATIVE_SOURCE', 'INPUT_CLOSURE_REPORT', "
        "'AUTHORITY_BINDING', 'EVIDENCE_RECORD', 'MATERIALIZATION_REPORT', "
        "'CORPUS_RELEASE_CANDIDATE', 'EVIDENCE_ARTIFACT_MANIFEST', "
        "'QA_DECISION_BATCH', 'CANONICAL_EVIDENCE_RECORD', "
        "'CORPUS_RELEASE_BUNDLE', 'BENCHMARK_ACCESS_POLICY', "
        "'BENCHMARK_ADJUDICATION_POLICY', 'BENCHMARK_THRESHOLD_POLICY', "
        "'BENCHMARK_REVIEW_DECISION', 'BENCHMARK_RESOLUTION', "
        "'BENCHMARK_ADJUDICATION_RECORD', 'BENCHMARK_SUITE')",
    )

    op.create_table(
        "benchmark_policy_artifacts",
        sa.Column("policy_sha256", sa.String(length=64), primary_key=True),
        sa.Column("policy_id", sa.String(length=100), nullable=False),
        sa.Column("revision", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "artifact_sha256",
            sa.String(length=64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "policy_id", "revision"),
        sa.CheckConstraint(
            "kind IN ('ACCESS', 'ADJUDICATION_PROCESS', 'THRESHOLD')",
            name="ck_benchmark_policy_artifacts_kind",
        ),
        sa.CheckConstraint(
            "length(policy_sha256) = 64",
            name="ck_benchmark_policy_artifacts_digest",
        ),
    )
    op.create_table(
        "benchmark_review_decisions",
        sa.Column("decision_sha256", sa.String(length=64), primary_key=True),
        sa.Column("review_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("suite_partition", sa.String(length=32), nullable=False),
        sa.Column("adjudicator_identity", sa.String(length=300), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "artifact_sha256",
            sa.String(length=64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "adjudicator_identity"),
        sa.CheckConstraint(
            "suite_partition IN ('DEVELOPMENT', 'SEALED_HOLDOUT')",
            name="ck_benchmark_review_decisions_partition",
        ),
        sa.CheckConstraint(
            "length(decision_sha256) = 64",
            name="ck_benchmark_review_decisions_digest",
        ),
    )
    op.create_index(
        "ix_benchmark_review_decisions_case_id",
        "benchmark_review_decisions",
        ["case_id"],
    )
    op.create_table(
        "benchmark_disagreement_resolutions",
        sa.Column("resolution_sha256", sa.String(length=64), primary_key=True),
        sa.Column("resolution_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("case_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "artifact_sha256",
            sa.String(length=64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(resolution_sha256) = 64",
            name="ck_benchmark_disagreement_resolutions_digest",
        ),
    )
    op.create_table(
        "benchmark_adjudication_records",
        sa.Column("adjudication_record_sha256", sa.String(length=64), primary_key=True),
        sa.Column(
            "adjudication_record_id", sa.String(length=64), nullable=False, unique=True
        ),
        sa.Column(
            "access_policy_sha256",
            sa.String(length=64),
            sa.ForeignKey("benchmark_policy_artifacts.policy_sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "adjudication_process_sha256",
            sa.String(length=64),
            sa.ForeignKey("benchmark_policy_artifacts.policy_sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "artifact_sha256",
            sa.String(length=64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("development_case_count", sa.Integer(), nullable=False),
        sa.Column("sealed_holdout_case_count", sa.Integer(), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(adjudication_record_sha256) = 64 "
            "AND length(access_policy_sha256) = 64 "
            "AND length(adjudication_process_sha256) = 64",
            name="ck_benchmark_adjudication_records_digests",
        ),
        sa.CheckConstraint(
            "development_case_count >= 0 AND sealed_holdout_case_count >= 0 "
            "AND development_case_count + sealed_holdout_case_count > 0",
            name="ck_benchmark_adjudication_records_counts",
        ),
    )
    op.create_table(
        "benchmark_adjudication_cases",
        sa.Column(
            "adjudication_record_sha256",
            sa.String(length=64),
            sa.ForeignKey(
                "benchmark_adjudication_records.adjudication_record_sha256",
                ondelete="RESTRICT",
            ),
            primary_key=True,
        ),
        sa.Column("case_id", sa.String(length=64), primary_key=True),
        sa.Column("suite_partition", sa.String(length=32), nullable=False),
        sa.Column("case_sha256", sa.String(length=64), nullable=False),
        sa.Column("review_decision_sha256s", sa.JSON(), nullable=False),
        sa.Column("resolution_sha256", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "suite_partition IN ('DEVELOPMENT', 'SEALED_HOLDOUT')",
            name="ck_benchmark_adjudication_cases_partition",
        ),
        sa.CheckConstraint(
            "length(case_sha256) = 64",
            name="ck_benchmark_adjudication_cases_digest",
        ),
    )
    op.create_table(
        "benchmark_suite_builds",
        sa.Column("suite_sha256", sa.String(length=64), primary_key=True),
        sa.Column("benchmark_id", sa.String(length=100), nullable=False),
        sa.Column("suite_partition", sa.String(length=32), nullable=False),
        sa.Column(
            "adjudication_record_sha256",
            sa.String(length=64),
            sa.ForeignKey(
                "benchmark_adjudication_records.adjudication_record_sha256",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("access_policy_sha256", sa.String(length=64), nullable=False),
        sa.Column("adjudication_process_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "threshold_policy_sha256",
            sa.String(length=64),
            sa.ForeignKey("benchmark_policy_artifacts.policy_sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_configuration_sha256", sa.String(length=64), nullable=False
        ),
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
        sa.CheckConstraint(
            "suite_partition IN ('DEVELOPMENT', 'SEALED_HOLDOUT')",
            name="ck_benchmark_suite_builds_partition",
        ),
        sa.CheckConstraint(
            "length(suite_sha256) = 64 "
            "AND length(adjudication_record_sha256) = 64 "
            "AND length(access_policy_sha256) = 64 "
            "AND length(adjudication_process_sha256) = 64 "
            "AND length(threshold_policy_sha256) = 64 "
            "AND length(candidate_configuration_sha256) = 64 "
            "AND length(manifest_sha256) = 64",
            name="ck_benchmark_suite_builds_digests",
        ),
        sa.CheckConstraint(
            "case_count > 0", name="ck_benchmark_suite_builds_case_count"
        ),
    )
    op.create_index(
        "uq_benchmark_suite_builds_holdout_once",
        "benchmark_suite_builds",
        ["adjudication_record_sha256"],
        unique=True,
        postgresql_where=sa.text("suite_partition = 'SEALED_HOLDOUT'"),
    )
    op.create_table(
        "benchmark_access_events",
        sa.Column("access_event_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "access_policy_sha256",
            sa.String(length=64),
            sa.ForeignKey("benchmark_policy_artifacts.policy_sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("actor_identity", sa.String(length=300), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_sha256", sa.String(length=64), nullable=False, unique=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('BUILD_DEVELOPMENT_SUITE', 'BUILD_SEALED_HOLDOUT_SUITE')",
            name="ck_benchmark_access_events_action",
        ),
        sa.CheckConstraint(
            "length(access_policy_sha256) = 64 AND length(resource_sha256) = 64",
            name="ck_benchmark_access_events_digests",
        ),
    )


def downgrade() -> None:
    op.drop_table("benchmark_access_events")
    op.drop_index(
        "uq_benchmark_suite_builds_holdout_once", table_name="benchmark_suite_builds"
    )
    op.drop_table("benchmark_suite_builds")
    op.drop_table("benchmark_adjudication_cases")
    op.drop_table("benchmark_adjudication_records")
    op.drop_table("benchmark_disagreement_resolutions")
    op.drop_index(
        "ix_benchmark_review_decisions_case_id",
        table_name="benchmark_review_decisions",
    )
    op.drop_table("benchmark_review_decisions")
    op.drop_table("benchmark_policy_artifacts")
    op.drop_constraint(
        "ck_steward_artifacts_kind", "steward_artifacts", type_="check"
    )
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
