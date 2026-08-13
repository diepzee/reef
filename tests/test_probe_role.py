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


async def test_a_member_may_bump_version_but_not_rewrite_a_cove(probe, graph):
    """The column grant, asserted from a role it actually constrains.

    A page write bumps ``spaces.version``, so the row policy has to admit
    every member. Row security cannot say *which column*, so without the
    grant a member could rename a cove or hand themselves its ownership with
    one statement. This is the assertion the whole probe fixture exists for:
    against the owning role it would pass while the grant was absent.
    """
    person = await graph.person("grant@example.test", "Grant")
    space = await graph.shared_space("grant-cove", person)

    await probe.execute("SELECT set_config('app.person_id', $1, false)", str(person.id))
    try:
        await probe.execute(
            "UPDATE spaces SET version = version + 1 WHERE id = $1", space.id
        )

        for column, value in (("slug", "stolen"), ("owner_person_id", str(person.id))):
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await probe.execute(
                    f"UPDATE spaces SET {column} = $1 WHERE id = $2", value, space.id
                )
    finally:
        await probe.execute("SELECT set_config('app.person_id', '', false)")


async def test_only_an_owner_may_add_someone_to_a_cove(probe, graph):
    """Membership is administration, and the database says so too.

    A full member is not an administrator. An earlier draft of the insert
    policy admitted any member, which would have let one add an arbitrary
    allowlisted person to a cove -- handing them every page in it, past and
    future -- with the application's ownership check the only thing in the
    way. Asserted through the probe so it is a real refusal, not an owner's
    implicit privilege.
    """
    owner = await graph.person("cove-owner@example.test", "Owner")
    member = await graph.person("cove-member@example.test", "Member")
    outsider = await graph.person("cove-outsider@example.test", "Outsider")
    space = await graph.shared_space("admin-cove", owner, member)

    await probe.execute("SELECT set_config('app.person_id', $1, false)", str(member.id))
    try:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await probe.execute(
                "INSERT INTO memberships (person_id, space_id, role) "
                "VALUES ($1, $2, 'member')",
                outsider.id,
                space.id,
            )
    finally:
        await probe.execute("SELECT set_config('app.person_id', '', false)")

    # The owner may, which is what makes the refusal above about authority
    # rather than about the insert being broken.
    await probe.execute("SELECT set_config('app.person_id', $1, false)", str(owner.id))
    try:
        await probe.execute(
            "INSERT INTO memberships (person_id, space_id, role) "
            "VALUES ($1, $2, 'member')",
            outsider.id,
            space.id,
        )
    finally:
        await probe.execute("SELECT set_config('app.person_id', '', false)")


async def test_only_an_owner_may_delete_a_cove(probe, graph):
    """The delete policy, asserted from a role it actually constrains.

    ``spaces_owner_delete`` restricts DELETE to the owner, and until the
    application grew a way to delete a cove nothing exercised it. It needs a
    probe more than most policies do: a DELETE filtered by a row policy is not
    an error. Postgres removes zero rows and reports success, so a member's
    attempt looks exactly like a member's attempt that worked, and the same
    statement run as the table's owner would have destroyed the cove outright.
    """
    owner = await graph.person("del-owner@example.test", "Owner")
    member = await graph.person("del-member@example.test", "Member")
    space = await graph.shared_space("del-cove", owner, member)

    await probe.execute("SELECT set_config('app.person_id', $1, false)", str(member.id))
    try:
        # No exception: the policy filters the row out rather than refusing.
        await probe.execute("DELETE FROM spaces WHERE id = $1", space.id)
        assert (
            await probe.fetchval("SELECT count(*) FROM spaces WHERE id = $1", space.id)
            == 1
        )
    finally:
        await probe.execute("SELECT set_config('app.person_id', '', false)")

    # The owner may, which is what makes the survival above about authority
    # rather than about the delete being broken.
    await probe.execute("SELECT set_config('app.person_id', $1, false)", str(owner.id))
    try:
        await probe.execute("DELETE FROM spaces WHERE id = $1", space.id)
        assert (
            await probe.fetchval("SELECT count(*) FROM spaces WHERE id = $1", space.id)
            == 0
        )
    finally:
        await probe.execute("SELECT set_config('app.person_id', '', false)")


async def test_a_member_may_rename_a_cove_but_not_promote_themselves(probe, graph):
    """The second column grant, asserted from a role it constrains.

    ``memberships_self_update`` has to admit the whole row so a person can
    rename their own cove. Row security cannot say *which* column, so
    without the grant that same policy would let a viewer set their own
    ``role`` to ``member`` -- turning read-only access into write access with
    one statement -- or move their membership onto a cove they can see but
    do not belong to.
    """
    person = await graph.person("rename@example.test", "Rename")
    space = await graph.shared_space("rename-cove", person)
    other = await graph.shared_space("other-cove", person)

    await probe.execute("SELECT set_config('app.person_id', $1, false)", str(person.id))
    try:
        await probe.execute(
            "UPDATE memberships SET alias = $1 WHERE person_id = $2 AND space_id = $3",
            "renamed",
            person.id,
            space.id,
        )
        assert (
            await probe.fetchval(
                "SELECT alias FROM memberships WHERE person_id = $1 AND space_id = $2",
                person.id,
                space.id,
            )
            == "renamed"
        )

        for column, value in (("role", "member"), ("space_id", str(other.id))):
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await probe.execute(
                    f"UPDATE memberships SET {column} = $1 WHERE person_id = $2",
                    value,
                    person.id,
                )
    finally:
        await probe.execute("SELECT set_config('app.person_id', '', false)")


async def test_a_person_cannot_rename_somebody_elses_membership(probe, graph, seed):
    """The row half of the same policy: my names, nobody else's."""
    mine = await graph.person("mine@example.test", "Mine")
    theirs = await graph.person("theirs@example.test", "Theirs")
    space = await graph.shared_space("joint-cove", mine, theirs)

    await probe.execute("SELECT set_config('app.person_id', $1, false)", str(mine.id))
    try:
        await probe.execute(
            "UPDATE memberships SET alias = $1 WHERE person_id = $2 AND space_id = $3",
            "hijacked",
            theirs.id,
            space.id,
        )
    finally:
        await probe.execute("SELECT set_config('app.person_id', '', false)")

    # A row policy denies by filtering to zero rows, not by raising, so the
    # UPDATE above "succeeds" having changed nothing. Read back through the
    # seeding connection -- as this principal the row is invisible either
    # way, which would make an unchanged value and a hidden one identical.
    assert (
        await seed.fetchval(
            "SELECT alias FROM memberships WHERE person_id = $1 AND space_id = $2",
            theirs.id,
            space.id,
        )
        == "joint-cove"
    )
