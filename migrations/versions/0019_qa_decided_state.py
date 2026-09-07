"""Let a QA run decide without promoting.

Revision ID: 0019_qa_decided_state
Revises: 0018_composite_release_assembly
Create Date: 2026-08-30

Serving holds one release, so a multi-document corpus must arrive as one - but QA promotes
as it decides, and promotion binds evidence to a release id inside the signed record. A
per-document run therefore promotes under its own release and can never be re-pointed at a
composite. Deciding and promoting have to be separable acts. See
docs/qa-promotion-separation.md.

`DECIDED` is the durable condition of a member waiting for the rest of its composite: every
check a `VALIDATED` run has passed, with its decision batch sealed and attested, but not yet
told which release it belongs to.

The second constraint makes the state and the columns agree in both directions, so a run
cannot claim to be promoted without a bundle, or carry a bundle while claiming it is not.
Existing rows are unaffected: every one is either `PREPARED` with no release or `VALIDATED`
with all three columns set, which is exactly what the constraint requires.
"""

from alembic import op

revision = "0019_qa_decided_state"
down_revision = "0018_composite_release_assembly"
branch_labels = None
depends_on = None

# Postgres generated this name; read from the live schema rather than reconstructed.
_BUNDLE_UNIQUE = "corpus_qa_runs_bundle_sha256_key"

_PROMOTION = (
    "(state = 'VALIDATED') = ("
    "corpus_release_id IS NOT NULL AND bundle_sha256 IS NOT NULL "
    "AND bundle_artifact_sha256 IS NOT NULL)"
)


def upgrade() -> None:
    # One run meant one bundle, so `bundle_sha256` was unique. Composite promotion gives
    # every member of a release the same bundle, and the constraint would admit only the
    # first member and reject the rest.
    op.drop_constraint(_BUNDLE_UNIQUE, "corpus_qa_runs", type_="unique")
    op.drop_constraint("ck_corpus_qa_runs_state", "corpus_qa_runs", type_="check")
    op.create_check_constraint(
        "ck_corpus_qa_runs_state",
        "corpus_qa_runs",
        "state IN ('PREPARED', 'DECIDED', 'VALIDATED')",
    )
    op.create_check_constraint(
        "ck_corpus_qa_runs_promotion", "corpus_qa_runs", _PROMOTION
    )


def downgrade() -> None:
    # Refuses rather than rewriting history. A DECIDED run has a sealed, attested decision
    # batch; forcing it back to PREPARED would claim those decisions were never made, and
    # forcing it to VALIDATED would claim a promotion that never happened.
    from sqlalchemy import text

    decided = (
        op.get_bind()
        .execute(text("SELECT count(*) FROM corpus_qa_runs WHERE state = 'DECIDED'"))
        .scalar_one()
    )
    if decided:
        raise RuntimeError(
            f"{decided} QA run(s) are DECIDED but not promoted; downgrading would have to "
            "misrepresent their state"
        )
    op.create_unique_constraint(_BUNDLE_UNIQUE, "corpus_qa_runs", ["bundle_sha256"])
    op.drop_constraint("ck_corpus_qa_runs_promotion", "corpus_qa_runs", type_="check")
    op.drop_constraint("ck_corpus_qa_runs_state", "corpus_qa_runs", type_="check")
    op.create_check_constraint(
        "ck_corpus_qa_runs_state",
        "corpus_qa_runs",
        "state IN ('PREPARED', 'VALIDATED')",
    )
