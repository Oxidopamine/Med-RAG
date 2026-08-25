"""Replace the external review gate with deterministic evidence QA.

Revision ID: 0010_automated_evidence_qa
Revises: 0009_evidence_qa_promotion
Create Date: 2026-08-25
"""

from alembic import op

revision = "0010_automated_evidence_qa"
down_revision = "0009_evidence_qa_promotion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_corpus_qa_runs_state", "corpus_qa_runs", type_="check")
    op.execute(
        "UPDATE corpus_qa_runs SET state = 'PREPARED' "
        "WHERE state = 'AWAITING_DECISIONS'"
    )
    op.create_check_constraint(
        "ck_corpus_qa_runs_state",
        "corpus_qa_runs",
        "state IN ('PREPARED', 'VALIDATED')",
    )
    op.alter_column(
        "evidence_qa_decisions",
        "reviewer_identity",
        new_column_name="decision_authority",
    )


def downgrade() -> None:
    op.alter_column(
        "evidence_qa_decisions",
        "decision_authority",
        new_column_name="reviewer_identity",
    )
    op.drop_constraint("ck_corpus_qa_runs_state", "corpus_qa_runs", type_="check")
    op.execute(
        "UPDATE corpus_qa_runs SET state = 'AWAITING_DECISIONS' "
        "WHERE state = 'PREPARED'"
    )
    op.create_check_constraint(
        "ck_corpus_qa_runs_state",
        "corpus_qa_runs",
        "state IN ('AWAITING_DECISIONS', 'VALIDATED')",
    )
