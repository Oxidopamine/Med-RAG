"""Persist the narrative source analysis stage.

Revision ID: 0016_narrative_analysis_stage
Revises: 0015_licence_excerpt_permission
Create Date: 2026-08-30

The narrative census had schemas and a pure processor but nowhere to live, so a signed
census could not be stored and materialization had nothing to fetch and disagree with.
That comparison is the whole reason the stage exists rather than the structured report
simply being made optional, so without this table the narrative report would be a rubber
stamp.

`narrative_analysis_runs` is the peer of `structured_package_runs` and is keyed the same
way, on (candidate, item, processor, version). The item is part of the key deliberately:
the narrative topology is the multi-item one - one WHO NCD candidate carries thirteen
guidelines - and a lookup that ignored `inventory_item_id` would hand back the first
document's census as every other document's, which is precisely the defect already
recorded against the structured materializer's run derivation.

`structured_input_run_id` is nullable, paired with `input_closure_sha256` by a check
constraint so a run can never claim one without the other. Both are present in normal
operation; the nullability mirrors `structured_package_runs`, where a report may be
produced before a closure exists.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_narrative_analysis_stage"
down_revision = "0015_licence_excerpt_permission"
branch_labels = None
depends_on = None


_KINDS_BEFORE = (
    "kind IN ('INVENTORY_RESPONSE', 'INVENTORY_MANIFEST', 'SOURCE', "
    "'RELEASE_CANDIDATE', 'STRUCTURED_REPORT', 'DEPENDENCY_METADATA', "
    "'DEPENDENCY_PACKAGE', 'NARRATIVE_SOURCE', 'INPUT_CLOSURE_REPORT', "
    "'AUTHORITY_BINDING', 'EVIDENCE_RECORD', 'MATERIALIZATION_REPORT', "
    "'CORPUS_RELEASE_CANDIDATE', 'EVIDENCE_ARTIFACT_MANIFEST', "
    "'QA_DECISION_BATCH', 'CANONICAL_EVIDENCE_RECORD', "
    "'CORPUS_RELEASE_BUNDLE', 'BENCHMARK_ACCESS_POLICY', "
    "'BENCHMARK_ADJUDICATION_POLICY', 'BENCHMARK_THRESHOLD_POLICY', "
    "'BENCHMARK_REVIEW_DECISION', 'BENCHMARK_RESOLUTION', "
    "'BENCHMARK_ADJUDICATION_RECORD', 'BENCHMARK_SUITE', "
    "'BENCHMARK_GENERATION_POLICY', 'BENCHMARK_GENERATION_RECORD')"
)

_KINDS_AFTER = _KINDS_BEFORE.replace(
    "'NARRATIVE_SOURCE', ", "'NARRATIVE_SOURCE', 'NARRATIVE_ANALYSIS_REPORT', "
)


def upgrade() -> None:
    # The signed census is a new artifact kind. The content-addressed store admits only
    # kinds named in this constraint, so it has to be widened before the stage can store
    # anything - and widening it is the one place the store's vocabulary is enumerated.
    op.drop_constraint("ck_steward_artifacts_kind", "steward_artifacts", type_="check")
    op.create_check_constraint(
        "ck_steward_artifacts_kind", "steward_artifacts", _KINDS_AFTER
    )

    op.create_table(
        "narrative_analysis_runs",
        sa.Column("narrative_run_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "reconciliation_candidate_id",
            sa.String(length=64),
            sa.ForeignKey(
                "reconciliation_release_candidates.candidate_id", ondelete="RESTRICT"
            ),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "trust_root_id",
            sa.String(length=200),
            sa.ForeignKey("trust_roots.trust_root_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "trust_root_sha256",
            sa.String(length=64),
            sa.ForeignKey(
                "trust_root_revisions.definition_sha256", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("inventory_item_id", sa.String(length=512), nullable=False),
        sa.Column(
            "source_artifact_sha256",
            sa.String(length=64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "structured_input_run_id",
            sa.String(length=64),
            sa.ForeignKey("structured_input_runs.input_run_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("input_closure_sha256", sa.String(length=64), nullable=True),
        sa.Column("processor_name", sa.String(length=100), nullable=False),
        sa.Column("processor_version", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("report_sha256", sa.String(length=64), nullable=False, unique=True),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column(
            "report_artifact_sha256",
            sa.String(length=64),
            sa.ForeignKey("steward_artifacts.sha256", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "attestation_id",
            sa.String(length=64),
            sa.ForeignKey(
                "cryptographic_attestations.attestation_id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("unit_count_total", sa.Integer(), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "reconciliation_candidate_id",
            "inventory_item_id",
            "processor_name",
            "processor_version",
            name="uq_narrative_analysis_runs_item_processor",
        ),
        sa.CheckConstraint(
            "state IN ('VALIDATED', 'BLOCKED')",
            name="ck_narrative_analysis_runs_state",
        ),
        sa.CheckConstraint(
            "length(report_sha256) = 64",
            name="ck_narrative_analysis_runs_report_digest",
        ),
        sa.CheckConstraint(
            "(structured_input_run_id IS NULL AND input_closure_sha256 IS NULL) OR "
            "(structured_input_run_id IS NOT NULL AND length(input_closure_sha256) = 64)",
            name="ck_narrative_analysis_runs_input_closure",
        ),
        sa.CheckConstraint(
            "unit_count_total >= 0 AND document_count >= 0",
            name="ck_narrative_analysis_runs_counts",
        ),
    )


def downgrade() -> None:
    op.drop_table("narrative_analysis_runs")
    op.drop_constraint("ck_steward_artifacts_kind", "steward_artifacts", type_="check")
    op.create_check_constraint(
        "ck_steward_artifacts_kind", "steward_artifacts", _KINDS_BEFORE
    )
