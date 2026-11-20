"""The migration that lets Reef count calls across people without reading them.

Its SQL is fixed text. So, like the other migrations' tests, it is tested on a
database the migration chain builds up to and including it, not on the
suite's schema.
"""

import importlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import asyncpg
import pytest_asyncio
from test_migration_chain import _admin_dsn, build_by_migrating, drop_database

MIGRATION = importlib.import_module(
    "reef.piccolo_migrations.reef_2026_11_20t16_00_00_000000"
)

#: Built and dropped per run, like the drill's database.
DATABASE = "reef_call_log_totals_drill"

FIRST = "00000000-0000-0000-0000-0000000000a1"
SECOND = "00000000-0000-0000-0000-0000000000a2"

TOTALS = "SELECT * FROM reef_call_log_totals($1)"
SINCE = datetime(2026, 11, 1, tzinfo=UTC)


@pytest_asyncio.fixture(scope="module")
async def as_of_the_totals() -> AsyncIterator[str]:
    """Build the schema as it stood right after this migration, with calls.

    Two people; each made a call, and one of them a second one without a
    reason. Seeded as the superuser, which row security does not bind.

    :returns: the DSN of the database, as the owning role
    """
    dsn = await build_by_migrating(DATABASE, upto=MIGRATION.ID)
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
            "INSERT INTO call_log (id, person_id, method, reason, writer, outcome, "
            "called_at) VALUES "
            "(gen_random_uuid(), $1, 'recall', 'why', 'assistant', 'ok', "
            "'2026-11-10'), "
            "(gen_random_uuid(), $1, 'recall', '', 'assistant', 'ok', "
            "'2026-11-11'), "
            "(gen_random_uuid(), $2, 'recall', 'because', 'assistant', 'ok', "
            "'2026-11-12')",
            FIRST,
            SECOND,
        )
    finally:
        await admin.close()
    yield dsn
    await drop_database(DATABASE)


async def _migrate(dsn: str) -> None:
    """Run the migration's statements in one transaction, as it does.

    :param dsn: the database to run them on
    """
    connection = await asyncpg.connect(dsn)
    try:
        async with connection.transaction():
            for statement in MIGRATION.STATEMENTS:
                await connection.execute(statement)
    finally:
        await connection.close()


async def _totals(dsn: str) -> list[tuple]:
    """Read the totals as the app's role, with no person armed.

    :param dsn: the database to read
    :returns: each total as a tuple
    """
    connection = await asyncpg.connect(dsn)
    try:
        return [tuple(row) for row in await connection.fetch(TOTALS, SINCE)]
    finally:
        await connection.close()


async def test_the_chain_creates_the_totals_owned_by_the_authz_role(
    as_of_the_totals,
):
    """Counted across both people, by a function the app's role cannot rewrite."""
    assert await _totals(as_of_the_totals) == [("recall", "ok", 3, 1, 2)]
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    try:
        owner = await admin.fetchval(
            "SELECT pg_get_userbyid(proowner) FROM pg_proc "
            "WHERE proname = 'reef_call_log_totals'"
        )
    finally:
        await admin.close()
    assert owner in ("reef_authz", "rif_authz")


async def test_it_creates_the_totals_where_they_are_missing_and_can_run_again(
    as_of_the_totals,
):
    """Dropped, the totals come back; run on top of itself, nothing changes."""
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    try:
        await admin.execute("DROP FUNCTION IF EXISTS reef_call_log_totals(timestamptz)")
    finally:
        await admin.close()
    await _migrate(as_of_the_totals)
    await _migrate(as_of_the_totals)
    assert await _totals(as_of_the_totals) == [("recall", "ok", 3, 1, 2)]
