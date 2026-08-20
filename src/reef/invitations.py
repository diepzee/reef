"""Minting allowlist entries: the single door into reef, and its budget.

reef is invitation-only (see :mod:`reef.auth`), and a ``persons`` row *is* the
allowlist entry — it is what lets an unknown subject bind on first sign-in.
Two flows create one: inviting someone into a cove, and inviting someone to
reef itself. Both go through :func:`allowlist` so the budget cannot be walked
around by creating a junk cove and inviting into that instead.

The budget protects the resource, not an endpoint: it counts *new* rows, so
inviting an address reef already knows costs nothing.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from reef import audit
from reef.access import Principal, arm
from reef.config import env
from reef.models import Person

#: New allowlist entries one member may mint per window.
INVITE_BUDGET = 5
#: Length of the rolling window, in days. Rolling rather than calendar so
#: there is no burst at midnight on the 1st, and so the refusal can name a
#: date rather than a quota that silently refills.
INVITE_WINDOW_DAYS = 30


class InviteBudgetExceeded(Exception):
    """Raised when an inviter has spent their allowlist budget."""


def _now() -> datetime:
    """Return the current time on the same clock ``created_at`` is written on.

    ``Person.created_at`` defaults to Piccolo's ``TimestampNow``, documented
    as "the current timestamp, in the local time of the machine that Python
    is running on", and rendering as ``current_timestamp`` in Postgres — the
    database server's local time. Neither is UTC, and the column is
    ``timestamp without time zone``, so nothing records the offset.

    So this must be naive *local* time. Using ``datetime.now(UTC)`` here
    silently skews every window by the deployment's UTC offset — two hours
    in Brussels, zero on Railway, which is exactly the kind of bug that
    passes locally and rots in production. Do not "fix" this to UTC.

    :returns: the current local time, without tzinfo
    """
    # DTZ005 wants a tz-aware call. An aware datetime is precisely the bug
    # this function exists to avoid: it would not match the naive local
    # values Piccolo stores.
    return datetime.now()  # noqa: DTZ005


def _window_start(now: datetime | None = None) -> datetime:
    """Return the oldest timestamp still inside the budget window.

    :param now: clock override for tests; defaults to wall time
    :returns: the window's lower bound, naive local time
    """
    return (now or _now()) - timedelta(days=INVITE_WINDOW_DAYS)


async def invites_left(inviter: Principal, now: datetime | None = None) -> int:
    """Return how many allowlist entries this inviter may still mint.

    :param inviter: the person whose budget is in question
    :param now: clock override for tests
    :returns: remaining entries, never negative
    """
    await arm(inviter)
    # Counted by the database against the armed principal, not by a WHERE
    # this module composes: once persons carries a policy an inviter cannot
    # read the rows they minted, and a count that silently came back 0 would
    # hand out unlimited invites rather than fail closed.
    rows = await Person.raw(
        "SELECT reef_invites_minted({}) AS spent", INVITE_WINDOW_DAYS
    )
    return max(0, INVITE_BUDGET - (rows[0]["spent"] if rows else 0))


async def next_invite_at(
    inviter: Principal, now: datetime | None = None
) -> datetime | None:
    """Return when this inviter's budget next frees a slot.

    The oldest entry inside the window is the first to age out, so that row's
    creation plus the window length is the moment one slot returns.

    :param inviter: the person whose budget is in question
    :param now: clock override for tests
    :returns: the unlock time, or None if nothing is pending
    """
    await arm(inviter)
    rows = await Person.raw(
        "SELECT reef_oldest_invite({}) AS oldest", INVITE_WINDOW_DAYS
    )
    oldest = rows[0]["oldest"] if rows else None
    if oldest is None:
        return None
    return oldest + timedelta(days=INVITE_WINDOW_DAYS)


def relay_instructions() -> str:
    """Return the words the inviter must pass on themselves.

    reef sends no mail. Both invite flows therefore owe the inviter the same
    sentence, and it lives here so neither can drift from the other or quietly
    omit it -- a cove invite that reports only success reads as "done" while
    the invitee has in fact been told nothing.

    :returns: the relay instruction, naming the deployment's own URL if set
    """
    base_url = (env("BASE_URL") or "").rstrip("/")
    where = base_url or "reef"
    return (
        f"Tell them to go to {where} and sign in with this exact address. "
        "reef sends no invitation email, so nothing reaches them until you do."
    )


@dataclass(frozen=True)
class AllowlistEntry:
    """An address that may sign in, and the id reef knows it by."""

    person_id: UUID
    email: str


async def allowlist(
    inviter: Principal,
    email: str,
    display_name: str | None = None,
    now: datetime | None = None,
) -> tuple[AllowlistEntry, bool]:
    """Ensure ``email`` is on the allowlist, spending budget only if new.

    The one place a ``persons`` row is created from an invite. An address reef
    already knows is returned untouched and costs nothing, so adding an
    existing member to another cove never approaches the ceiling.

    :param inviter: the person spending the budget
    :param email: the address the invitee will sign in with
    :param display_name: how members see them; defaults to the email's name part
    :param now: clock override for tests
    Returns an :class:`AllowlistEntry` rather than a ``Person`` row on
    purpose. Under the identity policies an inviter cannot read a stranger's
    row at all, so there is nothing to read back -- and nothing the callers
    need beyond the id and the address they supplied.

    :raises InviteBudgetExceeded: if a new entry is needed and none remain
    :returns: the entry, and whether this call created it
    """
    await arm(inviter)
    email = email.strip().lower()
    # Only the id comes back. Inviting somebody who already has an account
    # must link a membership without letting the inviter read a stranger's
    # row -- so the lookup answers "who, if anyone" and nothing more.
    rows = await Person.raw("SELECT reef_person_id_by_email({}) AS id", email)
    existing_id = rows[0]["id"] if rows else None
    if existing_id is not None:
        return AllowlistEntry(person_id=existing_id, email=email), False
    # The budget is counted and spent in one statement, by the database.
    # Checking it here and inserting afterwards is a check-then-act: two
    # invitations racing for the last slot both see one free and both land.
    #
    # The insert is a definer function for a second reason -- save() emits
    # INSERT ... RETURNING, and Postgres applies SELECT policies to what a
    # RETURNING gives back, so a self-only persons policy refuses the inviter
    # their own invitee and reports it as a check-policy violation.
    rows = await Person.raw(
        "SELECT reef_allowlist_person({}, {}, {}, {}) AS id",
        email,
        display_name or email.split("@")[0],
        INVITE_WINDOW_DAYS,
        INVITE_BUDGET,
    )
    new_id = rows[0]["id"] if rows else None
    if new_id is None:
        unlocks = await next_invite_at(inviter, now)
        when = f" Your next invite unlocks {unlocks:%-d %B %Y}." if unlocks else ""
        raise InviteBudgetExceeded(
            f"You have invited {INVITE_BUDGET} new people in the last "
            f"{INVITE_WINDOW_DAYS} days, which is the limit.{when}"
        )
    audit.record(audit.INVITE_MINTED, actor=inviter.person_id, invitee_id=new_id)
    return AllowlistEntry(person_id=new_id, email=email), True


async def invite_to_reef(
    inviter: Principal,
    email: str,
    display_name: str | None = None,
) -> dict:
    """Invite someone to reef itself, without sharing any cove.

    They arrive in their own personal cove, seeded on first sign-in by
    ``ensure_personal_cove``. Nothing is disclosed, so unlike a cove invite
    there is nothing here to regret.

    reef sends no mail, so the return value carries the words the inviter
    passes on themselves.

    :param inviter: the person spending the budget
    :param email: the address the invitee will sign in with
    :param display_name: how members see them
    :raises InviteBudgetExceeded: if the inviter's budget is spent
    :returns: outcome with the relay text and remaining budget
    """
    entry, created = await allowlist(inviter, email, display_name)
    return {
        "email": entry.email,
        "already_known": not created,
        "invites_left": await invites_left(inviter),
        "next_step": relay_instructions(),
    }
