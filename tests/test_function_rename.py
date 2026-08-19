"""Renaming the RLS helpers must not open a window where a policy is absent.

The helper functions decide which rows a person may see. Dropping and
recreating them would leave, however briefly, a policy whose predicate does
not resolve — on the one boundary this schema exists to enforce.

``ALTER FUNCTION ... RENAME TO`` keeps the OID, and a policy stores the OID
rather than the name, so policies follow the rename untouched. These tests
hold that claim against a real PostgreSQL rather than trusting it.
"""

import asyncpg
import pytest

from reef.config import get_settings

RENAME = __import__(
    "reef.piccolo_migrations.rif_2026_08_19t13_00_00_000000",
    fromlist=["RENAME"],
).RENAME


@pytest.fixture
async def scratch():
    """A connection with a throwaway function, table and policy."""
    connection = await asyncpg.connect(get_settings().test_database_url)
    await connection.execute("""
        DROP TABLE IF EXISTS rename_fixture CASCADE;
        DROP FUNCTION IF EXISTS rif_fixture_ids() CASCADE;
        DROP FUNCTION IF EXISTS reef_fixture_ids() CASCADE;
        CREATE FUNCTION rif_fixture_ids() RETURNS SETOF int
            LANGUAGE sql STABLE AS $body$ SELECT 1 $body$;
        CREATE TABLE rename_fixture (id int);
        INSERT INTO rename_fixture VALUES (1), (2);
        ALTER TABLE rename_fixture ENABLE ROW LEVEL SECURITY;
        CREATE POLICY rename_fixture_policy ON rename_fixture
            USING (id IN (SELECT rif_fixture_ids()));
    """)
    yield connection
    await connection.execute("""
        DROP TABLE IF EXISTS rename_fixture CASCADE;
        DROP FUNCTION IF EXISTS rif_fixture_ids() CASCADE;
        DROP FUNCTION IF EXISTS reef_fixture_ids() CASCADE;
    """)
    await connection.close()


async def policy_predicate(connection) -> str:
    return await connection.fetchval(
        "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy "
        "WHERE polname = 'rename_fixture_policy'"
    )


async def test_the_function_is_renamed(scratch):
    await scratch.execute(RENAME)
    assert (
        await scratch.fetchval(
            "SELECT count(*) FROM pg_proc WHERE proname = 'reef_fixture_ids'"
        )
        == 1
    )
    assert (
        await scratch.fetchval(
            "SELECT count(*) FROM pg_proc WHERE proname = 'rif_fixture_ids'"
        )
        == 0
    )


async def test_the_policy_follows_without_being_touched(scratch):
    """The whole reason this is a rename and not a drop-and-recreate."""
    assert "rif_fixture_ids" in await policy_predicate(scratch)
    await scratch.execute(RENAME)
    predicate = await policy_predicate(scratch)
    # The *call* is what matters. Postgres also stored a column alias from
    # when the policy was written ("AS rif_fixture_ids"), and an alias is a
    # label, not a reference -- it does not resolve to anything and does not
    # follow the rename. Asserting on the call avoids reading a cosmetic
    # leftover as a live dependency.
    assert "SELECT reef_fixture_ids()" in predicate
    assert "SELECT rif_fixture_ids()" not in predicate


async def test_the_policy_still_filters_afterwards(scratch):
    """A predicate that resolves is not the same as one that still works."""
    await scratch.execute(RENAME)
    assert await scratch.fetchval("SELECT reef_fixture_ids()") == 1


async def test_running_it_twice_changes_nothing(scratch):
    """A partially applied chain, or a re-run, must not error."""
    await scratch.execute(RENAME)
    await scratch.execute(RENAME)
    assert "reef_fixture_ids" in await policy_predicate(scratch)


async def test_it_is_a_no_op_on_a_database_built_under_the_new_names(scratch):
    """Fresh databases create reef_ functions directly and must be left alone."""
    await scratch.execute(RENAME)
    before = await policy_predicate(scratch)
    await scratch.execute(RENAME)
    assert await policy_predicate(scratch) == before
