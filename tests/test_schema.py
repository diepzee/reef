"""Schema-level checks: the fixture's shape, and deny-by-default."""

import asyncpg
import pytest

from rif.access import Principal, arm
from rif.models import MemberRole, Membership, Page, Space, SpaceKind


async def test_household_fixture_creates_four_memberships(tx, household):
    assert len(await Membership.objects()) == 4


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


async def test_membership_role_defaults_to_member(tx, household):
    """A membership created without a role must be a full member.

    Piccolo stores the enum's *value*, so the column holds ``'member'``
    rather than ``'MEMBER'`` -- the same casing the RLS write predicate
    compares against.
    """
    row = (
        await Membership.objects()
        .where(
            Membership.person_id == household["wouter"].id,
            Membership.space_id == household["shared"].id,
        )
        .first()
    )
    assert row.role == MemberRole.MEMBER.value


async def test_one_personal_space_per_person_is_a_db_invariant(household):
    """A second personal space for the same owner must be refused.

    The invariant is a partial unique index, not a column constraint: it
    holds only where ``kind = 'personal'``. If it ever lapsed,
    ``resolve_space(principal, "personal")`` would find two spaces, raise
    ``AccessDenied``, and lock the person out of every tool call.
    """
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await Space(
            slug="second-personal",
            kind=SpaceKind.PERSONAL.value,
            owner_person_id=household["wouter"].id,
        ).save()


async def test_a_person_may_own_many_shared_spaces(household):
    """Owning shared spaces is unbounded: the old global UNIQUE is gone."""
    for slug in ("trip", "admin"):
        await Space(
            slug=slug,
            kind=SpaceKind.SHARED.value,
            owner_person_id=household["wouter"].id,
        ).save()
    owned = await Space.objects().where(
        Space.owner_person_id == household["wouter"].id,
        Space.kind == SpaceKind.SHARED.value,
    )
    assert {space.slug for space in owned} == {"household", "trip", "admin"}
