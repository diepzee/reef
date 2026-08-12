"""The helper functions every RLS policy is built on.

These do not test a feature. They test the property that makes the whole
policy system possible: that ``rif_space_ids()`` reads ``memberships``
without being filtered by ``memberships``' own policy, so a predicate can
call it without recursing.

If ``rif_authz`` ever loses ``BYPASSRLS``, or a future migration recreates a
function under a different owner, the design silently reverts to the shape
that exhausts the server's stack on the first request. The ownership test
below is the tripwire for exactly that.
"""

import asyncpg
import pytest

from rif.access import Principal, arm
from rif.db import DB
from rif.models import Membership, Space
from rif.rls import AUTHZ_ROLE


async def _arm(person) -> None:
    """Bind ``person`` as the RLS principal for the current transaction.

    :param person: the person to arm
    """
    await arm(Principal(person_id=person.id, email=person.email))


async def test_the_helper_functions_are_owned_by_the_bypassing_role():
    """Ownership and BYPASSRLS are what stop the policies recursing."""
    rows = await DB._run_in_new_connection(
        "SELECT p.proname, pg_get_userbyid(p.proowner) AS owner, r.rolbypassrls "
        "FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner "
        "WHERE p.proname IN ('rif_space_ids', 'rif_member_space_ids')"
    )
    assert len(rows) == 2, "both helper functions should exist"
    for row in rows:
        assert row["owner"] == AUTHZ_ROLE
        assert row["rolbypassrls"] is True


async def test_the_functions_are_security_definer_with_a_fixed_search_path():
    """A definer function without a pinned search_path is a privilege hole."""
    rows = await DB._run_in_new_connection(
        "SELECT proname, prosecdef, proconfig FROM pg_proc "
        "WHERE proname IN ('rif_space_ids', 'rif_member_space_ids')"
    )
    for row in rows:
        assert row["prosecdef"] is True, f"{row['proname']} must be SECURITY DEFINER"
        assert "search_path=public, pg_catalog" in (row["proconfig"] or [])


async def test_the_functions_are_closed_to_public(tx, household):
    """PUBLIC must not hold EXECUTE; only the app role is granted it."""
    granted = await DB._run_in_new_connection(
        "SELECT has_function_privilege('public', 'rif_space_ids()', 'EXECUTE') AS pub"
    )
    assert granted[0]["pub"] is False


async def test_space_ids_returns_only_the_armed_principals_spaces(tx, household):
    """The core contract: one principal, their memberships, nobody else's."""
    await _arm(household["wouter"])
    rows = await DB._run_in_new_connection("SELECT rif_space_ids() AS id")
    # A new connection carries no transaction-local principal, so this is the
    # unarmed case even though the test armed its own transaction.
    assert rows == []

    async with DB.transaction():
        await _arm(household["wouter"])
        mine = {r["id"] for r in await Space.raw("SELECT rif_space_ids() AS id")}
        assert mine == {household["w_personal"].id, household["shared"].id}
        assert household["p_personal"].id not in mine


async def test_member_space_ids_excludes_viewer_memberships(tx, household, graph):
    """Write authority is narrower than read authority, and stays so."""
    # memberships has no UPDATE policy -- role changes belong to the
    # ownership-transfer function -- so a viewer has to be seeded.
    await graph.set_role(household["partner"], household["shared"], "viewer")
    await _arm(household["partner"])
    readable = {r["id"] for r in await Space.raw("SELECT rif_space_ids() AS id")}
    writable = {r["id"] for r in await Space.raw("SELECT rif_member_space_ids() AS id")}

    assert household["shared"].id in readable
    assert household["shared"].id not in writable
    assert household["p_personal"].id in writable


async def test_an_unarmed_transaction_reaches_no_spaces(tx, household):
    """Fail closed: no principal means no rows, not all rows."""
    assert await Space.raw("SELECT rif_space_ids() AS id") == []


async def test_a_cleared_principal_reaches_no_spaces(tx, household):
    """The clearing path sets '' rather than unsetting; NULLIF must fold it."""
    await _arm(household["wouter"])
    await Space.raw("SELECT set_config('app.person_id', '', true)")
    assert await Space.raw("SELECT rif_space_ids() AS id") == []


async def test_a_policy_calling_the_function_on_its_own_table_does_not_recurse(graph):
    """The property the design exists for, proven on ``memberships`` itself.

    This installs precisely the policy shape that killed the first two
    designs -- a predicate on ``memberships`` that resolves through
    ``memberships`` -- and asserts it answers instead of exhausting the
    stack. Postgres surfaces the failure as ``stack depth limit exceeded``
    (54001), verified against a live server, so that is what this guards.

    Deliberately does not take the ``tx`` fixture. ``ALTER TABLE`` needs an
    ACCESS EXCLUSIVE lock, which an open transaction holding rows in
    ``memberships`` blocks forever rather than failing -- so the rows are
    committed first and the assertion opens its own transaction afterwards.
    """
    wouter = await graph.person("recursion@example.test", "Wouter")
    partner = await graph.person("recursion2@example.test", "Partner")
    personal = await graph.personal_space(wouter, slug="recursion-personal")
    await graph.personal_space(partner, slug="recursion-partner")
    shared = await graph.shared_space("recursion-shared", wouter, partner)

    await DB._run_in_new_connection("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY")
    await DB._run_in_new_connection("ALTER TABLE memberships FORCE ROW LEVEL SECURITY")
    await DB._run_in_new_connection(
        "CREATE POLICY memberships_recursion_probe ON memberships FOR SELECT "
        "USING (space_id IN (SELECT rif_space_ids()))"
    )
    try:
        async with DB.transaction():
            await _arm(wouter)
            visible = {
                (row["person_id"], row["space_id"])
                for row in await Membership.select(
                    Membership.person_id, Membership.space_id
                )
            }
        # Wouter's own two memberships, plus partner's membership of the
        # shared space -- a row in a space Wouter reaches. Partner's personal
        # membership is not.
        assert visible == {
            (wouter.id, personal.id),
            (wouter.id, shared.id),
            (partner.id, shared.id),
        }
    except asyncpg.exceptions.StatementTooComplexError as exc:  # pragma: no cover
        pytest.fail(f"the predicate recursed: {exc}")
    finally:
        await DB._run_in_new_connection(
            "DROP POLICY IF EXISTS memberships_recursion_probe ON memberships"
        )
        await DB._run_in_new_connection(
            "ALTER TABLE memberships NO FORCE ROW LEVEL SECURITY"
        )
        await DB._run_in_new_connection(
            "ALTER TABLE memberships DISABLE ROW LEVEL SECURITY"
        )
