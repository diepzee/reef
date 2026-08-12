"""The probe role, and why the suite cannot do without it.

The tests here assert a property of the *test harness* rather than of reef.
Everything else in the suite connects as ``rif``, which owns the tables, and
an owner's privileges are implicit: a column grant that stops production's
``rif_app`` dead does not constrain the owner at all.

So a test written the obvious way -- "a member cannot rewrite a cove's slug"
-- would pass against the owner while the policy it claims to prove was
missing. The first test below demonstrates that divergence on purpose, so the
reason this fixture exists cannot be quietly forgotten.

This repo has already paid for this exact mistake once, in the other
direction: production ran as the bootstrap superuser for five days while the
tests, run against a constrained role, proved a shape production did not
have.
"""

import asyncpg
import pytest
from conftest import PROBE_ROLE


async def test_the_probe_is_not_a_superuser_and_does_not_bypass_rls(probe):
    """If it did either, every negative test in the suite would be vacuous."""
    row = await probe.fetchrow(
        "SELECT current_user AS role, "
        "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS super, "
        "(SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS bypass"
    )
    assert row["role"] == PROBE_ROLE
    assert row["super"] is False
    assert row["bypass"] is False


async def test_the_probe_does_not_own_the_tables(probe):
    """Ownership is the thing that makes column grants toothless."""
    owner = await probe.fetchval(
        "SELECT tableowner FROM pg_tables WHERE tablename = 'spaces'"
    )
    assert owner != PROBE_ROLE


async def test_a_column_grant_binds_the_probe_but_not_the_owner(probe):
    """The divergence this fixture exists for, demonstrated rather than asserted.

    A column-level grant is applied, then exercised from both connections.
    The owner sails through; the probe is refused. Any privilege test written
    against the owner would therefore be reporting the wrong answer.
    """
    from rif.db import DB

    await DB._run_in_new_connection("CREATE TABLE probe_demo (keep int, guard int)")
    try:
        await DB._run_in_new_connection("GRANT SELECT ON probe_demo TO " + PROBE_ROLE)
        await DB._run_in_new_connection(
            "GRANT UPDATE (keep) ON probe_demo TO " + PROBE_ROLE
        )
        await DB._run_in_new_connection("INSERT INTO probe_demo VALUES (1, 1)")

        # The owner is not constrained by the grant at all.
        await DB._run_in_new_connection("UPDATE probe_demo SET guard = 2")

        # The probe may write the granted column...
        await probe.execute("UPDATE probe_demo SET keep = 3")

        # ...and is refused the one it was not granted.
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await probe.execute("UPDATE probe_demo SET guard = 4")
    finally:
        await DB._run_in_new_connection("DROP TABLE IF EXISTS probe_demo")


async def test_row_level_security_still_applies_to_the_probe(probe, graph):
    """Policies bind both roles, so only privilege tests need this fixture.

    ``FORCE ROW LEVEL SECURITY`` extends policies to the owner, which is why
    the rest of the suite can assert visibility from the ordinary connection.
    Confirmed here rather than assumed.
    """
    from rif.access import Principal, arm
    from rif.db import DB
    from rif.pages import save_page

    person = await graph.person("probe-rls@example.test", "Probe")
    await graph.personal_space(person, slug="probe-rls")

    # The write itself needs an armed principal -- the insert policy is
    # already enforced against the owner, which is FORCE RLS doing its job.
    async with DB.transaction():
        principal = Principal(person_id=person.id, email=person.email)
        await arm(principal)
        await save_page(
            principal, "personal", "p.md", "body", message="probe", title="T"
        )

    # Unarmed, on a connection that neither owns the table nor bypasses RLS.
    assert await probe.fetchval("SELECT count(*) FROM pages") == 0
