"""Admit a narrative materialization run alongside a structured one.

Revision ID: 0017_narrative_materialization
Revises: 0016_narrative_analysis_stage
Create Date: 2026-08-30

`materialization_runs` was shaped for one topology: `structured_run_id` NOT NULL, and one
run per (candidate, materializer). Neither holds for the narrative path. Its prior
structural statement is a narrative census rather than a FHIR package report, and its
candidates carry many items - one WHO NCD candidate carries thirteen guidelines.

Three changes, and one of them is a correctness fix rather than an addition:

- `structured_run_id` becomes nullable and `narrative_run_id` is added, with a check
  constraint requiring exactly one. A run descends from one topology's prior statement or
  the other's, never both and never neither - neither would be a run with nothing to have
  been checked against, which is the whole property the census exists to provide.
- `inventory_item_id` is added and backfilled from the structured run each existing row
  already points at, then made NOT NULL. Both existing rows join cleanly.
- The uniqueness key gains `inventory_item_id`. Without this the narrative materializer
  would find the first document's run and return it as the second document's result -
  silently, and with the wrong evidence. That defect is already recorded against the
  structured run derivation, which cannot be changed because it is committed to signed
  history; this stops the narrative path inheriting it.

No signed content moves. `inventory_item_id` is row metadata; the immutable report,
authority binding and evidence records are unchanged, and their digests with them.
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_narrative_materialization"
down_revision = "0016_narrative_analysis_stage"
branch_labels = None
depends_on = None

# Postgres generated this name and truncated it to 63 characters; it is read from the
# live schema rather than reconstructed, because guessing where the truncation falls is
# how a migration passes review and fails on deploy.
_OLD_UNIQUE = "materialization_runs_reconciliation_candidate_id_materializ_key"
_NEW_UNIQUE = "uq_materialization_runs_item_materializer"


def upgrade() -> None:
    op.add_column(
        "materialization_runs",
        sa.Column("narrative_run_id", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "materialization_runs_narrative_run_id_fkey",
        "materialization_runs",
        "narrative_analysis_runs",
        ["narrative_run_id"],
        ["narrative_run_id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "materialization_runs",
        sa.Column("inventory_item_id", sa.String(length=512), nullable=True),
    )
    # Every existing run is structured and points at a structured package run, which knows
    # the item. Backfilled by join rather than by literal so the migration does not encode
    # this deployment's data.
    #
    # `trg_materialization_runs_immutable` (added in 0008) rejects every UPDATE on this
    # table, so it is suspended for the backfill and restored immediately after. That
    # protection exists to stop the *application* rewriting signed history, and this is not
    # that: `inventory_item_id` is new metadata whose value is already implied by the
    # structured run each row points at, and no signed field - not the report, the
    # authority binding, the digests, or any evidence record - is touched or could be. A
    # migration that needed to change one of those would be the wrong migration.
    op.execute(
        "ALTER TABLE materialization_runs DISABLE TRIGGER trg_materialization_runs_immutable"
    )
    # No try/finally around this. Alembic runs a migration in one transaction, so a failed
    # UPDATE aborts it and every later statement - including a re-enable in a `finally` -
    # fails with "current transaction is aborted", masking the real error behind a
    # meaningless one. The rollback restores the trigger on its own, because DDL here is
    # transactional; letting the original exception surface is what makes the failure
    # diagnosable.
    op.execute(
        """
        UPDATE materialization_runs AS m
        SET inventory_item_id = s.inventory_item_id
        FROM structured_package_runs AS s
        WHERE m.structured_run_id = s.structured_run_id
          AND m.inventory_item_id IS NULL
        """
    )
    op.execute(
        "ALTER TABLE materialization_runs ENABLE TRIGGER trg_materialization_runs_immutable"
    )
    unfilled = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM materialization_runs WHERE inventory_item_id IS NULL"
            )
        )
        .scalar_one()
    )
    if unfilled:
        raise RuntimeError(
            f"{unfilled} materialization run(s) have no resolvable inventory item; "
            "refusing to guess one for a signed run"
        )
    op.alter_column("materialization_runs", "inventory_item_id", nullable=False)
    op.alter_column("materialization_runs", "structured_run_id", nullable=True)

    op.drop_constraint(_OLD_UNIQUE, "materialization_runs", type_="unique")
    op.create_unique_constraint(
        _NEW_UNIQUE,
        "materialization_runs",
        [
            "reconciliation_candidate_id",
            "inventory_item_id",
            "materializer_name",
            "materializer_version",
        ],
    )
    op.create_check_constraint(
        "ck_materialization_runs_one_topology",
        "materialization_runs",
        "(structured_run_id IS NULL) <> (narrative_run_id IS NULL)",
    )


def downgrade() -> None:
    # Refuses rather than destroys. A narrative run has no structured_run_id to restore, so
    # reverting the column to NOT NULL would either fail on the constraint or require
    # deleting signed materialization history to succeed.
    count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM materialization_runs WHERE narrative_run_id IS NOT NULL"
            )
        )
        .scalar_one()
    )
    if count:
        raise RuntimeError(
            f"{count} narrative materialization run(s) exist; "
            "downgrading would discard signed history"
        )
    op.drop_constraint(
        "ck_materialization_runs_one_topology", "materialization_runs", type_="check"
    )
    op.drop_constraint(_NEW_UNIQUE, "materialization_runs", type_="unique")
    op.create_unique_constraint(
        _OLD_UNIQUE,
        "materialization_runs",
        ["reconciliation_candidate_id", "materializer_name", "materializer_version"],
    )
    op.alter_column("materialization_runs", "structured_run_id", nullable=False)
    op.drop_column("materialization_runs", "inventory_item_id")
    op.drop_constraint(
        "materialization_runs_narrative_run_id_fkey",
        "materialization_runs",
        type_="foreignkey",
    )
    op.drop_column("materialization_runs", "narrative_run_id")
