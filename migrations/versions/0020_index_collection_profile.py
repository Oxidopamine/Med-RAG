"""Let a reserved index collection take its candidate vector profile exactly once.

Revision ID: 0020_index_collection_profile
Revises: 0019_qa_decided_state
Create Date: 2026-09-01

QA reserves a release-specific collection *name* and builds nothing. Index attestation is
what selects the candidate-profile collection, so `qdrant-attest` rewrites the reserved
`corpus_cr_<id>` to the profile-suffixed `corpus_cr_<id>--vp-<profile>` in the same
transaction that records `index_status='VALIDATED'`.

`reject_corpus_release_content_change` listed `qdrant_collection` among the fields that can
never change, which refused that write. The two rules contradicted each other, and the
database won: no release could reach `index_status='VALIDATED'` through `qdrant-attest` at
all. The failure was invisible to the suite because tests run on SQLite through
`Base.metadata.create_all`, which creates no triggers, and because no test passed a
profile-suffixed collection through `mark_index_validated`.

The application layer already enforces the correct invariant
(`SQLCorpusReleaseRepository.mark_index_validated` raises
`INDEX_COLLECTION_IDENTITY_MISMATCH` unless the new name is unchanged or extends the
reserved name with `--vp-`). This migration makes the trigger agree with it rather than
loosening the contract: the name may still only ever move from the reserved value to that
one derived form, and only while the release has not yet been index-validated. Once
`index_status='VALIDATED'`, the name is frozen again, so a validated release still cannot
switch profiles.

`starts_with` is used rather than `LIKE` because collection identifiers contain `_`, which
`LIKE` would treat as a single-character wildcard.
"""

from alembic import op

revision = "0020_index_collection_profile"
down_revision = "0019_qa_decided_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_corpus_release_content_change()
        RETURNS trigger AS $$
        BEGIN
          IF OLD.contract_version IS DISTINCT FROM NEW.contract_version
             OR OLD.manifest_sha256 IS DISTINCT FROM NEW.manifest_sha256
             OR OLD.manifest::jsonb IS DISTINCT FROM NEW.manifest::jsonb
             OR OLD.previous_release_id IS DISTINCT FROM NEW.previous_release_id
             OR OLD.cutoff_at IS DISTINCT FROM NEW.cutoff_at
             OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
            RAISE EXCEPTION 'immutable corpus release content cannot be changed';
          END IF;
          IF OLD.qdrant_collection IS DISTINCT FROM NEW.qdrant_collection
             AND NOT (
               OLD.index_status IS DISTINCT FROM 'VALIDATED'
               AND OLD.qdrant_collection IS NOT NULL
               AND NEW.qdrant_collection IS NOT NULL
               AND starts_with(
                 NEW.qdrant_collection, OLD.qdrant_collection || '--vp-'
               )
             ) THEN
            RAISE EXCEPTION
              'corpus release index collection can only take its candidate vector profile once';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_corpus_release_content_change()
        RETURNS trigger AS $$
        BEGIN
          IF OLD.contract_version IS DISTINCT FROM NEW.contract_version
             OR OLD.manifest_sha256 IS DISTINCT FROM NEW.manifest_sha256
             OR OLD.manifest::jsonb IS DISTINCT FROM NEW.manifest::jsonb
             OR OLD.previous_release_id IS DISTINCT FROM NEW.previous_release_id
             OR OLD.qdrant_collection IS DISTINCT FROM NEW.qdrant_collection
             OR OLD.cutoff_at IS DISTINCT FROM NEW.cutoff_at
             OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
            RAISE EXCEPTION 'immutable corpus release content cannot be changed';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
