"""Cover the corpus-release immutability trigger, which only exists in PostgreSQL.

The rest of the suite builds its schema with ``Base.metadata.create_all`` against SQLite,
which creates no triggers at all. That is precisely how a contradiction between
``reject_corpus_release_content_change`` (migration 0003) and
``SQLCorpusReleaseRepository.mark_index_validated`` survived unnoticed: the application
invariant was covered by
``tests/unit/test_corpus_releases.py::test_index_validation_selects_one_candidate_profile_collection``,
the database rule meant to enforce the same thing was not, and the two disagreed. No
release could reach ``index_status='VALIDATED'`` through ``qdrant-attest`` until migration
0020 reconciled them.

These tests assert the trigger permits exactly the transition the application performs and
nothing wider. They run only where a migrated PostgreSQL is reachable, and leave no rows
behind: every case runs inside a transaction that is rolled back.
"""

from __future__ import annotations

import os

import pytest

asyncpg = pytest.importorskip("asyncpg")

TEST_DATABASE_URL = os.environ.get(
    "MEDRAG_TEST_DATABASE_URL",
    "postgresql://medrag:medrag@localhost:5432/medrag",
)

RESERVED_COLLECTION = "corpus_cr_triggerfixture0000000000000000000000000"
PROFILE_COLLECTION = f"{RESERVED_COLLECTION}--vp-{'a' * 24}"


async def _connect():
    try:
        connection = await asyncpg.connect(TEST_DATABASE_URL, timeout=5)
    except (OSError, asyncpg.PostgresError) as error:
        pytest.skip(f"no PostgreSQL at {TEST_DATABASE_URL}: {error}")
    trigger = await connection.fetchval(
        """
        SELECT count(*) FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        WHERE c.relname = 'corpus_releases'
          AND t.tgname = 'trg_corpus_releases_content_immutable'
        """
    )
    if not trigger:
        await connection.close()
        pytest.skip("corpus_releases immutability trigger absent; run alembic upgrade head")
    return connection


@pytest.fixture
async def release():
    """Yield (connection, release_id) for a release row that never leaves the database."""

    connection = await _connect()
    transaction = connection.transaction()
    await transaction.start()
    release_id = "CR_triggerfixture0000000000000000"
    await connection.execute(
        """
        INSERT INTO corpus_releases (
            corpus_release_id, contract_version, manifest_sha256, manifest,
            state, qdrant_collection, cutoff_at, index_status, created_at
        ) VALUES ($1, '1.0.0', $2, '{}'::json, 'VALIDATED', $3, now(), 'NOT_BUILT', now())
        """,
        release_id,
        "b" * 64,
        RESERVED_COLLECTION,
    )
    try:
        yield connection, release_id
    finally:
        await transaction.rollback()
        await connection.close()


async def _set_collection(connection, release_id: str, collection: str) -> None:
    await connection.execute(
        "UPDATE corpus_releases SET qdrant_collection = $1 WHERE corpus_release_id = $2",
        collection,
        release_id,
    )


async def test_reserved_name_may_take_its_candidate_profile_once(release) -> None:
    connection, release_id = release

    await _set_collection(connection, release_id, PROFILE_COLLECTION)

    assert (
        await connection.fetchval(
            "SELECT qdrant_collection FROM corpus_releases WHERE corpus_release_id = $1",
            release_id,
        )
        == PROFILE_COLLECTION
    )


async def test_unrelated_collection_name_is_refused(release) -> None:
    connection, release_id = release

    with pytest.raises(asyncpg.exceptions.RaiseError, match="candidate vector profile"):
        async with connection.transaction():
            await _set_collection(connection, release_id, "corpus_cr_somewhere_else")


async def test_profile_cannot_change_once_index_is_validated(release) -> None:
    connection, release_id = release
    await _set_collection(connection, release_id, PROFILE_COLLECTION)
    await connection.execute(
        """
        UPDATE corpus_releases
        SET index_status = 'VALIDATED', index_point_count = 1,
            index_attestation_sha256 = $2, index_validated_at = now()
        WHERE corpus_release_id = $1
        """,
        release_id,
        "c" * 64,
    )

    with pytest.raises(asyncpg.exceptions.RaiseError, match="candidate vector profile"):
        async with connection.transaction():
            await _set_collection(
                connection, release_id, f"{RESERVED_COLLECTION}--vp-{'d' * 24}"
            )


async def test_release_content_is_still_immutable(release) -> None:
    """The narrowed trigger must not have loosened anything else."""

    connection, release_id = release

    with pytest.raises(asyncpg.exceptions.RaiseError, match="immutable corpus release"):
        async with connection.transaction():
            await connection.execute(
                "UPDATE corpus_releases SET manifest_sha256 = $1 WHERE corpus_release_id = $2",
                "e" * 64,
                release_id,
            )
