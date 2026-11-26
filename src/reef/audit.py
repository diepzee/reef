"""A record of the privileged acts, so they are visible rather than prevented.

Two kinds of operation earn an entry, and they earn it for different reasons.

Some legitimately reach past the policies: admitting somebody to a space,
removing them, handing a space to a successor. They run inside ``SECURITY
DEFINER`` functions owned by a ``BYPASSRLS`` role because no row policy can
express them without permitting far more. That is the right design, and it
means the guarantee those operations carry is *accountability*, not
*prevention*.

The others are the irreversible ones: erasing an account, destroying a space.
Both stay inside the policies -- ``spaces_owner_delete`` restricts a space's
deletion to its owner, and Postgres enforces that whether or not anything is
logged. They are here because of what they leave behind, which is nothing.
Every other question about a space can be answered by reading it; once it is
gone, a record made at the time is the only thing that can say it existed and
who ended it. Prevention is the database's job in both cases; this is the
memory of the act.

The honest claim this supports is narrow and worth stating exactly: these acts
leave a trail outside the database, on a service the application cannot
rewrite, promptly enough that erasing them afterwards requires a second,
different kind of access. It does **not** make reef secure from whoever can
deploy it -- nothing here does, and the module that ships telemetry says the
same thing at more length.

Identifiers only. Never an email, a display name, an engram path or a body: a
trail that carried those would be a copy of the corpus in a third party's
database, which is the exposure the whole row-level-security effort exists to
shrink. Who acted, on which space, to what effect -- enough to answer "what
happened to my invitation" without becoming a second place the answer leaks
from.
"""

from functools import partial
from uuid import UUID

from reef.db import after_commit
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
MEMBER_ADMITTED = "space.member_admitted"
MEMBER_REMOVED = "space.member_removed"
MEMBER_LEFT = "space.member_left"
OWNERSHIP_TRANSFERRED = "space.ownership_transferred"
SPACE_DELETED = "space.deleted"
ENGRAM_DELETED = "space.engram_deleted"
ACCOUNT_ERASED = "account.erased"


def record_after_commit(action: str, actor: UUID, **fields: object) -> None:
    """Defer an audit event until the surrounding transaction scope commits.

    Used for leaving, whose ownership transfer and removal may be rolled back
    together. A rollback discards both events.

    :param action: one of this module's action constants
    :param actor: the principal who performed it
    :param fields: further identifiers; ids and counts only, never content
    """
    after_commit(partial(record, action, actor, **fields))


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
