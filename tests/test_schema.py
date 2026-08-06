"""Schema-level checks: the fixture's shape, and deny-by-default."""

from rif.access import Principal, arm
from rif.models import Membership, Page


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
