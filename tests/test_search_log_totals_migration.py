"""Rehearse the frozen search totals migration through the real chain."""

import importlib
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from test_migration_chain import _admin_dsn, build_by_migrating, drop_database

DATABASE = "reef_search_log_totals_drill"
MIGRATION_ID = "2026-11-26T09:00:00:000000"
MODULE = "reef.piccolo_migrations.reef_2026_11_26t09_00_00_000000"
FIRST = "00000000-0000-0000-0000-0000000000a1"
SECOND = "00000000-0000-0000-0000-0000000000a2"


@pytest_asyncio.fixture(scope="module")
async def migrated() -> AsyncIterator[str]:
    """Build the chain and seed two people's searches.

    :returns: the database DSN as the owning app role
    """
    dsn = await build_by_migrating(DATABASE, upto=MIGRATION_ID)
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    try:
        await admin.execute(
            "INSERT INTO persons (id, email, display_name) VALUES "
            "($1, 'first@example.test', 'First'), "
            "($2, 'second@example.test', 'Second')",
            FIRST,
            SECOND,
        )
        await admin.execute(
            "INSERT INTO search_log (id, person_id, query, engram_ids, searched_at) "
            "SELECT gen_random_uuid(), id, 'private query', ARRAY[]::uuid[], "
            "date_trunc('day', now() AT TIME ZONE 'UTC') - interval '2 days' "
            "FROM persons WHERE id IN ($1, $2)",
            FIRST,
            SECOND,
        )
    finally:
        await admin.close()
    yield dsn
    await drop_database(DATABASE)


async def test_chain_creates_totals_with_the_authz_owner(migrated):
    """The app can count both people's searches without reading them."""
    app = await asyncpg.connect(migrated)
    try:
        assert tuple(await app.fetchrow("SELECT * FROM reef_search_log_totals(7)")) == (
            2,
            0,
        )
        assert await app.fetch("SELECT * FROM search_log") == []
        assert await app.fetchval(
            "SELECT pg_get_userbyid(proowner) FROM pg_proc "
            "WHERE proname = 'reef_search_log_totals'"
        ) in ("reef_authz", "rif_authz")
    finally:
        await app.close()


async def test_migrated_totals_accept_89_days_and_refuse_90(migrated):
    """The shipped SQL must leave the partially retained oldest day out."""
    app = await asyncpg.connect(migrated)
    try:
        assert tuple(
            await app.fetchrow("SELECT * FROM reef_search_log_totals(89)")
        ) == (2, 0)
        with pytest.raises(asyncpg.InvalidParameterValueError):
            await app.fetchrow("SELECT * FROM reef_search_log_totals(90)")
    finally:
        await app.close()


async def test_migration_recreates_totals_and_preserves_search_rows(migrated):
    """Recreate the function twice and check that stored searches are unchanged."""
    migration = importlib.import_module(MODULE)
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    app = await asyncpg.connect(migrated)
    try:
        before = await admin.fetch("SELECT * FROM search_log ORDER BY id")
        await admin.execute("DROP FUNCTION reef_search_log_totals(integer)")
        for _ in range(2):
            async with app.transaction():
                for statement in migration.STATEMENTS:
                    await app.execute(statement)
        assert tuple(await app.fetchrow("SELECT * FROM reef_search_log_totals(7)")) == (
            2,
            0,
        )
        assert await admin.fetch("SELECT * FROM search_log ORDER BY id") == before
    finally:
        await app.close()
        await admin.close()


@pytest.mark.parametrize("seconds, missed", [(-1, 1), (0, 0), (300, 0)])
async def test_migrated_totals_exclude_reads_from_today(migrated, seconds, missed):
    """Check the frozen function's midnight cutoff as a non-owner caller."""
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    transaction = admin.transaction()
    await transaction.start()
    try:
        space, engram = uuid4(), uuid4()
        await admin.execute(
            "INSERT INTO spaces (id, slug, kind, owner_person_id) "
            "VALUES ($1, 'edge', 'shared', $2)",
            space,
            FIRST,
        )
        await admin.execute(
            "INSERT INTO engrams (id, space_id, path, title, body) "
            "VALUES ($1, $2, 'edge.md', 'Edge', 'Private text')",
            engram,
            space,
        )
        await admin.execute(
            "INSERT INTO search_log (id, person_id, query, engram_ids, "
            "session_id, searched_at) VALUES (gen_random_uuid(), $1, "
            "'private edge query', ARRAY[]::uuid[], 'edge-session', "
            "date_trunc('day', now() AT TIME ZONE 'UTC') - interval '10 minutes')",
            FIRST,
        )
        await admin.execute(
            "INSERT INTO engram_access (id, person_id, engram_id, via, "
            "session_id, accessed_at) VALUES (gen_random_uuid(), $1, $2, "
            "'read', 'edge-session', date_trunc('day', now() AT TIME ZONE 'UTC') "
            "+ $3::interval)",
            FIRST,
            engram,
            timedelta(seconds=seconds),
        )
        await admin.execute("SET LOCAL ROLE reef_probe")
        assert tuple(
            await admin.fetchrow("SELECT * FROM reef_search_log_totals(7)")
        ) == (3, missed)
    finally:
        await transaction.rollback()
        await admin.close()
