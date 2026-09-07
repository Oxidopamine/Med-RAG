"""Record which QA'd documents compose a composite release.

Revision ID: 0018_composite_release_assembly
Revises: 0017_narrative_materialization
Create Date: 2026-08-30

Serving holds one release, so a multi-document corpus has to arrive as one. The manifest
already supported that - `inventory_snapshots` is a tuple validated against repeating a
trust root, and `evidence` is a tuple - so assembly needs no new release schema and reuses
the registrar every single-document release goes through.

What it does need is a registry answer to "which documents are in this release", rather
than one obtainable only by reading a manifest and trusting it.

`qa_run_id` is unique across the whole table, not merely within one release. A QA run
composed into two releases would be indexed and activated under two identities, and
superseding one would leave the other serving.

There is no `member_release_id`: a member has no release of its own. It stops at `DECIDED`
and the composite performs the single promotion (0019, docs/qa-promotion-separation.md).
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_composite_release_assembly"
down_revision = "0017_narrative_materialization"
branch_labels = None
depends_on = None

_KINDS_BEFORE = (
    "kind IN ('INVENTORY_RESPONSE', 'INVENTORY_MANIFEST', 'SOURCE', "
    "'RELEASE_CANDIDATE', 'STRUCTURED_REPORT', 'DEPENDENCY_METADATA', "
    "'DEPENDENCY_PACKAGE', 'NARRATIVE_SOURCE', 'NARRATIVE_ANALYSIS_REPORT', "
    "'INPUT_CLOSURE_REPORT', "
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
    "'CORPUS_RELEASE_BUNDLE', ", "'CORPUS_RELEASE_BUNDLE', 'RELEASE_ASSEMBLY', "
)


def upgrade() -> None:
    op.drop_constraint("ck_steward_artifacts_kind", "steward_artifacts", type_="check")
    op.create_check_constraint(
        "ck_steward_artifacts_kind", "steward_artifacts", _KINDS_AFTER
    )
    op.create_table(
        "corpus_release_members",
        sa.Column(
            "corpus_release_id",
            sa.String(length=64),
            sa.ForeignKey("corpus_releases.corpus_release_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "qa_run_id",
            sa.String(length=64),
            sa.ForeignKey("corpus_qa_runs.qa_run_id", ondelete="RESTRICT"),
            primary_key=True,
            unique=True,
        ),
        sa.Column("assembly_sha256", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "length(assembly_sha256) = 64",
            name="ck_corpus_release_members_assembly_digest",
        ),
    )


def downgrade() -> None:
    op.drop_table("corpus_release_members")
    op.drop_constraint("ck_steward_artifacts_kind", "steward_artifacts", type_="check")
    op.create_check_constraint(
        "ck_steward_artifacts_kind", "steward_artifacts", _KINDS_BEFORE
    )
