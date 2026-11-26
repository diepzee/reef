"""A caller's own objects must not change what an access helper reads.

The helpers that decide who may read what run with their owner's rights.
Postgres looks names up along a search path, and searches temporary tables
first unless the path says otherwise. These tests make a caller's object
stand in the way and check that the helpers still read the real one.
"""

import asyncpg
from conftest import caller_for, save_as

from reef.config import get_settings
from reef.models import Space
from reef.rls import HELPER_SEARCH_PATH


async def test_a_temporary_table_cannot_grant_access(household, probe) -> None:
    """The app role makes a temporary ``memberships`` table that says the
    partner is a member of Wouter's personal space. The partner still cannot
    read Wouter's private engram."""
    private = await save_as(
        caller_for(household["wouter"]), "personal", "private.md", "private"
    )
    shared = await save_as(
        caller_for(household["wouter"]), "household", "shared.md", "shared"
    )
    transaction = probe.transaction()
    await transaction.start()
    try:
        allowed = await probe.fetchval(
            "SELECT has_database_privilege(current_user, current_database(), 'TEMP')"
        )
        assert allowed, "the stand-in for the app role can make temporary tables"
        await probe.execute(
            "CREATE TEMP TABLE memberships (LIKE public.memberships) ON COMMIT DROP"
        )
        await probe.execute(
            "INSERT INTO pg_temp.memberships (id, person_id, space_id, role, alias) "
            "VALUES (1, $1, $2, 'member', 'forged')",
            household["partner"].id,
            household["w_personal"].id,
        )
        await probe.execute("GRANT SELECT ON pg_temp.memberships TO PUBLIC")
        await probe.execute(
            "SELECT set_config('app.person_id', $1, true)",
            str(household["partner"].id),
        )
        # The session itself finds its own table first, and row security is
        # at work: the partner reads the shared engram, not the private one.
        assert await probe.fetchval("SELECT count(*) FROM memberships") == 1
        seen = await probe.fetch(
            "SELECT id FROM engrams WHERE id = ANY($1::uuid[])",
            [private.id, shared.id],
        )
        assert [row["id"] for row in seen] == [shared.id]
    finally:
        await transaction.rollback()


async def test_a_function_in_public_cannot_stand_in_for_a_built_in(household) -> None:
    """A function ``public.current_setting`` that always names Wouter is made
    in a transaction that is rolled back. In that session, bound to the
    partner, the helper that lists a person's spaces must still ask the real
    ``current_setting``, and so not list Wouter's personal space.

    The session is new on purpose: Postgres keeps a function's parsed body
    within a session until something makes it stale, and a new function
    elsewhere does not. A helper that ran before would not look names up
    again.
    """
    # The suite's role owns the schema, so it may create in ``public``; the
    # app role may not.
    owner = await asyncpg.connect(get_settings().database.test_url)
    try:
        transaction = owner.transaction()
        await transaction.start()
        await owner.execute(
            "CREATE FUNCTION public.current_setting(text, boolean) RETURNS text "
            f"LANGUAGE sql AS $$ SELECT '{household['wouter'].id}'::text $$"
        )
        await owner.execute(
            "SELECT pg_catalog.set_config('app.person_id', $1, true)",
            str(household["partner"].id),
        )
        seen = await owner.fetch("SELECT reef_space_ids() AS id")
        assert household["w_personal"].id not in {row["id"] for row in seen}
        assert {row["id"] for row in seen} == {
            household["p_personal"].id,
            household["shared"].id,
        }
        await transaction.rollback()
    finally:
        await owner.close()


async def test_every_helper_has_the_safe_search_path() -> None:
    """Every function in ``public`` that runs with its owner's rights names
    the system catalog first and temporary tables last. A helper added later
    without that setting fails here."""
    rows = await Space.raw(
        "SELECT p.oid::regprocedure::text AS name, p.proconfig AS config "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.prosecdef ORDER BY 1"
    )
    assert "reef_space_ids()" in {row["name"] for row in rows}
    wrong = {
        row["name"]: row["config"]
        for row in rows
        if row["config"] != [f"search_path={HELPER_SEARCH_PATH}"]
    }
    assert wrong == {}, (
        "these run with their owner's rights without the safe search path. One "
        "that reef.rls does not declare is a leftover in the shared test "
        "database from another branch: drop it."
    )
