"""Ending membership erases private usage without touching anyone else's."""

import asyncpg
import pytest
import pytest_asyncio

from reef.access import Principal, arm
from reef.account import delete_account_rows
from reef.db import transaction_scope
from reef.engrams import save_engram
from reef.models import Space
from reef.spaces import delete_space, leave_space, remove_member
from reef.usage import refresh_activation


@pytest_asyncio.fixture
async def used_spaces(household: dict) -> dict[str, Principal]:
    """Give both people usage of two shared engrams and a personal engram.

    :param household: two members and their spaces
    :returns: the principals with committed usage and activation rows
    """
    people = {
        name: Principal(person_id=household[name].id, email=household[name].email)
        for name in ("wouter", "partner")
    }
    async with transaction_scope():
        for person in people.values():
            for path in ("first.md", "second.md"):
                await save_engram(person, "household", path, "used", message="")
            await save_engram(person, "personal", "private.md", "used", message="")
            await refresh_activation(person)
    return people


async def usage_rows(seed: asyncpg.Connection) -> dict[str, list[asyncpg.Record]]:
    """Read complete stored rows, including rows hidden by security policies.

    :param seed: the connection which bypasses row security
    :returns: both usage tables, with each row's space
    """
    return {
        table: await seed.fetch(
            f"SELECT u.*, e.space_id FROM {table} u "
            "JOIN engrams e ON e.id = u.engram_id ORDER BY u.id"
        )
        for table in ("engram_access", "engram_heat")
    }


@pytest.mark.parametrize("way", ["removed", "member_leaves", "owner_leaves"])
async def test_membership_end_erases_only_departing_usage(
    household: dict,
    used_spaces: dict[str, Principal],
    seed: asyncpg.Connection,
    way: str,
) -> None:
    """Each departure removes every shared usage row, preserving all others."""
    departing = used_spaces["wouter" if way == "owner_leaves" else "partner"]
    before = await usage_rows(seed)
    async with transaction_scope():
        if way == "removed":
            await remove_member(used_spaces["wouter"], "household", departing.email)
        else:
            await leave_space(departing, "household")
    after = await usage_rows(seed)
    for table, rows in before.items():
        expected = [
            row
            for row in rows
            if not (
                row["person_id"] == departing.person_id
                and row["space_id"] == household["shared"].id
            )
        ]
        assert len(rows) - len(expected) == 2
        assert after[table] == expected


async def test_transfer_alone_keeps_both_members_usage(
    household: dict, used_spaces: dict[str, Principal], seed: asyncpg.Connection
) -> None:
    """Changing owner does not end the former owner's membership."""
    before = await usage_rows(seed)
    async with transaction_scope():
        await arm(used_spaces["wouter"])
        await Space.raw(
            "SELECT reef_transfer_space_ownership({}, {})",
            household["shared"].id,
            used_spaces["partner"].person_id,
        )
    assert await usage_rows(seed) == before


@pytest.mark.parametrize("way", ["removed", "member_leaves", "owner_leaves"])
async def test_rollback_restores_membership_and_usage(
    household: dict,
    used_spaces: dict[str, Principal],
    seed: asyncpg.Connection,
    way: str,
) -> None:
    """A failed request rolls back departure and its cleanup together."""
    before = await usage_rows(seed)
    memberships = await seed.fetch("SELECT * FROM memberships ORDER BY id")
    with pytest.raises(RuntimeError, match="abort"):
        async with transaction_scope():
            if way == "removed":
                await remove_member(
                    used_spaces["wouter"], "household", used_spaces["partner"].email
                )
            else:
                await leave_space(
                    used_spaces["wouter" if way == "owner_leaves" else "partner"],
                    "household",
                )
            raise RuntimeError("abort")
    assert await usage_rows(seed) == before
    assert await seed.fetch("SELECT * FROM memberships ORDER BY id") == memberships
    assert (
        await seed.fetchval(
            "SELECT owner_person_id FROM spaces WHERE id = $1", household["shared"].id
        )
        == used_spaces["wouter"].person_id
    )


async def test_space_deletion_cascades_usage(
    household: dict, used_spaces: dict[str, Principal], seed: asyncpg.Connection
) -> None:
    """Deleting the engrams of a sole-member space erases their usage."""
    async with transaction_scope():
        await remove_member(
            used_spaces["wouter"], "household", used_spaces["partner"].email
        )
    before = await usage_rows(seed)
    async with transaction_scope():
        await delete_space(used_spaces["wouter"], "household")
    assert await usage_rows(seed) == {
        table: [row for row in rows if row["space_id"] != household["shared"].id]
        for table, rows in before.items()
    }


@pytest.mark.parametrize("person", ["wouter", "partner"])
async def test_account_deletion_cascades_usage(
    used_spaces: dict[str, Principal], seed: asyncpg.Connection, person: str
) -> None:
    """Account erasure removes its usage, including when ownership transfers."""
    before = await usage_rows(seed)
    async with transaction_scope():
        await delete_account_rows(used_spaces[person])
    assert await usage_rows(seed) == {
        table: [
            row for row in rows if row["person_id"] != used_spaces[person].person_id
        ]
        for table, rows in before.items()
    }
