"""Search totals count across people without opening either private log."""

import asyncio
import os
import sys
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from reef import searchlog
from reef.access import Principal
from reef.config import get_settings
from reef.db import transaction_scope
from reef.engrams import save_engram
from reef.spaces import leave_space

TODAY_UTC = "date_trunc('day', now() AT TIME ZONE 'UTC')"
PRIVATE_QUERY = "my private diagnosis"
COLUMNS = ("searches", "misses")


async def _search(
    connection: asyncpg.Connection,
    person: UUID,
    *,
    session: str | None = "private-session",
    returned: tuple[UUID, ...] = (),
    before_today: timedelta = timedelta(days=2),
    query: str = PRIVATE_QUERY,
) -> UUID:
    """Store one search relative to the database's UTC clock.

    :param connection: the seeding or armed connection
    :param person: whose search it is
    :param session: the conversation label, or none
    :param returned: engrams returned by the search
    :param before_today: how long before the start of today it happened
    :param query: the private query
    :returns: the search's id
    """
    identity = uuid4()
    await connection.execute(
        "INSERT INTO search_log (id, person_id, query, engram_ids, session_id, "
        "searched_at) VALUES ($1, $2, $3, $4, $5, "
        f"{TODAY_UTC} - $6::interval)",
        identity,
        person,
        query,
        list(returned),
        session,
        before_today,
    )
    return identity


async def _access(
    connection: asyncpg.Connection,
    search: UUID,
    engram: UUID,
    *,
    after: timedelta = timedelta(minutes=1),
    via: str = "read",
    other_person: UUID | None = None,
    other_session: str | None = None,
    without_session: bool = False,
) -> None:
    """Store an access relative to one search.

    :param connection: the seeding connection
    :param search: the search to follow
    :param engram: the engram opened
    :param after: the delay after searching
    :param via: how the engram was used
    :param other_person: another reader, when testing a mismatch
    :param other_session: another conversation, when testing a mismatch
    :param without_session: omit the read's session label
    """
    await connection.execute(
        "INSERT INTO engram_access (id, person_id, engram_id, via, weight, "
        "session_id, accessed_at) SELECT gen_random_uuid(), "
        "coalesce($2::uuid, person_id), $3, $4, 1, "
        "CASE WHEN $7 THEN NULL ELSE coalesce($5::text, session_id) END, "
        "searched_at + $6::interval "
        "FROM search_log WHERE id = $1",
        search,
        other_person,
        engram,
        via,
        other_session,
        after,
        without_session,
    )


async def _seed_searches(seed: asyncpg.Connection, household: dict) -> UUID:
    """Give each person a search and a read, and one older search.

    :param seed: the policy-free connection
    :param household: the two people and their shared space
    :returns: the shared engram's id
    """
    person = household["wouter"]
    async with transaction_scope():
        engram = await save_engram(
            Principal(person.id, person.email),
            "household",
            "private.md",
            "Private text.",
            message="seed",
        )
    for name in ("wouter", "partner"):
        search = await _search(seed, household[name].id)
        await _access(seed, search, engram.id)
    await _search(seed, person.id, before_today=timedelta(days=30))
    return engram.id


async def test_totals_return_two_numbers_and_nothing_private(household, seed, probe):
    """No query, person, engram, session, time or count of people leaves it."""
    await _seed_searches(seed, household)
    [row] = await probe.fetch("SELECT * FROM reef_search_log_totals(7)")
    assert tuple(row.keys()) == COLUMNS
    assert tuple(row.values()) == (2, 2)
    assert all(type(value) is int for value in row.values())


@pytest.mark.parametrize("person_name", [None, "wouter", "partner"])
async def test_totals_never_open_either_log(household, seed, probe, person_name):
    """Unarmed callers, assistants and the web app keep the same row boundary.

    Both transports arm the same person id; sharing the engram grants no
    access to another person's searches or reading history.
    """
    await _seed_searches(seed, household)
    async with probe.transaction():
        person_id = str(household[person_name].id) if person_name else ""
        await probe.execute("SELECT set_config('app.person_id', $1, true)", person_id)
        before = [
            await probe.fetch(f"SELECT * FROM {table} ORDER BY id")
            for table in ("search_log", "engram_access")
        ]
        assert tuple(
            await probe.fetchrow("SELECT * FROM reef_search_log_totals(7)")
        ) == (2, 2)
        after = [
            await probe.fetch(f"SELECT * FROM {table} ORDER BY id")
            for table in ("search_log", "engram_access")
        ]
        assert before == after
        for rows in after:
            if person_name:
                assert rows
                assert {str(row["person_id"]) for row in rows} == {person_id}
            else:
                assert rows == []


async def test_only_app_roles_can_call_the_fixed_function(seed):
    """The caller cannot narrow the count or rewrite the bypassing function."""
    row = await seed.fetchrow(
        "SELECT pg_get_function_identity_arguments(oid) AS args, prosecdef, "
        "pg_get_userbyid(proowner) AS owner, proacl::text[] AS acl, proconfig, "
        "provolatile::text FROM pg_proc WHERE proname = 'reef_search_log_totals'"
    )
    assert row is not None
    assert row["args"] == "p_days integer"
    assert row["prosecdef"] is True
    assert row["owner"] in ("reef_authz", "rif_authz")
    assert row["provolatile"] == "s"
    assert row["proconfig"] == ["search_path=pg_catalog, public, pg_temp"]
    assert not [grant for grant in row["acl"] if grant.startswith("=")]
    assert {grant.split("=")[0] for grant in row["acl"]} <= {
        "reef_authz",
        "rif_authz",
        "reef",
        "reef_app",
        "reef_probe",
        "rif",
        "rif_app",
        "rif_probe",
    }


@pytest.mark.parametrize("days", [0, 6, 90, 91, None])
async def test_database_refuses_periods_outside_the_search_totals_bounds(probe, days):
    """The function itself enforces the period, even without the wrapper."""
    with pytest.raises(asyncpg.InvalidParameterValueError):
        await probe.fetch("SELECT * FROM reef_search_log_totals($1)", days)


@pytest.mark.parametrize("days", [7, 89])
async def test_wrapper_accepts_both_bounds(household, seed, days):
    """The wrapper returns the database's two counts, with no principal."""
    await _seed_searches(seed, household)
    total = await searchlog.totals(days)
    assert (total.searches, total.misses) == (2 if days == 7 else 3, 2)


@pytest.mark.parametrize("days", [6, 90, 91])
async def test_wrapper_refuses_periods_outside_the_bounds(days):
    """The Python caller gets the same range as a direct database caller."""
    with pytest.raises(ValueError, match="7 to 89"):
        await searchlog.totals(days)


async def test_an_empty_period_returns_zeroes(probe):
    """A period without searches still has one answer with both counts."""
    assert tuple(await probe.fetchrow("SELECT * FROM reef_search_log_totals(7)")) == (
        0,
        0,
    )


async def test_window_is_whole_utc_days_in_every_session_zone(household, probe):
    """Include the start, exclude today, and ignore the session's time zone."""
    async with probe.transaction():
        await probe.execute(
            "SELECT set_config('app.person_id', $1, true)", str(household["wouter"].id)
        )
        for age in (
            timedelta(days=7, seconds=1),
            timedelta(days=7),
            timedelta(seconds=1),
            timedelta(0),
        ):
            await _search(probe, household["wouter"].id, before_today=age)
        for zone in ("UTC", "<-5754>+57:54", "Pacific/Chatham"):
            await probe.execute("SELECT set_config('TimeZone', $1, true)", zone)
            assert tuple(
                await probe.fetchrow("SELECT * FROM reef_search_log_totals(7)")
            ) == (2, 0)


async def test_flagged_test_accounts_count_in_neither_number(household, seed, probe):
    """Scripted activity is left out, as it is in the call-log totals."""
    await _seed_searches(seed, household)
    await seed.execute(
        "INSERT INTO test_accounts (person_id) VALUES ($1)", household["wouter"].id
    )
    assert tuple(await probe.fetchrow("SELECT * FROM reef_search_log_totals(89)")) == (
        1,
        1,
    )


@pytest.mark.parametrize("leave_shared", [False, True])
async def test_misses_agree_with_distinct_searches_in_the_persons_log(
    household, seed, probe, leave_shared
):
    """Rules agree before and after the person's usage ends with membership.

    Leaving removes those reads from both counts while keeping searches.
    """
    engram = await _seed_searches(seed, household)
    person = household["wouter"]
    async with transaction_scope():
        second_engram = await save_engram(
            Principal(person.id, person.email),
            "household",
            "second.md",
            "Another private engram.",
            message="seed",
        )
    await seed.execute("DELETE FROM search_log")
    await seed.execute("DELETE FROM engram_access")
    cases = (
        ({}, {"after": timedelta(0)}, True),
        ({}, {"after": timedelta(minutes=59, seconds=59)}, True),
        ({}, {"after": timedelta(hours=1)}, False),
        ({}, {"after": timedelta(microseconds=-1)}, False),
        ({}, {"via": "write"}, False),
        ({}, {"other_person": household["partner"].id}, False),
        ({}, {"other_session": "elsewhere"}, False),
        ({"session": None}, {}, False),
        ({}, {"without_session": True}, False),
        ({"returned": (engram,)}, {}, False),
    )
    expected = set()
    for index, (search_options, access_options, missed) in enumerate(cases):
        query = f"{PRIVATE_QUERY} {index}"
        search = await _search(
            seed,
            person.id,
            query=query,
            **{"session": f"session-{index}", **search_options},
        )
        await _access(seed, search, engram, **access_options)
        if missed:
            await _access(seed, search, engram, **access_options)
            await _access(seed, search, second_engram.id, **access_options)
            expected.add(query)
    async with transaction_scope():
        rows = await searchlog.misses(Principal(person.id, person.email))
    assert {row["query"] for row in rows} == expected
    assert len(rows) == 3 * len(expected)
    for query in expected:
        assert {row["engram_id"] for row in rows if row["query"] == query} == {
            engram,
            second_engram.id,
        }
    distinct_searches = {(row["query"], row["searched_at"]) for row in rows}
    assert tuple(await probe.fetchrow("SELECT * FROM reef_search_log_totals(7)")) == (
        len(cases),
        len(distinct_searches),
    )
    if leave_shared:
        searches = await seed.fetch("SELECT * FROM search_log ORDER BY id")
        async with transaction_scope():
            principal = Principal(person.id, person.email)
            await leave_space(principal, "household")
            assert await searchlog.misses(principal) == []
        assert tuple(
            await probe.fetchrow("SELECT * FROM reef_search_log_totals(7)")
        ) == (len(cases), 0)
        assert await seed.fetch("SELECT * FROM search_log ORDER BY id") == searches


async def test_identical_searches_are_still_separate_searches(household, seed, probe):
    """Equal query text and time never collapse two actual searches."""
    await _seed_searches(seed, household)
    await _search(seed, household["wouter"].id)
    assert tuple(await probe.fetchrow("SELECT * FROM reef_search_log_totals(7)")) == (
        3,
        3,
    )


@pytest.mark.parametrize(
    ("after", "missed"),
    [
        (timedelta(minutes=9, seconds=59), 1),
        (timedelta(minutes=10), 0),
        (timedelta(minutes=15), 0),
    ],
)
async def test_todays_reads_cannot_change_yesterdays_totals(
    household, seed, probe, after, missed
):
    """Only reads before midnight count, in every caller time zone."""
    engram = await _seed_searches(seed, household)
    await seed.execute("DELETE FROM search_log")
    await seed.execute("DELETE FROM engram_access")
    search = await _search(
        seed, household["wouter"].id, before_today=timedelta(minutes=10)
    )
    assert tuple(await probe.fetchrow("SELECT * FROM reef_search_log_totals(7)")) == (
        1,
        0,
    )
    await _access(seed, search, engram, after=after)
    async with probe.transaction():
        for zone in ("UTC", "<-5754>+57:54", "Pacific/Chatham"):
            await probe.execute("SELECT set_config('TimeZone', $1, true)", zone)
            assert tuple(
                await probe.fetchrow("SELECT * FROM reef_search_log_totals(7)")
            ) == (1, missed)


async def _script(*arguments: str) -> tuple[str, str, int]:
    """Run the operator's script against this worktree's test database.

    :param arguments: command-line arguments
    :returns: stdout, stderr and exit code
    """
    root = Path(__file__).resolve().parent.parent
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(root / "scripts" / "search_totals.py"),
        *arguments,
        env={**os.environ, "DATABASE_URL": get_settings().database.test_url},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=root,
    )
    stdout, stderr = await process.communicate()
    return stdout.decode(), stderr.decode(), process.returncode


async def test_script_prints_only_the_two_counts(household, seed):
    """Print a header and one numeric row, as the call totals script does."""
    await _seed_searches(seed, household)
    stdout, stderr, code = await _script("--days", "89")
    assert code == 0, stderr
    assert [line.split() for line in stdout.splitlines()] == [list(COLUMNS), ["3", "2"]]
    assert PRIVATE_QUERY not in stdout + stderr


async def test_script_refuses_less_than_a_week():
    """Refuse before querying the database and print no data."""
    stdout, stderr, code = await _script("--days", "6")
    assert code != 0
    assert stdout == ""
    assert "7" in stderr


async def test_script_refuses_the_partial_oldest_day():
    """Ninety whole days could include rows the hourly purge removes today."""
    stdout, stderr, code = await _script("--days", "90")
    assert code != 0
    assert stdout == ""
    assert "89" in stderr
