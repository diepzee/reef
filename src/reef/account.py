"""Permanent account erasure while preserving other members' shared coves."""

from dataclasses import dataclass

from reef import audit
from reef.access import Principal, accessible_coves, alias_map
from reef.models import (
    Attachment,
    Cove,
    CoveKind,
    MemberRole,
    Membership,
    Page,
    Person,
    Revision,
)


@dataclass
class AccountDeletion:
    """Rows affected by account deletion, plus object keys to erase post-commit."""

    deleted_coves: list[str]
    transferred_coves: list[str]
    file_keys: list[str]


async def delete_account_rows(principal: Principal) -> AccountDeletion:
    """Delete a person and private/sole-member data inside the current transaction.

    Shared coves with another member survive: ownership moves deterministically
    to a remaining full member (or promotes a viewer when no member remains),
    and the departing person's membership disappears through the person FK.
    Their revision author references become ``NULL`` through ``ON DELETE SET
    NULL``. Personal and sole-member owned coves are removed before the person.

    Object bytes cannot participate in the database transaction. Their keys
    are returned so the web response can remove them only after commit; a
    failure then leaves unreachable orphan bytes, never live metadata pointing
    at missing content.

    :param principal: authenticated person to erase
    :returns: deleted/transferred cove aliases and post-commit file keys
    """
    # accessible_coves arms the principal; the person lookup has to come
    # after it, not before. Read first and the query runs unarmed, which once
    # persons carries a policy returns nothing -- and this function would
    # report that it deleted an account it had not touched.
    coves = await accessible_coves(principal)
    aliases = await alias_map(principal)
    person = await Person.objects().where(Person.id == principal.person_id).first()
    if person is None:
        return AccountDeletion([], [], [])

    deleted_coves = []
    transferred_coves = []

    for cove in coves:
        if cove.kind == CoveKind.PERSONAL.value:
            deleted_coves.append(cove)
            continue
        if cove.owner_person_id != principal.person_id:
            continue

        remaining = await Membership.objects().where(
            Membership.cove_id == cove.id,
            Membership.person_id != principal.person_id,
        )
        if not remaining:
            deleted_coves.append(cove)
            continue

        # Prefer a full member, then the lowest id, so the choice is stable
        # rather than dependent on row order.
        successor = min(
            remaining,
            key=lambda membership: (
                membership.role != MemberRole.MEMBER.value,
                str(membership.person_id),
            ),
        )
        # Both writes happen inside the database. Promoting a viewer changes
        # somebody else's membership row and reassigning the cove changes its
        # owner away from the caller -- neither is expressible as a row policy
        # without permitting a great deal more, so the authority check lives
        # one line above the writes instead.
        handed_over = await Cove.raw(
            "SELECT reef_transfer_cove_ownership({}, {}) AS ok",
            cove.id,
            successor.person_id,
        )
        if not handed_over or not handed_over[0]["ok"]:
            continue
        audit.record(
            audit.OWNERSHIP_TRANSFERRED,
            actor=principal.person_id,
            cove_id=cove.id,
            successor_id=successor.person_id,
        )
        transferred_coves.append(aliases[cove.id])

    deleted_ids = [cove.id for cove in deleted_coves]
    file_keys = (
        await Attachment.select(Attachment.object_key)
        .where(Attachment.cove_id.is_in(deleted_ids))
        .output(as_list=True)
        if deleted_ids
        else []
    )
    deleted_coves = [aliases[cove.id] for cove in deleted_coves]

    # Delete cove content explicitly while the principal's membership still
    # arms every write policy. Relying on simultaneous cascades from Person ->
    # Cove -> Page and Person -> Revision author can make Postgres run the
    # SET NULL author trigger against a revision whose page cascade is already
    # in flight, violating the revision's page FK.
    page_ids = (
        await Page.select(Page.id)
        .where(Page.cove_id.is_in(deleted_ids))
        .output(as_list=True)
        if deleted_ids
        else []
    )
    if page_ids:
        await Revision.delete().where(Revision.page_id.is_in(page_ids))
    if deleted_ids:
        await Attachment.delete().where(Attachment.cove_id.is_in(deleted_ids))
        await Page.delete().where(Page.cove_id.is_in(deleted_ids))
        await Cove.delete().where(Cove.id.is_in(deleted_ids))

    # Foreign-key actions now do the rest: surviving shared revisions
    # anonymize, invites retain their invitees, and memberships/promotions
    # belonging to this person disappear.
    await Person.delete().where(Person.id == principal.person_id)
    audit.record(
        audit.ACCOUNT_ERASED,
        actor=principal.person_id,
        coves_deleted=len(deleted_coves),
        coves_transferred=len(transferred_coves),
    )

    return AccountDeletion(
        deleted_coves=deleted_coves,
        transferred_coves=transferred_coves,
        file_keys=file_keys,
    )
