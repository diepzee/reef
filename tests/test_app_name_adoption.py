"""Migration records move to the new app name before Piccolo reads them.

Piccolo records every applied migration against an app name and, on the next
boot, asks which migrations have run *for that app*. Rename the app in code
without moving the records and it asks about an app that has never run
anything — so it replays the entire chain against a populated database.

That cannot be fixed by a migration: by the time one runs, Piccolo has
already decided the chain is unapplied. It has to happen first, which is why
it lives in the runner.
"""

import importlib.util
from pathlib import Path

import asyncpg
import pytest

from reef.config import get_settings

ROOT = Path(__file__).resolve().parent.parent


def load_runner():
    """Import ``scripts/migrate.py``, which is not an installed module."""
    spec = importlib.util.spec_from_file_location(
        "migrate_runner", ROOT / "scripts" / "migrate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adopt_app_name = load_runner().adopt_app_name


@pytest.fixture
async def connection():
    """A connection with a temporary `migration` table shadowing the real one.

    A TEMP table takes precedence in the search path, so the statement under
    test resolves to this one and the real table is never touched.
    """
    conn = await asyncpg.connect(get_settings().test_database_url)
    await conn.execute(
        "CREATE TEMP TABLE migration (name TEXT, app_name TEXT, ran_on TIMESTAMPTZ)"
    )
    yield conn
    await conn.close()


async def test_records_move_to_the_new_name(connection):
    await connection.execute(
        "INSERT INTO migration VALUES ('2026-08-06T12:11:43:809197', 'rif', now()), "
        "('2026-08-07T11:00:00:000000', 'rif', now())"
    )
    assert await adopt_app_name(connection) == 2
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM migration WHERE app_name = 'reef'"
        )
        == 2
    )
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM migration WHERE app_name = 'rif'"
        )
        == 0
    )


async def test_running_twice_moves_nothing_the_second_time(connection):
    await connection.execute(
        "INSERT INTO migration VALUES ('2026-08-06T12:11:43:809197', 'rif', now())"
    )
    assert await adopt_app_name(connection) == 1
    assert await adopt_app_name(connection) == 0


async def test_records_already_under_the_new_name_are_left_alone(connection):
    await connection.execute(
        "INSERT INTO migration VALUES ('2026-08-06T12:11:43:809197', 'reef', now())"
    )
    assert await adopt_app_name(connection) == 0
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM migration WHERE app_name = 'reef'"
        )
        == 1
    )


async def test_another_app_is_not_touched(connection):
    """Only reef's own records move; anything else is somebody else's."""
    await connection.execute(
        "INSERT INTO migration VALUES ('x', 'rif', now()), ('y', 'other', now())"
    )
    assert await adopt_app_name(connection) == 1
    assert (
        await connection.fetchval("SELECT app_name FROM migration WHERE name = 'y'")
        == "other"
    )


async def test_a_database_without_the_table_is_not_an_error():
    """A fresh database has no migration table until Piccolo makes one."""
    conn = await asyncpg.connect(get_settings().test_database_url)
    try:
        # No TEMP table here, and the real one does not exist in the test
        # database -- which is exactly the first-boot case.
        assert await adopt_app_name(conn) == 0
    finally:
        await conn.close()
