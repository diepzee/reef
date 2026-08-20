"""Schema-level checks: the fixture's shape, and deny-by-default."""

import asyncpg
import pytest

from reef.access import Principal, arm
from reef.models import CoveKind, MemberRole, Page


async def test_household_fixture_creates_four_memberships(household, seed):
    # Read past the policies: this asserts the fixture's shape, not what any
    # one principal is allowed to see.
    assert await seed.fetchval("SELECT count(*) FROM memberships") == 4


async def test_content_is_invisible_without_a_principal(tx, household):
    principal = Principal(
        person_id=household["wouter"].id, email=household["wouter"].email
    )
    await arm(principal)
    await Page(
        cove_id=household["w_personal"].id,
        path="a.md",
        title="a",
        tags=[],
        body="secret",
    ).save()
    await Page.raw("SELECT set_config('app.person_id', '', true)")
    assert await Page.objects() == []


async def test_membership_role_defaults_to_member(household, seed):
    """A membership created without a role must be a full member.

    Piccolo stores the enum's *value*, so the column holds ``'member'``
    rather than ``'MEMBER'`` -- the same casing the RLS write predicate
    compares against.
    """
    role = await seed.fetchval(
        "SELECT role FROM memberships WHERE person_id = $1 AND cove_id = $2",
        household["wouter"].id,
        household["shared"].id,
    )
    assert role == MemberRole.MEMBER.value


async def seed_owned_shared(person):
    """Return the slugs of shared coves ``person`` owns, read past the policies.

    :param person: the owner
    :returns: a set of slugs
    """
    import asyncpg as _asyncpg
    from conftest import seed_dsn

    connection = await _asyncpg.connect(seed_dsn())
    try:
        rows = await connection.fetch(
            "SELECT slug FROM coves WHERE owner_person_id = $1 AND kind = 'shared'",
            person.id,
        )
    finally:
        await connection.close()
    return {row["slug"] for row in rows}


async def test_one_personal_cove_per_person_is_a_db_invariant(household, seed):
    """A second personal cove for the same owner must be refused.

    The invariant is a partial unique index, not a column constraint: it
    holds only where ``kind = 'personal'``. If it ever lapsed,
    ``resolve_cove(principal, "personal")`` would find two coves, raise
    ``AccessDenied``, and lock the person out of every tool call.
    """
    # Written through the seed connection: this asserts a *database*
    # constraint, so it must reach the constraint rather than being turned
    # away earlier by a policy.
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await seed.execute(
            "INSERT INTO coves (id, slug, kind, owner_person_id, version) "
            "VALUES (gen_random_uuid(), $1, $2, $3, 0)",
            "second-personal",
            CoveKind.PERSONAL.value,
            household["wouter"].id,
        )


async def test_a_person_may_own_many_shared_coves(household, graph):
    """Owning shared coves is unbounded: the old global UNIQUE is gone."""
    for slug in ("trip", "admin"):
        await graph.shared_cove(slug, household["wouter"])
    owned = await seed_owned_shared(household["wouter"])
    assert owned == {"household", "trip", "admin"}


def test_enable_statements_names_no_column_the_old_migrations_predate():
    """The trap this project has now fallen into three times.

    Three historical migrations call ``enable_statements`` to re-apply the
    policies of their day. Anything added to that set is therefore executed
    against a database as it looked in August, so a statement naming a column
    added later fails every build from scratch -- a fresh deploy, and the
    restore drill in ``docs/restore.md`` -- while production, already past
    those migrations, never notices.

    ``cove_appearances`` was caught in review and split out.
    ``session_epoch`` was not, and shipped broken. This asserts the rule
    directly rather than trusting the next person to remember it.
    """
    from reef.rls import enable_statements

    combined = " ".join(enable_statements())
    for column in (
        "session_epoch",
        "joined_open_door",
        "cove_appearances",
        "last_seen_release",
    ):
        assert column not in combined, (
            f"{column} postdates the migrations that call enable_statements; "
            f"give it its own statements group, as appearance_statements has"
        )


async def test_last_seen_release_defaults_to_null(household, seed):
    """The column starts NULL and Postgres stores whatever string it's given.

    This reads and writes through the ``seed`` connection, which bypasses
    the identity policies (see ``tests/conftest.py``), so it proves nothing
    about ownership or RLS -- only that the column exists, defaults to NULL,
    and round-trips a value. NULL rather than ``''`` matters because "never
    seen anything" and "seen version empty-string" are different states, and
    only one of them should light the dot for somebody who predates the
    feature. The RLS proof -- that a person can read and write only their
    own marker -- lives in ``tests/test_release_notes_api.py``.
    """
    person_id = household["wouter"].id
    assert (
        await seed.fetchval(
            "SELECT last_seen_release FROM persons WHERE id = $1", person_id
        )
        is None
    )
    await seed.execute(
        "UPDATE persons SET last_seen_release = '0.4.0' WHERE id = $1", person_id
    )
    assert (
        await seed.fetchval(
            "SELECT last_seen_release FROM persons WHERE id = $1", person_id
        )
        == "0.4.0"
    )
