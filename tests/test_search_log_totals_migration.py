"""Rehearse the frozen search totals migration through the real chain."""

import importlib
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest_asyncio
from test_migration_chain import _admin_dsn, build_by_migrating, drop_database

from reef.rls import search_log_statements

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


async def test_migration_adds_only_the_function_and_preserves_existing_rows(migrated):
    """Apply to stored searches twice: neither tables nor rows are rewritten."""
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


def test_frozen_sql_matches_the_declared_function():
    """The migration freezes the same body, ownership and grants the suite uses."""
    migration = importlib.import_module(MODULE)
    statements = search_log_statements()
    start = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("CREATE OR REPLACE FUNCTION reef_search_log_totals(")
    )
    assert migration.STATEMENTS == statements[start:]
    assert "\nfrom reef.rls" not in Path(migration.__file__).read_text()
