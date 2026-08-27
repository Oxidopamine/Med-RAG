"""Separate the excerpt permission from the page-reproduction permission.

Revision ID: 0015_licence_excerpt_permission
Revises: 0014_development_suite_lineage
Create Date: 2026-08-28

`license_render_allowed` conflated two distinct licence acts. Quoting a short attributed
passage and reproducing a page of the work are different questions under CC BY-NC-SA 3.0
IGO, and WHO's publishing policy separates them further by carving out figures, tables
and maps as requiring explicit permission. For a corpus that is almost entirely
spreadsheet and decision-table rows, that carve-out lands on the page image and not on
the excerpt - so the two permissions have different answers and cannot share a column.

`license_excerpt_allowed` is the narrower act: may the passage text be shown.
`license_render_allowed` keeps its name and narrows to the stricter act: may a region of
the source page be reproduced.

Defaults to false, so a source that predates the split reproduces its previous behaviour
of showing nothing until a licence decision is recorded for it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_licence_excerpt_permission"
down_revision = "0014_development_suite_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "license_excerpt_allowed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # A source already cleared for page reproduction is necessarily cleared for the
    # lesser act, so carrying that forward is the only backfill that keeps existing
    # deployments rendering what they rendered before. The reverse is not implied and is
    # not applied.
    op.execute(
        "UPDATE sources SET license_excerpt_allowed = true WHERE license_render_allowed = true"
    )


def downgrade() -> None:
    op.drop_column("sources", "license_excerpt_allowed")
