"""The allowlist budget: both invite doors, one ceiling.

These run against real PostgreSQL rather than mocks on purpose.
``Person.created_at`` is filled server-side by ``TimestampNow()``, and the
column is ``timestamp without time zone``; a mocked repository would let the
window arithmetic pass here while it silently never enforced in production.
"""

from datetime import timedelta

import pytest

from rif.access import Principal
from rif.invitations import (
    INVITE_BUDGET,
    INVITE_WINDOW_DAYS,
    InviteBudgetExceeded,
    _now,
    allowlist,
    invite_to_reef,
    invites_left,
    next_invite_at,
)
from rif.models import Membership, Person
from rif.spaces import invite


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def spend(me: Principal, count: int) -> None:
    """Mint ``count`` fresh allowlist entries for ``me``.

    :param me: the inviter spending budget
    :param count: how many new addresses to invite
    """
    for n in range(count):
        await allowlist(me, f"spend{n}@example.test")


async def test_our_clock_matches_the_one_created_at_is_written_on(household, seed):
    """Guard the skew that makes every window silently wrong.

    ``created_at`` defaults to Piccolo's ``TimestampNow``, which is *local*
    time — not UTC. If ``_now()`` is ever changed to a UTC basis this drifts
    by the deployment's offset, and every budget test still passes while
    production quietly counts the wrong rows.
    """
    from rif.db import DB

    me = principal_for(household["wouter"])
    before = _now()
    # Committed rather than held open, so the stored timestamp can be read
    # back from a connection the persons policy does not hide it from.
    async with DB.transaction():
        await allowlist(me, "clock@example.test")
    after = _now()

    created_at = await seed.fetchval(
        "SELECT created_at FROM persons WHERE email = $1", "clock@example.test"
    )
    assert before - timedelta(seconds=5) <= created_at
    assert created_at <= after + timedelta(seconds=5)


async def test_fresh_inviter_has_the_full_budget(tx, household):
    me = principal_for(household["wouter"])
    assert await invites_left(me) == INVITE_BUDGET


async def test_invite_to_reef_allowlists_without_any_membership(tx, household):
    me = principal_for(household["wouter"])
    result = await invite_to_reef(me, "curious@example.test", "Curious")

    # The inviter cannot read the row -- persons is self-only -- so this
    # asserts what is observable: reef knows the address, a budget entry was
    # spent (true only if invited_by names the inviter), and no membership
    # was created, which is the whole point of a reef invite.
    rows = await Person.raw(
        "SELECT rif_person_id_by_email({}) AS id", "curious@example.test"
    )
    person_id = rows[0]["id"]
    assert person_id is not None
    assert await Membership.count().where(Membership.person_id == person_id) == 0
    assert result["already_known"] is False
    assert result["invites_left"] == INVITE_BUDGET - 1


async def test_budget_boundary_last_one_lands_next_one_refused(tx, household):
    me = principal_for(household["wouter"])
    await spend(me, INVITE_BUDGET)
    assert await invites_left(me) == 0

    with pytest.raises(InviteBudgetExceeded):
        await allowlist(me, "one-too-many@example.test")

    rows = await Person.raw(
        "SELECT rif_person_id_by_email({}) AS id", "one-too-many@example.test"
    )
    assert rows[0]["id"] is None


async def test_entries_older_than_the_window_do_not_count(household, seed):
    from rif.db import DB

    me = principal_for(household["wouter"])
    async with DB.transaction():
        await spend(me, INVITE_BUDGET)

    # Age every entry past the window rather than moving the clock, so the
    # comparison exercises real stored timestamps.
    stale = _now() - timedelta(days=INVITE_WINDOW_DAYS + 1)
    # Through the seed connection: an unarmed UPDATE on persons is filtered
    # to zero rows and raises nothing, so via the ORM this would quietly do
    # nothing and the assertion below would be meaningless.
    await seed.execute(
        "UPDATE persons SET created_at = $1 WHERE invited_by_person_id = $2",
        stale,
        me.person_id,
    )

    async with DB.transaction():
        assert await invites_left(me) == INVITE_BUDGET
        _, created = await allowlist(me, "after-the-window@example.test")
    assert created is True


async def test_an_address_reef_already_knows_is_free(tx, household):
    """Adding an existing member to another cove must not cost budget."""
    me = principal_for(household["wouter"])
    await spend(me, INVITE_BUDGET)
    assert await invites_left(me) == 0

    entry, created = await allowlist(me, household["partner"].email)
    assert created is False
    assert entry.person_id == household["partner"].id


async def test_both_doors_share_one_budget(tx, household):
    """The bypass this design exists to prevent.

    Cove invites mint the same allowlist entry, so spending the budget
    through ``invite`` must leave nothing for ``invite_to_reef``.
    """
    me = principal_for(household["wouter"])
    for n in range(INVITE_BUDGET):
        await invite(me, "household", f"cove{n}@example.test")

    assert await invites_left(me) == 0
    with pytest.raises(InviteBudgetExceeded):
        await invite_to_reef(me, "via-the-other-door@example.test")


async def test_budget_is_per_inviter(tx, household):
    me = principal_for(household["wouter"])
    them = principal_for(household["partner"])
    await spend(me, INVITE_BUDGET)

    assert await invites_left(me) == 0
    assert await invites_left(them) == INVITE_BUDGET


async def test_refusal_names_when_the_next_invite_unlocks(tx, household):
    me = principal_for(household["wouter"])
    await spend(me, INVITE_BUDGET)

    unlocks = await next_invite_at(me)
    assert unlocks is not None
    # The oldest entry ages out first, so the window's length from now is the
    # outer bound on when a slot returns.
    assert unlocks <= _now() + timedelta(days=INVITE_WINDOW_DAYS)

    with pytest.raises(InviteBudgetExceeded) as excinfo:
        await allowlist(me, "blocked@example.test")
    assert str(unlocks.day) in str(excinfo.value)


async def test_email_is_normalised_before_it_is_stored(tx, household):
    me = principal_for(household["wouter"])
    entry, created = await allowlist(me, "  MiXeD@Example.TEST  ")
    assert entry.email == "mixed@example.test"
    assert created is True

    again, created_again = await allowlist(me, "mixed@example.test")
    assert created_again is False
    assert again.person_id == entry.person_id
