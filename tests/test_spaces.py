import pytest

from rif.access import AccessDenied, Principal, resolve_space
from rif.models import MemberRole, Membership, Person, Space, SpaceKind
from rif.pages import get_page, save_page
from rif.spaces import (
    SpaceError,
    _membership,
    create_space,
    ensure_personal_space,
    invite,
    remove_member,
)


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def membership_for(person, space) -> Membership | None:
    """Fetch a membership by its real composite key.

    :param person: the member
    :param space: the space
    :returns: the membership row, or None
    """
    return (
        await Membership.objects()
        .where(Membership.person_id == person.id, Membership.space_id == space.id)
        .first()
    )


async def test_create_space_makes_owner_the_first_member(tx, household):
    me = principal_for(household["wouter"])
    space = await create_space(me, "trip")
    assert space.kind == SpaceKind.SHARED.value
    assert space.owner_person_id == household["wouter"].id
    assert (await resolve_space(me, "trip")).id == space.id


async def test_create_space_rejects_bad_and_taken_names(tx, household):
    me = principal_for(household["wouter"])
    for bad in ("personal", "Has Caps", "-leading", "a", "household"):
        with pytest.raises(SpaceError):
            await create_space(me, bad)


async def test_create_space_refuses_every_spelling_of_personal(tx, household):
    """No shared space may render as, or squat inside, the personal namespace.

    ``personal\\n`` is the adversarial case: ``$`` matches before a trailing
    newline and the reserved set is an exact-match check, so the pair used to
    let a shared space through whose name renders identically to the private
    space in ``list_spaces``. ``personal-<hex>`` is the squat on the
    deterministic onboarding slug.
    """
    me = principal_for(household["wouter"])
    for bad in (
        "personal",
        "personal\n",
        "personal\r",
        "personal-abc",
        f"personal-{household['partner'].id.hex}",
        "trip\n",
        "trip\nx",
    ):
        with pytest.raises(SpaceError):
            await create_space(me, bad)
    # the fixture's personal slugs are "wouter"/"partner", so any row whose
    # slug begins "personal" would have to have come from the loop above
    assert await Space.objects().where(Space.slug.like("personal%")).first() is None


async def test_onboarding_survives_an_attempted_slug_squat(tx, household, graph):
    """A squat on the victim's onboarding slug is impossible, so first sign-in works.

    The personal slug is derived from the person id, and ``spaces.slug`` is
    globally unique, so a squat would make every request by that person fail
    inside ``principal_from_claims`` — a permanent lockout. The prefix
    reservation is what prevents it.
    """
    attacker = principal_for(household["wouter"])
    victim = await graph.person("victim@example.test", "Victim")
    with pytest.raises(SpaceError):
        await create_space(attacker, f"personal-{victim.id.hex}")
    await ensure_personal_space(victim.id, victim.email)
    assert (
        await get_page(principal_for(victim), "personal", "meta/persona.md") is not None
    )


async def test_invite_new_email_creates_person_and_membership(tx, household):
    from rif.invitations import invites_left

    me = principal_for(household["wouter"])
    before = await invites_left(me)
    result = await invite(me, "household", "Anna@Example.test", display_name="Anna")
    assert result["already_member"] is False

    # The inviter cannot read the row they just created -- that is the
    # persons policy working -- so this asserts what is actually observable:
    # reef now knows the address, the membership carries it, and a budget
    # entry was spent, which is only true if invited_by names the inviter.
    rows = await Person.raw(
        "SELECT rif_person_id_by_email({}) AS id", "anna@example.test"
    )
    anna_id = rows[0]["id"]
    assert anna_id is not None

    row = await _membership(anna_id, household["shared"].id)
    assert row is not None and row.role == MemberRole.MEMBER.value
    assert await invites_left(me) == before - 1


async def test_invite_discloses_scope_and_is_idempotent(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "household", "a.md", "x", message="x")
    first = await invite(me, "household", "anna@example.test")
    again = await invite(me, "household", "anna@example.test")
    assert "permanently" in first["disclosure"] and "1 page" in first["disclosure"]
    assert again["already_member"] is True


async def test_only_the_owner_invites_or_removes(tx, household):
    partner = principal_for(household["partner"])
    with pytest.raises(SpaceError):
        await invite(partner, "household", "anna@example.test")
    with pytest.raises(SpaceError):
        await remove_member(partner, "household", "wouter@example.test")


async def test_the_personal_space_cannot_be_shared(tx, household):
    me = principal_for(household["wouter"])
    with pytest.raises(SpaceError):
        await invite(me, "personal", "anna@example.test")


async def test_remove_member_revokes_future_reads(tx, household):
    me = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await remove_member(me, "household", "partner@example.test")
    with pytest.raises(AccessDenied):
        await resolve_space(theirs, "household")


async def test_owner_cannot_remove_themselves(tx, household):
    me = principal_for(household["wouter"])
    with pytest.raises(SpaceError):
        await remove_member(me, "household", "wouter@example.test")


async def test_removing_unbound_invitee_erases_the_orphan_person(tx, household):
    me = principal_for(household["wouter"])
    await invite(me, "household", "typo@example.test")
    result = await remove_member(me, "household", "typo@example.test")
    assert result["person_erased"] is True
    assert (
        await Person.objects().where(Person.email == "typo@example.test").first()
        is None
    )


async def test_ensure_personal_space_seeds_only_the_persona_once(tx, graph):
    """The protocol ships with the product; only the persona is seeded."""
    anna = await graph.person("anna@example.test", "Anna")
    await ensure_personal_space(anna.id, anna.email)
    await ensure_personal_space(anna.id, anna.email)  # idempotent
    me = principal_for(anna)
    persona = await get_page(me, "personal", "meta/persona.md")
    assert persona is not None
    assert persona.version == 1  # seeded once, not twice
    assert await get_page(me, "personal", "meta/protocol.md") is None
    spaces = await Space.objects().where(Space.owner_person_id == anna.id)
    assert len(spaces) == 1
