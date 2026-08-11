"""Minting allowlist entries: the single door into reef, and its budget.

reef is invitation-only (see :mod:`rif.auth`), and a ``persons`` row *is* the
allowlist entry — it is what lets an unknown subject bind on first sign-in.
Two flows create one: inviting someone into a cove, and inviting someone to
reef itself. Both go through :func:`allowlist` so the budget cannot be walked
around by creating a junk space and inviting into that instead.

The budget protects the resource, not an endpoint: it counts *new* rows, so
inviting an address reef already knows costs nothing.
"""

import os
from datetime import datetime, timedelta

from rif.access import Principal
from rif.models import Person

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


def _minted_in_window(inviter: Principal, now: datetime | None = None):
    """Build the clause matching rows this inviter minted inside the window.

    Returned as a clause rather than a query because the count and the
    unlock-date lookup need different Piccolo query types, and they must
    never disagree about what "in the window" means.

    :param inviter: the person whose budget is in question
    :param now: clock override for tests
    :returns: a Piccolo ``Combinable`` for use in ``.where()``
    """
    return (Person.invited_by_person_id == inviter.person_id) & (
        Person.created_at >= _window_start(now)
    )


async def invites_left(inviter: Principal, now: datetime | None = None) -> int:
    """Return how many allowlist entries this inviter may still mint.

    :param inviter: the person whose budget is in question
    :param now: clock override for tests
    :returns: remaining entries, never negative
    """
    spent = await Person.count().where(_minted_in_window(inviter, now))
    return max(0, INVITE_BUDGET - spent)


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
    oldest = (
        await Person.objects()
        .where(_minted_in_window(inviter, now))
        .order_by(Person.created_at)
        .first()
    )
    if oldest is None:
        return None
    return oldest.created_at + timedelta(days=INVITE_WINDOW_DAYS)


async def allowlist(
    inviter: Principal,
    email: str,
    display_name: str | None = None,
    now: datetime | None = None,
) -> tuple[Person, bool]:
    """Ensure ``email`` is on the allowlist, spending budget only if new.

    The one place a ``persons`` row is created from an invite. An address reef
    already knows is returned untouched and costs nothing, so adding an
    existing member to another cove never approaches the ceiling.

    :param inviter: the person spending the budget
    :param email: the address the invitee will sign in with
    :param display_name: how members see them; defaults to the email's name part
    :param now: clock override for tests
    :raises InviteBudgetExceeded: if a new entry is needed and none remain
    :returns: the person row, and whether this call created it
    """
    email = email.strip().lower()
    person = await Person.objects().where(Person.email == email).first()
    if person is not None:
        return person, False
    if await invites_left(inviter, now) <= 0:
        unlocks = await next_invite_at(inviter, now)
        when = f" Your next invite unlocks {unlocks:%-d %B %Y}." if unlocks else ""
        raise InviteBudgetExceeded(
            f"You have invited {INVITE_BUDGET} new people in the last "
            f"{INVITE_WINDOW_DAYS} days, which is the limit.{when}"
        )
    person = Person(
        email=email,
        display_name=display_name or email.split("@")[0],
        invited_by_person_id=inviter.person_id,
    )
    await person.save()
    return person, True


async def invite_to_reef(
    inviter: Principal,
    email: str,
    display_name: str | None = None,
) -> dict:
    """Invite someone to reef itself, without sharing any cove.

    They arrive in their own personal space, seeded on first sign-in by
    ``ensure_personal_space``. Nothing is disclosed, so unlike a cove invite
    there is nothing here to regret.

    reef sends no mail, so the return value carries the words the inviter
    passes on themselves.

    :param inviter: the person spending the budget
    :param email: the address the invitee will sign in with
    :param display_name: how members see them
    :raises InviteBudgetExceeded: if the inviter's budget is spent
    :returns: outcome with the relay text and remaining budget
    """
    person, created = await allowlist(inviter, email, display_name)
    base_url = os.environ.get("RIF_BASE_URL", "").rstrip("/")
    where = base_url or "reef"
    return {
        "email": person.email,
        "already_known": not created,
        "invites_left": await invites_left(inviter),
        "next_step": (
            f"Tell them to go to {where} and sign in with this exact address. "
            "reef sends no email, so nothing reaches them until you do."
        ),
    }
