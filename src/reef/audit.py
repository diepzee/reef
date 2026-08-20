"""A record of the privileged acts, so they are visible rather than prevented.

Two kinds of operation earn an entry, and they earn it for different reasons.

Some legitimately reach past the policies: admitting somebody to a cove,
removing them, handing a cove to a successor. They run inside ``SECURITY
DEFINER`` functions owned by a ``BYPASSRLS`` role because no row policy can
express them without permitting far more. That is the right design, and it
means the guarantee those operations carry is *accountability*, not
*prevention*.

The others are the irreversible ones: erasing an account, destroying a cove.
Both stay inside the policies -- ``coves_owner_delete`` restricts a cove's
deletion to its owner, and Postgres enforces that whether or not anything is
logged. They are here because of what they leave behind, which is nothing.
Every other question about a cove can be answered by reading it; once it is
gone, a record made at the time is the only thing that can say it existed and
who ended it. Prevention is the database's job in both cases; this is the
memory of the act.

The honest claim this supports is narrow and worth stating exactly: these acts
leave a trail outside the database, on a service the application cannot
rewrite, promptly enough that erasing them afterwards requires a second,
different kind of access. It does **not** make reef secure from whoever can
deploy it -- nothing here does, and the module that ships telemetry says the
same thing at more length.

Identifiers only. Never an email, a display name, a page path or a body: a
trail that carried those would be a copy of the corpus in a third party's
database, which is the exposure the whole row-level-security effort exists to
shrink. Who acted, on which cove, to what effect -- enough to answer "what
happened to my invitation" without becoming a second place the answer leaks
from.
"""

from uuid import UUID

from reef.telemetry import is_configured

# The complete set. Adding one is a deliberate act: it means either a new way
# for authority to be exercised outside the policies, or a new way to destroy
# something past recovering -- both worth noticing in review rather than
# discovering in a log.
INVITE_MINTED = "invite.minted"
# The launch exception (reef.opendoor). Its actor is the new person themselves,
# uniquely among these: nobody else is accountable for the admission, which is
# exactly why it is worth a line in the log.
OPEN_DOOR_ADMITTED = "invite.open_door_admitted"
MEMBER_ADMITTED = "cove.member_admitted"
MEMBER_REMOVED = "cove.member_removed"
OWNERSHIP_TRANSFERRED = "cove.ownership_transferred"
COVE_DELETED = "cove.deleted"
PAGE_DELETED = "cove.page_deleted"
ACCOUNT_ERASED = "account.erased"


def record(action: str, actor: UUID, **fields: object) -> None:
    """Record one privileged act.

    A no-op when telemetry is not configured, so local development and the
    test suite neither require credentials nor reach the network -- and, more
    importantly, so a telemetry outage can never fail the operation being
    recorded. An audit trail that can refuse an account deletion is worse
    than one that occasionally misses an entry.

    :param action: one of this module's action constants
    :param actor: the principal who performed it
    :param fields: further identifiers; ids and counts only, never content
    """
    if not is_configured():
        return

    import logfire

    logfire.info(
        "privileged: {action}",
        action=action,
        actor_id=str(actor),
        **{key: _safe(value) for key, value in fields.items()},
    )


def _safe(value: object) -> object:
    """Render a field value, keeping uuids readable and everything else plain.

    :param value: the value to render
    :returns: a value safe to attach to a span
    """
    return str(value) if isinstance(value, UUID) else value
