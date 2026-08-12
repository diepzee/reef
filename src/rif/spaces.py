"""Space administration: creation, invitation, removal, and onboarding.

The ``spaces``, ``memberships``, and ``persons`` tables carry no RLS; every
function here is therefore an enforcement point and checks authority itself.
The rule is creator-admin: whoever created a space owns it, and only the
owner changes its member list.

Like the rest of the Piccolo port there is no session to thread: queries bind
to the ambient transaction opened by :func:`rif.db.transaction_scope`.
"""

import re
from uuid import UUID

from rif.access import Principal, arm, resolve_space
from rif.invitations import allowlist
from rif.models import Membership, Page, Person, Space, SpaceKind
from rif.pages import save_page
from rif.protocol import PERSONA_PATH, PERSONA_STUB

_SLUG_RE = re.compile(r"[a-z][a-z0-9-]{1,63}\Z")
_RESERVED_PREFIXES = ("personal",)
"""Name prefixes no shared space may use.

Reserving the whole ``personal`` prefix, not just the exact alias, does two
jobs. It stops a space whose name renders as the private one in
``list_spaces`` — a confused-deputy path into an attacker's space — and it
stops a squat on ``personal-{person.id.hex}``, the slug
:func:`ensure_personal_space` derives. That slug is globally unique, so a
squat would make the victim's first sign-in raise inside
``principal_from_claims`` and lock them out of every tool call, permanently.
"""


class SpaceError(Exception):
    """Raised when a space-administration request cannot proceed."""


async def _membership(person_id: UUID, space_id: UUID) -> Membership | None:
    """Fetch one membership row by its real key.

    Piccolo gives every table a single surrogate primary key, so the
    composite ``(person_id, space_id)`` key cannot be fetched by identity the
    way SQLAlchemy's ``session.get`` did it.

    :param person_id: the member
    :param space_id: the space
    :returns: the membership row, or None
    """
    return (
        await Membership.objects()
        .where(Membership.person_id == person_id, Membership.space_id == space_id)
        .first()
    )


async def member_roster(space_id: UUID) -> list[dict]:
    """Return a space's members as display name/email pairs, sorted by name.

    Both consent surfaces read through this: ``list_spaces`` tells a person
    who is in the room, ``prepare_promotion``'s warning names every reader a
    share is about to reach, and the web members panel needs addresses to key
    removal by.

    The disclosure rule is no longer this module's to remember. ``rif_roster``
    returns an email only to the cove's owner and an empty string to everyone
    else, and returns nothing at all to a non-member -- decided in SQL, by
    the armed principal, so a caller that forgets to check gets the safe
    answer rather than the whole roster. The old version selected
    ``Person.email`` outright and trusted every caller to blank it.

    :param space_id: the space to list
    :returns: ``[{"display_name": str, "email": str}, ...]``, sorted by
        display name; ``email`` is ``""`` unless the caller owns the space
    """
    rows = await Person.raw("SELECT * FROM rif_roster({})", space_id)
    return [
        {"display_name": row["member_name"], "email": row["member_email"]}
        for row in rows
    ]


async def member_names(space_id: UUID) -> list[str]:
    """Return the sorted display names of a space's members.

    :param space_id: the space to list
    :returns: display names, sorted
    """
    return [member["display_name"] for member in await member_roster(space_id)]


async def space_owner(space_id: UUID) -> dict | None:
    """Return the display name and email of a space's owner.

    Every member sees the owner's address, unlike ordinary members' -- the
    owner is the cove's accountable contact, which is the existing contract
    and is preserved deliberately. The membership check lives inside the
    function, so a non-member gets nothing.

    :param space_id: the space whose owner is wanted
    :returns: ``{"display_name": str, "email": str}``, or None
    """
    rows = await Person.raw("SELECT * FROM rif_space_owner({})", space_id)
    if not rows:
        return None
    return {
        "display_name": rows[0]["owner_name"],
        "email": rows[0]["owner_email"],
    }


async def display_names(person_ids: list[UUID]) -> dict[UUID, str]:
    """Return display names for the given people.

    Revision authorship has to keep rendering names for co-members whose
    ``persons`` rows a reader cannot otherwise see, which is what this is
    for. Names only -- never addresses.

    :param person_ids: the people to name
    :returns: mapping of person id to display name, omitting unknown ids
    """
    if not person_ids:
        return {}
    rows = await Person.raw(
        "SELECT * FROM rif_display_names({})", list(dict.fromkeys(person_ids))
    )
    return {row["person_id"]: row["display_name"] for row in rows}


async def create_space(principal: Principal, slug: str) -> Space:
    """Create a shared space; the creator becomes owner and first member.

    :param principal: the authenticated person
    :param slug: the space's name — lowercase letters, digits, hyphens
    :raises SpaceError: for an invalid, reserved, or already-taken name
    :returns: the created space
    """
    if not _SLUG_RE.fullmatch(slug) or slug.startswith(_RESERVED_PREFIXES):
        raise SpaceError(
            f"{slug!r} is not a usable space name: 2-64 characters, lowercase "
            "letters, digits, and hyphens, starting with a letter; names "
            "beginning 'personal' are reserved"
        )
    # Unlike every other entry point here this one resolves no existing space,
    # so nothing else arms the principal -- and both the rows it inserts are
    # checked against it once spaces and memberships carry policies.
    await arm(principal)
    if await Space.objects().where(Space.slug == slug).first() is not None:
        raise SpaceError(f"a space named {slug!r} already exists; pick another name")
    space = Space(
        slug=slug, kind=SpaceKind.SHARED.value, owner_person_id=principal.person_id
    )
    await space.save()
    await Membership(person_id=principal.person_id, space_id=space.id).save()
    return space


async def _owned_shared_space(principal: Principal, slug: str) -> Space:
    """Resolve ``slug`` and require it to be a shared space this principal owns.

    :param principal: the authenticated person
    :param slug: the space name as given by the caller
    :raises SpaceError: if the space is personal or owned by someone else
    :returns: the resolved space
    """
    space = await resolve_space(principal, slug)
    if space.kind == SpaceKind.PERSONAL.value:
        raise SpaceError("the personal space cannot be shared or administered")
    if space.owner_person_id != principal.person_id:
        raise SpaceError(f"only the owner of {slug!r} may change its members")
    return space


async def invite(
    principal: Principal,
    slug: str,
    email: str,
    display_name: str | None = None,
) -> dict:
    """Invite an email address into a shared space the principal owns.

    An unknown email becomes a person row on the spot — the runtime
    allowlist entry. The invitee gets in when they first sign in with this
    email, verified, through the unchanged binding path in ``rif.auth``.

    Row creation is delegated to :func:`rif.invitations.allowlist`, which is
    the only place an invite may mint one and which holds the per-inviter
    budget. Routing both invite flows through it is what stops the budget
    being bypassed by creating a junk space and inviting into that.

    :param principal: the authenticated person
    :param slug: the shared space to invite into
    :param email: the address the invitee will sign in with
    :param display_name: how members see them; defaults to the email's name part
    :raises SpaceError: if the principal does not own the space
    :raises InviteBudgetExceeded: if a new entry is needed and none remain
    :returns: outcome with the disclosure text and an ``already_member`` flag
    """
    space = await _owned_shared_space(principal, slug)
    person, _ = await allowlist(principal, email, display_name)
    email = person.email
    membership = await _membership(person.id, space.id)
    already = membership is not None
    if not already:
        await Membership(person_id=person.id, space_id=space.id).save()
    page_count = await Page.count().where(Page.space_id == space.id)
    return {
        "space": slug,
        "email": email,
        "already_member": already,
        "disclosure": (
            f"{email} will permanently see everything in {slug!r}, past and "
            f"future — {page_count} page(s) today. There is no un-sharing "
            "what they read."
        ),
    }


async def remove_member(principal: Principal, slug: str, email: str) -> dict:
    """Remove a member from a shared space the principal owns.

    Removal stops future access; it cannot unshare what was already read.
    Removing an invitee who never signed in (no bound subject, no other
    memberships) also erases the orphaned person row — the typo-repair path.

    :param principal: the authenticated person
    :param slug: the shared space to remove from
    :param email: the member's email
    :raises SpaceError: if not owner, target absent, or target is the owner
    :returns: outcome with a ``person_erased`` flag
    """
    space = await _owned_shared_space(principal, slug)
    email = email.strip().lower()
    person = await Person.objects().where(Person.email == email).first()
    membership = None if person is None else await _membership(person.id, space.id)
    if membership is None:
        raise SpaceError(f"{email} is not a member of {slug!r}")
    if person.id == principal.person_id:
        raise SpaceError("the owner cannot remove themselves from their own space")
    await membership.remove()
    person_erased = False
    if person.subject is None:
        remaining = await Membership.count().where(Membership.person_id == person.id)
        if remaining == 0:
            await person.remove()
            person_erased = True
    return {
        "space": slug,
        "email": email,
        "removed": True,
        "person_erased": person_erased,
    }


async def ensure_personal_space(person_id: UUID, email: str) -> None:
    """Create the person's personal space and starter pages, once.

    Called at first sign-in, with the principal already armed — the space and
    membership inserts below are checked against it once those tables carry
    policies, so arming afterwards would deny a person their own onboarding.

    Takes ids rather than a ``Person`` row because its caller no longer has
    one: identity binding returns only the three columns a principal is built
    from (see ``rif.identity``), deliberately not a full row.

    The slug is derived from the person id — it is globally unique by
    construction and never crosses the tool boundary, because personal spaces
    are always addressed by the ``personal`` alias.

    :param person_id: the newly bound person's id
    :param email: their address, for the principal passed to ``save_page``
    """
    existing = (
        await Space.objects()
        .where(
            Space.kind == SpaceKind.PERSONAL.value,
            Space.owner_person_id == person_id,
        )
        .first()
    )
    if existing is not None:
        return
    space = Space(
        slug=f"personal-{person_id.hex}",
        kind=SpaceKind.PERSONAL.value,
        owner_person_id=person_id,
    )
    await space.save()
    await Membership(person_id=person_id, space_id=space.id).save()
    principal = Principal(person_id=person_id, email=email)
    await save_page(
        principal,
        "personal",
        PERSONA_PATH,
        PERSONA_STUB,
        message="seeded at first sign-in",
        title="Persona",
        allow_protected=True,
    )
