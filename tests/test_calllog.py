"""The call log: off unless switched on, strictly per person, ninety days."""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import asyncpg
import pytest

from reef import calllog
from reef.access import Principal, arm
from reef.config import get_settings
from reef.db import transaction_scope
from reef.engrams import save_engram
from reef.export import build_full_dump
from reef.models import CallLog, Revision
from reef.rls import CALL_LOG_TOTALS_COLUMNS
from reef.worker import purge_old_logs


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


@pytest.fixture
def logging_on(monkeypatch):
    """Switch call logging on, as the operator would."""
    monkeypatch.setattr(get_settings().calls, "log", True)


async def _call(principal, reason="to answer a question", method="find", **kwargs):
    """Record one call with sensible defaults."""
    await calllog.record(
        principal,
        session_id=kwargs.pop("session_id", "s1"),
        writer=kwargs.pop("writer", "assistant"),
        method=method,
        reason=reason,
        duration_ms=kwargs.pop("duration_ms", 12),
        returned=kwargs.pop("returned", 3),
        outcome=kwargs.pop("outcome", "ok"),
    )


async def _rows(principal) -> list[dict]:
    """Return the call log as the principal sees it, oldest first."""
    await arm(principal)
    return await CallLog.select().order_by(CallLog.called_at)


async def test_nothing_is_recorded_unless_switched_on(tx, household):
    me = principal_for(household["wouter"])
    await _call(me)
    assert await _rows(me) == []


async def test_a_call_is_recorded_with_its_reason(tx, household, logging_on):
    me = principal_for(household["wouter"])
    await _call(me, reason="looking for the boiler manual")
    [row] = await _rows(me)
    assert row["person_id"] == me.person_id
    assert (row["method"], row["writer"], row["outcome"]) == ("find", "assistant", "ok")
    assert (row["reason"], row["returned"], row["duration_ms"]) == (
        "looking for the boiler manual",
        3,
        12,
    )
    assert row["session_id"] == "s1"


async def test_a_long_reason_is_cut_to_200_characters(tx, household, logging_on):
    me = principal_for(household["wouter"])
    await _call(me, reason="because " * 100)
    [row] = await _rows(me)
    assert len(row["reason"]) == 200


async def test_a_call_still_answers_when_recording_fails(
    household, logging_on, monkeypatch, caplog
):
    monkeypatch.setattr(calllog, "CALL_LOG_MAX_REASON", 10_000)
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await _call(me, reason="secret diagnosis " * 100)
        assert await _rows(me) == []
    assert "CheckViolationError" in caplog.text
    assert "secret diagnosis" not in caplog.text


async def test_a_person_sees_only_their_own_calls(household, logging_on):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    async with transaction_scope():
        await _call(me)
    async with transaction_scope():
        assert await _rows(partner) == []
    async with transaction_scope():
        assert len(await _rows(me)) == 1


async def test_a_call_cannot_be_logged_in_somebody_elses_name(household):
    me = principal_for(household["wouter"])
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with transaction_scope():
            await arm(me)
            await CallLog.raw(
                "INSERT INTO call_log (id, person_id, method, writer, outcome) "
                "VALUES (gen_random_uuid(), {}, 'find', 'assistant', 'ok')",
                household["partner"].id,
            )


async def test_a_logged_call_cannot_be_rewritten(household, logging_on):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await _call(me, reason="first")
    async with transaction_scope():
        await arm(me)
        await CallLog.raw("UPDATE call_log SET reason = 'other'")
    async with transaction_scope():
        assert [row["reason"] for row in await _rows(me)] == ["first"]


async def test_an_unknown_writer_is_refused(household, logging_on, caplog):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await _call(me, writer="robot")
        assert await _rows(me) == []


async def test_old_calls_are_deleted_for_everybody_and_young_ones_kept(
    household, seed, logging_on
):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    for person, reason in ((me, "old"), (partner, "old"), (me, "young")):
        async with transaction_scope():
            await _call(person, reason=reason)
    await seed.execute(
        "UPDATE call_log SET called_at = called_at - interval '91 days' "
        "WHERE reason = 'old'"
    )
    assert await calllog.purge() == 2
    left = await seed.fetch("SELECT reason FROM call_log")
    assert [row["reason"] for row in left] == ["young"]
    assert await calllog.purge() == 0


async def test_the_worker_purges_the_call_log_with_the_search_log(
    household, seed, logging_on
):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await _call(me)
    await seed.execute("UPDATE call_log SET called_at = called_at - interval '91 days'")
    assert await purge_old_logs() == 1
    assert await seed.fetchval("SELECT count(*) FROM call_log") == 0


class PurgeFailed(Exception):
    """A purge that went wrong, with a message no log line may carry."""


async def _old_rows_in_both_logs(seed, person) -> None:
    """Seed one row past its time in the search log and one in the call log.

    :param seed: the policy-free connection
    :param person: whose rows they are
    """
    await seed.execute(
        "INSERT INTO search_log (id, person_id, query, engram_ids, searched_at) "
        "VALUES (gen_random_uuid(), $1, 'old', '{}', "
        "LOCALTIMESTAMP - interval '91 days')",
        person.id,
    )
    await seed.execute(
        "INSERT INTO call_log (id, person_id, method, writer, outcome, called_at) "
        "VALUES (gen_random_uuid(), $1, 'recall', 'assistant', 'ok', "
        "LOCALTIMESTAMP - interval '91 days')",
        person.id,
    )


@pytest.mark.parametrize(
    ("failing", "purged_table", "kept_table"),
    [
        ("searchlog", "call_log", "search_log"),
        ("calllog", "search_log", "call_log"),
    ],
)
async def test_a_failing_purge_does_not_stop_the_other_one(
    household, seed, monkeypatch, caplog, failing, purged_table, kept_table
):
    """Each log is purged on its own: one failing leaves the other on time.

    Only the failure's class is logged, never its message.
    """

    async def broken() -> int:
        """Fail as a purge might, with a message that must stay private."""
        raise PurgeFailed("secret diagnosis")

    monkeypatch.setattr(f"reef.{failing}.purge", broken)
    await _old_rows_in_both_logs(seed, household["wouter"])
    with caplog.at_level("INFO", logger="reef.worker"):
        assert await purge_old_logs() == 1
    assert await seed.fetchval(f"SELECT count(*) FROM {purged_table}") == 0
    assert await seed.fetchval(f"SELECT count(*) FROM {kept_table}") == 1
    assert "PurgeFailed" in caplog.text
    assert "secret diagnosis" not in caplog.text


async def test_the_purge_cannot_be_asked_for_a_shorter_time(seed):
    arguments = await seed.fetchval(
        "SELECT pronargs FROM pg_proc WHERE proname = 'reef_purge_call_log'"
    )
    assert arguments == 0


async def test_the_full_export_carries_only_the_persons_own_calls(
    tx, household, logging_on
):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    await save_engram(me, "personal", "a.md", "Text.", message="x")
    await _call(me, reason="mine")
    await _call(partner, reason="theirs")
    with ZipFile(BytesIO(await build_full_dump(me))) as archive:
        calls = json.loads(archive.read("data/calls.json"))
        manifest = json.loads(archive.read("manifest.json"))
    assert [(c["method"], c["reason"], c["writer"]) for c in calls] == [
        ("find", "mine", "assistant")
    ]
    assert manifest["contents"]["call_log"] == 1


# --- totals across everybody, without reading anybody's calls -----------------

#: Words that must never leave the totals: reasons and a session label.
PRIVATE_REASON = "my divorce lawyer's number"
PRIVATE_SESSION = "session-zebra-7731"


#: The start of today on a UTC clock, as the database sees it: where every
#: window of totals ends. ``called_at`` holds UTC clock time, so calls are
#: placed relative to this in SQL, with no time zone in between.
TODAY_UTC = "date_trunc('day', now() AT TIME ZONE 'UTC')"


async def _log_at(connection, person, method, reason, outcome, before_today) -> None:
    """Log one call a given time before the start of today, UTC clock.

    Placed by the database's clock, not Python's, so the two cannot disagree
    about which day it is.

    :param connection: the policy-free connection, or one armed as ``person``
    :param person: whose call it is
    :param method: the method called
    :param reason: the reason given
    :param outcome: how the call ended
    :param before_today: how long before the start of today, as a timedelta
    """
    await connection.execute(
        "INSERT INTO call_log (id, person_id, session_id, method, reason, writer, "
        "outcome, called_at) VALUES (gen_random_uuid(), $1, $2, $3, $4, "
        f"'assistant', $5, {TODAY_UTC} - $6::interval)",
        person.id,
        PRIVATE_SESSION,
        method,
        reason,
        outcome,
        before_today,
    )


async def _seed_calls(seed, household) -> None:
    """Log calls for two people in the last week, and one older call.

    Wouter: two recalls (one without a reason) and a retired name. The
    partner: one recall this week, and a write 30 days ago. None is near a
    window's edge, so a run that crosses midnight counts them the same.

    :param seed: the policy-free connection
    :param household: the two people
    """
    wouter, partner = household["wouter"], household["partner"]
    day = timedelta(days=1)
    await _log_at(seed, wouter, "recall", PRIVATE_REASON, "ok", 3 * day)
    await _log_at(seed, wouter, "recall", "", "ok", 2 * day)
    await _log_at(seed, wouter, "read_pages", "", "retired_tool", day)
    await _log_at(seed, partner, "recall", "another reason", "ok", 2 * day)
    await _log_at(seed, partner, "write", "long ago", "version_conflict", 30 * day)


async def test_the_totals_count_each_method_and_outcome_across_people(household, seed):
    """Calls, and calls without a reason, per method and outcome, over a week."""
    await _seed_calls(seed, household)
    totals = await calllog.totals(7)
    assert [(t.method, t.outcome, t.calls, t.empty_reasons) for t in totals] == [
        ("read_pages", "retired_tool", 1, 1),
        ("recall", "ok", 3, 1),
    ]


async def test_the_totals_carry_no_person_reason_session_or_time(
    household, seed, probe
):
    """Through the app's own kind of role: four columns, and only totals in them.

    Not even how many people: in a reef of three, that would point at one.
    """
    await _seed_calls(seed, household)
    rows = await probe.fetch("SELECT * FROM reef_call_log_totals(30)")
    assert rows
    assert all(tuple(row.keys()) == CALL_LOG_TOTALS_COLUMNS for row in rows)
    assert "people" not in CALL_LOG_TOTALS_COLUMNS
    values = " ".join(str(value) for row in rows for value in row.values())
    for private in (
        str(household["wouter"].id),
        str(household["partner"].id),
        household["wouter"].email,
        PRIVATE_REASON,
        "another reason",
        PRIVATE_SESSION,
        str(datetime.now(UTC).year),
    ):
        assert private not in values


async def test_a_person_learns_only_totals_about_other_people(household, seed, probe):
    """Armed as the partner: their own rows in the table, only counts beyond.

    The function takes nothing but a number of days, so it cannot be narrowed
    to one person or one moment, and nobody without a grant may call it.
    """
    await _seed_calls(seed, household)
    async with probe.transaction():
        await probe.execute(
            "SELECT set_config('app.person_id', $1, true)",
            str(household["partner"].id),
        )
        own = await probe.fetch("SELECT method FROM call_log")
        totals = await probe.fetch("SELECT * FROM reef_call_log_totals(7)")
    assert sorted(row["method"] for row in own) == ["recall", "write"]
    assert {(row["method"], row["calls"]) for row in totals} == {
        ("read_pages", 1),
        ("recall", 3),
    }
    function = await seed.fetchrow(
        "SELECT pg_get_function_identity_arguments(oid) AS args, prosecdef, "
        "pg_get_userbyid(proowner) AS owner, proacl::text[] AS acl "
        "FROM pg_proc WHERE proname = 'reef_call_log_totals'"
    )
    assert function["args"] == "p_days integer"
    assert function["prosecdef"] is True
    assert function["owner"] in ("reef_authz", "rif_authz")
    assert not [grant for grant in function["acl"] if grant.startswith("=")]


@pytest.mark.parametrize("days", [6, 90, 91, None])
async def test_database_refuses_periods_outside_the_totals_bounds(probe, days):
    """Exclude short windows and the retention day that shrinks during today."""
    with pytest.raises(asyncpg.InvalidParameterValueError):
        await probe.fetch("SELECT * FROM reef_call_log_totals($1)", days)


@pytest.mark.parametrize("days", [6, 90, 91])
async def test_wrapper_refuses_periods_outside_the_totals_bounds(days: int) -> None:
    """Python callers get the same range as database callers."""
    with pytest.raises(ValueError, match="7 to 89"):
        await calllog.totals(days)


@pytest.mark.parametrize("days", [7, 89])
async def test_a_week_and_eighty_nine_days_are_counted(household, seed, days):
    """The bounds themselves are allowed."""
    await _seed_calls(seed, household)
    assert sum(total.calls for total in await calllog.totals(days)) == (
        4 if days == 7 else 5
    )


#: Calls at the edges of a week's window, by how long before the start of today.
EDGES = {
    "before": timedelta(days=7, seconds=1),
    "first": timedelta(days=7),
    "last": timedelta(seconds=1),
    "today": timedelta(0),
}


async def _edge_totals(probe, person, zones: list[str]) -> dict[str, list[str]]:
    """Log calls at the edges of a week, then read a week's totals in each zone.

    All in one transaction, through the app's kind of role, armed as
    ``person``: ``now()`` stands still within a transaction, so the window
    cannot move between logging and counting, even across midnight.

    :param probe: the non-owner connection
    :param person: whose calls they are
    :param zones: the ``TimeZone`` settings to count under, in turn
    :returns: the methods counted, by zone
    """
    answers = {}
    async with probe.transaction():
        await probe.execute(
            "SELECT set_config('app.person_id', $1, true)", str(person.id)
        )
        for method, before_today in EDGES.items():
            await _log_at(probe, person, method, "", "ok", before_today)
        for zone in zones:
            await probe.execute("SELECT set_config('TimeZone', $1, true)", zone)
            rows = await probe.fetch("SELECT method FROM reef_call_log_totals(7)")
            answers[zone] = [row["method"] for row in rows]
    return answers


async def test_the_window_is_whole_days_in_utc(household, probe):
    """From the start of the day a week ago to the start of today, in UTC.

    A call at the first moment of the window counts, one a second earlier
    does not. Today does not count yet, so polling the totals during a day
    never shows when one call was made.
    """
    assert await _edge_totals(probe, household["wouter"], ["UTC"]) == {
        "UTC": ["first", "last"]
    }


async def test_the_callers_time_zone_cannot_move_the_window(household, probe):
    """A session may set any zone, even one offset to the minute.

    If the window followed it, a caller could aim the totals at any minute.
    """
    zones = ["UTC", "<-5754>+57:54", "Pacific/Chatham"]
    answers = await _edge_totals(probe, household["wouter"], zones)
    assert answers == {zone: ["first", "last"] for zone in zones}


async def test_the_operators_script_prints_only_the_totals(household, seed):
    """``just call-totals`` prints a header and one line per total, nothing more."""
    await _seed_calls(seed, household)
    stdout, stderr, code = await _script("--days", "89")
    assert code == 0, stderr
    assert [line.split() for line in stdout.splitlines()] == [
        list(CALL_LOG_TOTALS_COLUMNS),
        ["read_pages", "retired_tool", "1", "1"],
        ["recall", "ok", "3", "1"],
        ["write", "version_conflict", "1", "0"],
    ]
    for private in (
        str(household["wouter"].id),
        household["partner"].email,
        PRIVATE_REASON,
        PRIVATE_SESSION,
    ):
        assert private not in stdout + stderr


async def test_the_operators_script_refuses_less_than_a_week():
    """Refused before the database is asked, and nothing is printed."""
    stdout, stderr, code = await _script("--days", "6")
    assert code != 0
    assert stdout == ""
    assert "7" in stderr


async def test_the_operators_script_refuses_the_partial_oldest_day() -> None:
    """Ninety whole days could include calls the purge removes today."""
    stdout, stderr, code = await _script("--days", "90")
    assert code != 0
    assert stdout == ""
    assert "89" in stderr


async def _script(*arguments: str) -> tuple[str, str, int]:
    """Run ``scripts/call_totals.py`` against the test database.

    :param arguments: its command-line arguments
    :returns: what it printed, what it logged, and its exit code
    """
    root = Path(__file__).resolve().parent.parent
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(root / "scripts" / "call_totals.py"),
        *arguments,
        env={**os.environ, "DATABASE_URL": get_settings().database.test_url},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=root,
    )
    stdout, stderr = (stream.decode() for stream in await process.communicate())
    return stdout, stderr, process.returncode


def test_the_reason_is_scrubbed_from_telemetry():
    from reef import telemetry

    assert "reason" in telemetry.SCRUB_PATTERNS


# --- who made a change ---------------------------------------------------------


async def test_assistant_writes_record_assistant(tx, household):
    me = principal_for(household["wouter"])
    engram = await save_engram(me, "personal", "a.md", "Text.", message="x")
    [revision] = await Revision.select().where(Revision.engram_id == engram.id)
    assert revision["channel"] == "assistant"


async def test_website_writes_record_person(tx, household):
    me = principal_for(household["wouter"])
    engram = await save_engram(
        me, "personal", "a.md", "Text.", message="x", channel="person"
    )
    [revision] = await Revision.select().where(Revision.engram_id == engram.id)
    assert revision["channel"] == "person"


async def test_a_write_through_the_website_api_records_person(api, world, seed):
    from conftest import _login

    alice, _bob, _ = world
    _login(api, alice)
    response = await api.put(
        "/api/engrams/team/notes/a.md",
        json={"body": "Text.\n", "message": "create"},
        headers={"X-Rif-Csrf": "1"},
    )
    assert response.status_code == 200
    assert await seed.fetchval("SELECT channel FROM revisions") == "person"


async def test_record_in_an_aborted_transaction_returns_and_logs_the_class(
    household, logging_on, caplog
):
    """The caller's own transaction is already dead; recording must not add to it."""
    me = principal_for(household["wouter"])
    async with transaction_scope():
        with pytest.raises(asyncpg.PostgresError):
            await CallLog.raw("SELECT * FROM no_such_table")
        await _call(me, reason="private diagnosis")
    assert "call log: a call was not recorded" in caplog.text
    assert "InFailedSQLTransactionError" in caplog.text
    assert "private diagnosis" not in caplog.text


async def test_record_without_an_open_transaction_writes_its_row(household, logging_on):
    me = principal_for(household["wouter"])
    await _call(me, reason="outside any scope")
    async with transaction_scope():
        assert [row["reason"] for row in await _rows(me)] == ["outside any scope"]


async def test_an_exported_revision_carries_its_channel(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "a.md", "Text.", message="x")
    await save_engram(me, "personal", "b.md", "Text.", message="x", channel="person")
    with ZipFile(BytesIO(await build_full_dump(me))) as archive:
        revisions = json.loads(archive.read("data/revisions.json"))
    assert {r["path"]: r["channel"] for r in revisions} == {
        "a.md": "assistant",
        "b.md": "person",
    }
