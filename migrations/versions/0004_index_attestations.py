"""Bind index validation and activation to an attestation digest.

Revision ID: 0004_index_attestations
Revises: 0003_corpus_releases
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_index_attestations"
down_revision = "0003_corpus_releases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "corpus_releases",
        sa.Column("index_attestation_sha256", sa.String(length=64), nullable=True),
    )
    op.drop_constraint(
        "ck_corpus_releases_index_validation", "corpus_releases", type_="check"
    )
    op.create_check_constraint(
        "ck_corpus_releases_index_validation",
        "corpus_releases",
        "(index_status = 'NOT_BUILT' AND index_validated_at IS NULL "
        "AND index_attestation_sha256 IS NULL) OR "
        "(index_status = 'VALIDATED' AND index_validated_at IS NOT NULL "
        "AND length(index_attestation_sha256) = 64)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_corpus_releases_index_validation", "corpus_releases", type_="check"
    )
    op.create_check_constraint(
        "ck_corpus_releases_index_validation",
        "corpus_releases",
        "(index_status = 'NOT_BUILT' AND index_validated_at IS NULL) OR "
        "(index_status = 'VALIDATED' AND index_validated_at IS NOT NULL)",
    )
    op.drop_column("corpus_releases", "index_attestation_sha256")
