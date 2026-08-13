"""Schema-level checks: the fixture's shape, and deny-by-default."""

import asyncpg
import pytest

from rif.access import Principal, arm
from rif.models import MemberRole, Page, SpaceKind


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
        space_id=household["w_personal"].id,
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
        "SELECT role FROM memberships WHERE person_id = $1 AND space_id = $2",
        household["wouter"].id,
        household["shared"].id,
    )
    assert role == MemberRole.MEMBER.value


async def seed_owned_shared(person):
    """Return the slugs of shared spaces ``person`` owns, read past the policies.

    :param person: the owner
    :returns: a set of slugs
    """
    import asyncpg as _asyncpg
    from conftest import seed_dsn

    connection = await _asyncpg.connect(seed_dsn())
    try:
        rows = await connection.fetch(
            "SELECT slug FROM spaces WHERE owner_person_id = $1 AND kind = 'shared'",
            person.id,
        )
    finally:
        await connection.close()
    return {row["slug"] for row in rows}


async def test_one_personal_space_per_person_is_a_db_invariant(household, seed):
    """A second personal space for the same owner must be refused.

    The invariant is a partial unique index, not a column constraint: it
    holds only where ``kind = 'personal'``. If it ever lapsed,
    ``resolve_space(principal, "personal")`` would find two spaces, raise
    ``AccessDenied``, and lock the person out of every tool call.
    """
    # Written through the seed connection: this asserts a *database*
    # constraint, so it must reach the constraint rather than being turned
    # away earlier by a policy.
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await seed.execute(
            "INSERT INTO spaces (id, slug, kind, owner_person_id, version) "
            "VALUES (gen_random_uuid(), $1, $2, $3, 0)",
            "second-personal",
            SpaceKind.PERSONAL.value,
            household["wouter"].id,
        )


async def test_a_person_may_own_many_shared_spaces(household, graph):
    """Owning shared spaces is unbounded: the old global UNIQUE is gone."""
    for slug in ("trip", "admin"):
        await graph.shared_space(slug, household["wouter"])
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

    ``space_appearances`` was caught in review and split out.
    ``session_epoch`` was not, and shipped broken. This asserts the rule
    directly rather than trusting the next person to remember it.
    """
    from rif.rls import enable_statements

    combined = " ".join(enable_statements())
    for column in ("session_epoch", "joined_open_door", "space_appearances"):
        assert column not in combined, (
            f"{column} postdates the migrations that call enable_statements; "
            f"give it its own statements group, as appearance_statements has"
        )
