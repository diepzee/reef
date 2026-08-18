"""Space administration: creation, invitation, removal, departure, and onboarding.

The ``spaces``, ``memberships``, and ``persons`` tables have carried row-level
security since the identity policies landed, so the checks here are the outer
of two layers rather than the only one. They stay because a policy filters
rows silently — a caller who is not the owner sees an empty result, not a
refusal — and an administrative tool owes the caller a reason. The rule is
creator-admin: whoever created a space owns it, and only the owner changes its
member list or destroys it.

Ownership is not a life sentence. An owner who leaves a shared cove hands it
to a successor rather than taking it down with them, which is the same
invariant account deletion keeps: departing never destroys somebody else's
memory. Destroying a cove is consequently only reachable by whoever is alone
in it.

Like the rest of the Piccolo port there is no session to thread: queries bind
to the ambient transaction opened by :func:`rif.db.transaction_scope`.
"""

import re
from uuid import UUID

from rif import audit
from rif.access import PERSONAL_ALIAS, Principal, arm, resolve_space
from rif.invitations import allowlist, relay_instructions
from rif.models import (
    Attachment,
    MemberRole,
    Membership,
    Page,
    Person,
    Revision,
    Space,
    SpaceKind,
)
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
    """Return a space's members, sorted by display name.

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

    Two queries rather than one, and not by preference: ``rif_roster`` lives
    in ``disclosure_statements`` which historical migrations re-run against
    the August schema, so it cannot name the avatar columns without breaking
    every database built from scratch. ``rif_member_faces`` names them from
    ``avatar_statements``, which those migrations never call. See
    :func:`rif.rls.avatar_statements`.

    ``avatar_len`` is ``None`` for a member who has chosen no picture, which
    is what tells the UI to draw their initials instead.

    :param space_id: the space to list
    :returns: ``[{"person_id": UUID, "display_name": str, "email": str,
        "avatar_len": int | None}, ...]``, sorted by display name; ``email``
        is ``""`` unless the caller owns the space
    """
    rows = await Person.raw("SELECT * FROM rif_roster({})", space_id)
    faces = await Person.raw("SELECT * FROM rif_member_faces({})", space_id)
    sizes = {face["person_id"]: face["avatar_len"] for face in faces}
    return [
        {
            "person_id": row["person_id"],
            "display_name": row["member_name"],
            "email": row["member_email"],
            "avatar_len": sizes.get(row["person_id"]),
        }
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

    The name is *this person's* name for the cove, stored on their
    membership. Cove names are no longer a global namespace, so a name
    somebody else already uses is not a collision at all -- only a name
    already in this person's own list is, and that one they can see, so the
    check below is honest rather than a race against an invisible row.

    :param principal: the authenticated person
    :param slug: the cove's name — lowercase letters, digits, hyphens
    :raises SpaceError: for an invalid, reserved, or already-used name
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
    if await _alias_taken(principal.person_id, slug):
        raise SpaceError(f"you already have a cove called {slug!r}; pick another name")
    space = Space(
        slug=slug, kind=SpaceKind.SHARED.value, owner_person_id=principal.person_id
    )
    await space.save()
    # Through the definer function like every other admission, so the alias
    # is chosen and taken in one statement. The creator owns the cove by the
    # line above, which is what the function checks.
    admitted = await Space.raw(
        "SELECT rif_admit_member({}, {}, {}, {}) AS alias",
        space.id,
        principal.person_id,
        slug,
        MemberRole.MEMBER.value,
    )
    if not admitted or admitted[0]["alias"] is None:
        raise SpaceError(f"could not create {slug!r}; nothing was changed")
    return space


async def _alias_taken(person_id: UUID, alias: str) -> bool:
    """Report whether this person already uses this cove name.

    :param person_id: the person whose names are in question
    :param alias: the name to test
    :returns: True when the name is already theirs
    """
    return (
        await Membership.objects()
        .where(Membership.person_id == person_id, Membership.alias == alias)
        .first()
    ) is not None


async def rename_cove(principal: Principal, alias: str, new_alias: str) -> dict:
    """Change what this principal calls a cove, for this principal only.

    Nobody else's name for it moves: the alias is a column on the caller's
    own membership row, which is the whole point of keeping it there. It is
    also what makes an admitted name repairable -- an invitee whose preferred
    name was taken is admitted under a suffixed one, and this is how they fix
    it.

    :param principal: the authenticated person
    :param alias: the cove's current name, as this principal knows it
    :param new_alias: the name to use instead
    :raises SpaceError: for an invalid, reserved, or already-used new name
    :raises AccessDenied: if no such cove is reachable
    :returns: the old and new names
    """
    space = await resolve_space(principal, alias)
    if space.kind == SpaceKind.PERSONAL.value:
        raise SpaceError("the personal space is always called 'personal'")
    if not _SLUG_RE.fullmatch(new_alias) or new_alias.startswith(_RESERVED_PREFIXES):
        raise SpaceError(
            f"{new_alias!r} is not a usable space name: 2-64 characters, "
            "lowercase letters, digits, and hyphens, starting with a letter; "
            "names beginning 'personal' are reserved"
        )
    if new_alias != alias and await _alias_taken(principal.person_id, new_alias):
        raise SpaceError(
            f"you already have a cove called {new_alias!r}; pick another name"
        )
    await Membership.update({Membership.alias: new_alias}).where(
        Membership.person_id == principal.person_id,
        Membership.space_id == space.id,
    )
    return {"was": alias, "now": new_alias}


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
    role: str = MemberRole.MEMBER.value,
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
    :param role: ``member`` (read and write) or ``viewer`` (read only)
    :raises SpaceError: if the principal does not own the space, or the role
        is not one a membership can hold
    :raises InviteBudgetExceeded: if a new entry is needed and none remain
    :returns: outcome with the role, disclosure text, ``already_member``, and
        the relay instruction the inviter must pass on themselves
    """
    if role not in {MemberRole.MEMBER.value, MemberRole.VIEWER.value}:
        raise SpaceError(f"a membership is 'member' or 'viewer', not {role!r}")
    space = await _owned_shared_space(principal, slug)
    entry, _ = await allowlist(principal, email, display_name)
    email = entry.email
    membership = await _membership(entry.person_id, space.id)
    already = membership is not None
    if not already:
        # The alias has to be free for the *invitee*, whose other memberships
        # the inviter cannot see -- so choosing it and taking it happen in one
        # statement inside the database. The cove's own name is offered first
        # and suffixed only if the invitee already uses it for something else.
        admitted = await Space.raw(
            "SELECT rif_admit_member({}, {}, {}, {}) AS alias",
            space.id,
            entry.person_id,
            space.slug,
            role,
        )
        if not admitted or admitted[0]["alias"] is None:
            raise SpaceError(f"could not admit {email} to {slug!r}")
        audit.record(
            audit.MEMBER_ADMITTED,
            actor=principal.person_id,
            space_id=space.id,
            member_id=entry.person_id,
        )
    page_count = await Page.count().where(Page.space_id == space.id)
    rights = (
        "read and write everything"
        if role == MemberRole.MEMBER.value
        else "read everything, without being able to write"
    )
    return {
        "space": slug,
        "email": email,
        "role": role,
        "already_member": already,
        "next_step": relay_instructions(),
        "disclosure": (
            f"{email} will permanently {rights} in {slug!r}, past and "
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
    rows = await Person.raw("SELECT rif_person_id_by_email({}) AS id", email)
    person_id = rows[0]["id"] if rows else None
    if person_id is None or await _membership(person_id, space.id) is None:
        raise SpaceError(f"{email} is not a member of {slug!r}")
    if person_id == principal.person_id:
        raise SpaceError("the owner cannot remove themselves from their own space")

    # The removal and the orphan check are one statement in the database.
    # Deciding here whether the departing person still belongs anywhere would
    # need a person-wide membership count, and the remover has no right to
    # see memberships in coves they are not in -- so the count would come
    # back short and erase somebody still active elsewhere.
    outcome = await Person.raw(
        "SELECT * FROM rif_remove_member({}, {})", space.id, person_id
    )
    if not outcome or not outcome[0]["removed"]:
        raise SpaceError(f"{email} is not a member of {slug!r}")
    person_erased = bool(outcome[0]["person_erased"])
    audit.record(
        audit.MEMBER_REMOVED,
        actor=principal.person_id,
        space_id=space.id,
        member_id=person_id,
        person_erased=person_erased,
    )
    return {
        "space": slug,
        "email": email,
        "removed": True,
        "person_erased": person_erased,
    }


async def _others_in(space_id: UUID, person_id: UUID) -> list[Membership]:
    """Return a space's memberships other than this person's.

    :param space_id: the space to count
    :param person_id: the person to exclude
    :returns: the remaining membership rows
    """
    return await Membership.objects().where(
        Membership.space_id == space_id,
        Membership.person_id != person_id,
    )


async def delete_space(principal: Principal, slug: str) -> dict:
    """Destroy a shared space the principal owns and is alone in.

    Deletion is deliberately refused while anybody else is a member, and the
    refusal names the alternative. Handing a cove on is what leaving already
    does, and destroying a cove other people keep their memory in should not
    be reachable by one confirmation: an owner who truly wants it gone can
    remove each member first, which is the same act performed visibly.

    The row goes before the bytes, the order :func:`rif.attachments
    .delete_attachment` argues for — the two stores cannot be made atomic, and
    unreferenced bytes are safer wreckage than rows whose bytes 404. The keys
    are therefore collected here, while the rows still exist, and returned for
    the caller to erase once this transaction has committed.

    Children are deleted explicitly rather than left to the cascades. They
    would cascade correctly — every foreign key pointing at ``spaces`` is
    ``ON DELETE CASCADE`` — but a cascade runs as an internal referential
    action that bypasses row-level security, whereas these statements are
    checked against the principal's own membership. Nothing is relied upon
    that the policies would not already permit.

    :param principal: the authenticated person
    :param slug: the shared space to destroy
    :raises SpaceError: if personal, not owned, or anyone else is still a member
    :returns: outcome with the page count and the object keys still to erase
    """
    space = await _owned_shared_space(principal, slug)
    others = await _others_in(space.id, principal.person_id)
    if others:
        raise SpaceError(
            f"{slug!r} still has {len(others)} other member(s); leave it to hand "
            "it on, or remove them first if it must be destroyed"
        )

    file_keys = (
        await Attachment.select(Attachment.object_key)
        .where(Attachment.space_id == space.id)
        .output(as_list=True)
    )
    page_ids = (
        await Page.select(Page.id).where(Page.space_id == space.id).output(as_list=True)
    )
    if page_ids:
        await Revision.delete().where(Revision.page_id.is_in(page_ids))
    await Attachment.delete().where(Attachment.space_id == space.id)
    await Page.delete().where(Page.space_id == space.id)
    # The principal's own membership goes with the space, by cascade.
    await Space.delete().where(Space.id == space.id)
    # Recorded not because the policies were bypassed -- they were not -- but
    # because nothing survives to be read afterwards. Counts, never the slug:
    # a cove's name is the user's words, and the trail takes identifiers only.
    audit.record(
        audit.COVE_DELETED,
        actor=principal.person_id,
        space_id=space.id,
        page_count=len(page_ids),
        file_count=len(file_keys),
    )
    return {
        "space": slug,
        "deleted": True,
        "pages": len(page_ids),
        "file_keys": file_keys,
    }


async def leave_space(principal: Principal, slug: str) -> dict:
    """Leave a shared space, handing it on if the principal owned it.

    The invariant this preserves is the one account deletion already keeps:
    departing never destroys somebody else's memory. An owner who leaves
    passes the cove to a successor — preferring a full member over a viewer,
    then the lowest id so the choice is stable rather than row-order
    dependent, the same rule as :func:`rif.account.delete_account_rows`.

    Leaving as the last member is refused rather than quietly deleting the
    cove and everything in it. That is a different act with a different
    consequence, and it has its own verb.

    :param principal: the authenticated person
    :param slug: the shared space to leave
    :raises SpaceError: if personal, or the principal is its only member
    :returns: outcome, including who inherited the cove if anyone did
    """
    space = await resolve_space(principal, slug)
    if space.kind == SpaceKind.PERSONAL.value:
        raise SpaceError("the personal space cannot be left")

    others = await _others_in(space.id, principal.person_id)
    if not others:
        raise SpaceError(
            f"you are the only member of {slug!r}; delete it instead of leaving it"
        )

    handed_to = None
    if space.owner_person_id == principal.person_id:
        successor = min(
            others,
            key=lambda membership: (
                membership.role != MemberRole.MEMBER.value,
                str(membership.person_id),
            ),
        )
        # Read the name before departing: the roster is membership-scoped, so
        # once the membership below is gone this principal can no longer ask
        # who is in the cove -- including who they just handed it to.
        names = await display_names([successor.person_id])
        handed_over = await Space.raw(
            "SELECT rif_transfer_space_ownership({}, {}) AS ok",
            space.id,
            successor.person_id,
        )
        if not handed_over or not handed_over[0]["ok"]:
            raise SpaceError(f"could not hand {slug!r} on; nothing was changed")
        handed_to = names.get(successor.person_id)
        audit.record(
            audit.OWNERSHIP_TRANSFERRED,
            actor=principal.person_id,
            space_id=space.id,
            successor_id=successor.person_id,
        )

    await Membership.delete().where(
        Membership.space_id == space.id,
        Membership.person_id == principal.person_id,
    )
    return {"space": slug, "left": True, "handed_to": handed_to}


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
    # Arms rather than assuming the caller did. The inserts below are checked
    # against the principal, and onboarding failing is a person locked out of
    # reef entirely on their first sign-in -- too sharp an edge to leave to
    # call order.
    principal = Principal(person_id=person_id, email=email)
    await arm(principal)
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
    await Membership(
        person_id=person_id, space_id=space.id, alias=PERSONAL_ALIAS
    ).save()
    await save_page(
        principal,
        "personal",
        PERSONA_PATH,
        PERSONA_STUB,
        message="seeded at first sign-in",
        title="Persona",
        allow_protected=True,
    )
