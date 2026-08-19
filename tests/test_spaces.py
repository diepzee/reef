import pytest

from reef.access import AccessDenied, Principal, resolve_space
from reef.models import (
    Attachment,
    AttachmentStatus,
    MemberRole,
    Membership,
    Page,
    Person,
    Space,
    SpaceKind,
)
from reef.pages import get_page, save_page
from reef.spaces import (
    SpaceError,
    _membership,
    create_space,
    delete_space,
    ensure_personal_space,
    invite,
    leave_space,
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
    from reef.invitations import invites_left

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


async def test_leaving_hands_the_cove_to_the_remaining_member(tx, household):
    """The invariant account deletion already keeps: departing destroys nothing."""
    me = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    result = await leave_space(me, "household")

    assert result["left"] is True
    assert result["handed_to"] == "Partner"
    # The cove outlives its creator's departure, owned by whoever is left.
    still_there = await resolve_space(theirs, "household")
    assert still_there.owner_person_id == household["partner"].id
    assert await membership_for(household["wouter"], household["shared"]) is None
    with pytest.raises(AccessDenied):
        await resolve_space(me, "household")


async def test_a_member_leaving_changes_no_ownership(tx, household):
    partner = principal_for(household["partner"])
    result = await leave_space(partner, "household")

    assert result["handed_to"] is None
    space = await resolve_space(principal_for(household["wouter"]), "household")
    assert space.owner_person_id == household["wouter"].id
    assert await membership_for(household["partner"], household["shared"]) is None


async def test_leaving_prefers_a_full_member_over_a_viewer(tx, graph):
    """Stable succession: a viewer inherits only when no member could.

    The viewer is seeded rather than admitted — nothing in the application
    creates one yet, and memberships carry no UPDATE policy, so the row has to
    come in through the policy-free builder.
    """
    owner = await graph.person("owner@example.test", "Owner")
    viewer = await graph.person("viewer@example.test", "Viewer")
    member = await graph.person("member@example.test", "Member")
    space = await graph.shared_space("crew", owner, member)
    await graph.add_membership(viewer, space, MemberRole.VIEWER.value)

    result = await leave_space(principal_for(owner), "crew")

    assert result["handed_to"] == "Member"
    inherited = await resolve_space(principal_for(member), "crew")
    assert inherited.owner_person_id == member.id


async def test_the_last_member_is_told_to_delete_rather_than_leave(tx, household):
    me = principal_for(household["wouter"])
    await remove_member(me, "household", "partner@example.test")
    with pytest.raises(SpaceError, match="delete it instead"):
        await leave_space(me, "household")
    # Refused, not half-done: the cove and the membership both survive.
    assert await resolve_space(me, "household") is not None


async def test_the_personal_space_cannot_be_left_or_deleted(tx, household):
    me = principal_for(household["wouter"])
    with pytest.raises(SpaceError):
        await leave_space(me, "personal")
    with pytest.raises(SpaceError):
        await delete_space(me, "personal")
    assert await resolve_space(me, "personal") is not None


async def test_deleting_is_refused_while_anyone_else_is_a_member(tx, household):
    me = principal_for(household["wouter"])
    with pytest.raises(SpaceError, match="other member"):
        await delete_space(me, "household")
    # Nobody's memory was destroyed on the way to the refusal.
    assert await resolve_space(principal_for(household["partner"]), "household")


async def test_only_the_owner_may_delete(tx, household):
    partner = principal_for(household["partner"])
    with pytest.raises(SpaceError):
        await delete_space(partner, "household")


async def test_deleting_alone_erases_the_cove_and_its_pages(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "household", "notes.md", "kept nowhere else", message="")
    await remove_member(me, "household", "partner@example.test")

    result = await delete_space(me, "household")

    assert result["deleted"] is True and result["pages"] == 1
    with pytest.raises(AccessDenied):
        await resolve_space(me, "household")
    assert (
        await Space.objects().where(Space.id == household["shared"].id).first() is None
    )
    assert await Page.count().where(Page.space_id == household["shared"].id) == 0
    assert await membership_for(household["wouter"], household["shared"]) is None


async def test_deleting_reports_the_object_keys_for_the_caller_to_erase(tx, household):
    """The rows go first and the bytes follow, so the keys have to come back."""
    me = principal_for(household["wouter"])
    await remove_member(me, "household", "partner@example.test")
    await Attachment(
        space_id=household["shared"].id,
        object_key="files/one.pdf",
        filename="one.pdf",
        mime="application/pdf",
        byte_size=1,
        description="a file",
        status=AttachmentStatus.READY.value,
    ).save()

    result = await delete_space(me, "household")

    assert result["file_keys"] == ["files/one.pdf"]
    assert (
        await Attachment.count().where(Attachment.space_id == household["shared"].id)
        == 0
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


async def test_a_stranger_using_the_same_name_is_not_a_collision(tx, graph):
    """Superseded assertion, kept as the record of what changed.

    This used to expect a refusal: cove names were one global namespace, so
    the first person to take 'family' took it from everybody, and the
    collision surfaced as a raw driver error that doubled as a cross-tenant
    existence oracle. Names now live on the membership, so a stranger's cove
    is not in this person's namespace at all and there is nothing to refuse.
    """
    squatter = await graph.person("squatter@x.test", "Squatter")
    victim = await graph.person("victim@x.test", "Victim")
    await graph.personal_space(squatter)
    await graph.personal_space(victim)
    theirs = await create_space(principal_for(squatter), "family")

    mine = await create_space(principal_for(victim), "family")

    assert mine.id != theirs.id
    assert await resolve_space(principal_for(victim), "family") is not None
    # Reusing one of *your own* names is still refused, and still leaves the
    # transaction usable.
    with pytest.raises(SpaceError, match="already have a cove"):
        await create_space(principal_for(victim), "family")
    second = await create_space(principal_for(victim), "family-jones")
    assert second.slug == "family-jones"


async def test_renaming_a_cove_moves_only_my_own_name_for_it(tx, graph):
    """The alias is a column on my membership, so a rename is invisible to
    everybody else -- and it is how an invitee repairs a suffixed name."""
    from reef.spaces import rename_cove

    ann = await graph.person("ann3@x.test", "Ann")
    bo = await graph.person("bo3@x.test", "Bo")
    await graph.personal_space(ann)
    await graph.personal_space(bo)
    cove = await graph.shared_space("house", ann, bo)

    outcome = await rename_cove(principal_for(ann), "house", "home")

    assert outcome == {"was": "house", "now": "home"}
    assert (await resolve_space(principal_for(ann), "home")).id == cove.id
    with pytest.raises(AccessDenied):
        await resolve_space(principal_for(ann), "house")
    # Bo is untouched.
    assert (await resolve_space(principal_for(bo), "house")).id == cove.id


async def test_a_rename_cannot_take_a_name_i_already_use(tx, graph):
    from reef.spaces import rename_cove

    ann = await graph.person("ann4@x.test", "Ann")
    await graph.personal_space(ann)
    await graph.shared_space("house", ann)
    await graph.shared_space("boat", ann)

    with pytest.raises(SpaceError, match="already have a cove"):
        await rename_cove(principal_for(ann), "boat", "house")


async def test_a_cove_cannot_be_renamed_to_personal(tx, graph):
    """It would shadow the private space in every later call."""
    from reef.spaces import rename_cove

    ann = await graph.person("ann5@x.test", "Ann")
    await graph.personal_space(ann)
    await graph.shared_space("house", ann)

    with pytest.raises(SpaceError, match="reserved"):
        await rename_cove(principal_for(ann), "house", "personal")


async def test_an_invitee_who_already_uses_the_name_gets_a_suffixed_one(tx, graph):
    """The inviter cannot see the invitee's other coves, so the alias is
    chosen and taken inside the database rather than guessed here."""
    from reef.access import alias_map

    owner = await graph.person("owner@x.test", "Owner")
    guest = await graph.person("guest@x.test", "Guest")
    await graph.personal_space(owner)
    await graph.personal_space(guest)
    # The guest already calls something 'family'.
    await graph.shared_space("family", guest)
    theirs = await create_space(principal_for(owner), "family")

    await invite(principal_for(owner), "family", "guest@x.test")

    names = await alias_map(principal_for(guest))
    assert names[theirs.id] == "family-2"
    assert (await resolve_space(principal_for(guest), "family-2")).id == theirs.id


async def test_invite_tells_the_inviter_to_relay_it_themselves(tx, household):
    """A cove invite must say that nothing reaches the invitee on its own.

    reef sends no mail, so an invite that reports only success reads as
    "done" when in fact the invitee has been told nothing. ``invite_to_reef``
    has always said so; the cove path -- the one people actually use -- did
    not, which is how a correctly-invited member sat unreachable.
    """
    me = principal_for(household["wouter"])
    result = await invite(me, "household", "anna@example.test")
    assert "sends no invitation email" in result["next_step"]
    assert "anna@example.test" in result["email"]
