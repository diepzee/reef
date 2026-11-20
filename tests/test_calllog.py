"""The call log: off unless switched on, strictly per person, ninety days."""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
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

#: When the totals below start; every seeded row but the old one is younger.
SINCE = datetime(2026, 11, 1, tzinfo=UTC)

#: Words that must never leave the totals: reasons and a session label.
PRIVATE_REASON = "my divorce lawyer's number"
PRIVATE_SESSION = "session-zebra-7731"


async def _seed_calls(seed, household) -> None:
    """Log calls for two people, past row security, at fixed times.

    Wouter: two recalls (one without a reason) and a retired name. The
    partner: one recall, and one before :data:`SINCE`, which is not counted.

    :param seed: the policy-free connection
    :param household: the two people
    """
    wouter, partner = household["wouter"].id, household["partner"].id
    rows = [
        (wouter, "recall", PRIVATE_REASON, "ok", "2026-11-10"),
        (wouter, "recall", "", "ok", "2026-11-11"),
        (wouter, "read_pages", "", "retired_tool", "2026-11-12"),
        (partner, "recall", "another private reason", "ok", "2026-11-13"),
        (partner, "write", "long ago", "version_conflict", "2026-10-01"),
    ]
    for person, method, reason, outcome, day in rows:
        await seed.execute(
            "INSERT INTO call_log (id, person_id, session_id, method, reason, "
            "writer, outcome, called_at) VALUES (gen_random_uuid(), $1, $2, $3, "
            "$4, 'assistant', $5, $6::timestamp)",
            person,
            PRIVATE_SESSION,
            method,
            reason,
            outcome,
            datetime.fromisoformat(day),
        )


async def test_the_totals_count_each_method_and_outcome_across_people(household, seed):
    """Calls, calls without a reason and people, per method and outcome."""
    await _seed_calls(seed, household)
    totals = await calllog.totals(SINCE)
    assert [
        (t.method, t.outcome, t.calls, t.empty_reasons, t.people) for t in totals
    ] == [
        ("read_pages", "retired_tool", 1, 1, 1),
        ("recall", "ok", 3, 1, 2),
    ]


async def test_the_totals_carry_no_person_reason_session_or_time(
    household, seed, probe
):
    """Through the app's own kind of role: five columns, and only totals in them."""
    await _seed_calls(seed, household)
    rows = await probe.fetch("SELECT * FROM reef_call_log_totals($1)", SINCE)
    assert rows
    assert all(tuple(row.keys()) == CALL_LOG_TOTALS_COLUMNS for row in rows)
    values = " ".join(str(value) for row in rows for value in row.values())
    for private in (
        str(household["wouter"].id),
        str(household["partner"].id),
        household["wouter"].email,
        PRIVATE_REASON,
        "another private reason",
        PRIVATE_SESSION,
        "2026-11",
    ):
        assert private not in values


async def test_a_person_learns_only_totals_about_other_people(household, seed, probe):
    """Armed as the partner: their own rows in the table, only counts beyond.

    The function takes nothing but a moment, so it cannot be narrowed to one
    person, and nobody without a grant may call it.
    """
    await _seed_calls(seed, household)
    async with probe.transaction():
        await probe.execute(
            "SELECT set_config('app.person_id', $1, true)",
            str(household["partner"].id),
        )
        own = await probe.fetch("SELECT method FROM call_log")
        totals = await probe.fetch("SELECT * FROM reef_call_log_totals($1)", SINCE)
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
    assert function["args"] == "p_since timestamp with time zone"
    assert function["prosecdef"] is True
    assert function["owner"] in ("reef_authz", "rif_authz")
    assert not [grant for grant in function["acl"] if grant.startswith("=")]


async def test_the_operators_script_prints_only_the_totals(household, seed):
    """``just call-totals`` prints a header and one line per total, nothing more."""
    await _seed_calls(seed, household)
    root = Path(__file__).resolve().parent.parent
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(root / "scripts" / "call_totals.py"),
        "--days",
        "36500",
        env={**os.environ, "DATABASE_URL": get_settings().database.test_url},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=root,
    )
    stdout, stderr = (stream.decode() for stream in await process.communicate())
    assert process.returncode == 0, stderr
    lines = stdout.splitlines()
    assert [line.split() for line in lines] == [
        list(CALL_LOG_TOTALS_COLUMNS),
        ["read_pages", "retired_tool", "1", "1", "1"],
        ["recall", "ok", "3", "1", "2"],
        ["write", "version_conflict", "1", "0", "1"],
    ]
    everything = stdout + stderr
    for private in (
        str(household["wouter"].id),
        household["partner"].email,
        PRIVATE_REASON,
        PRIVATE_SESSION,
    ):
        assert private not in everything


async def test_the_totals_refuse_a_moment_without_a_time_zone():
    """A naive moment would be read in the server's zone, which nobody sees."""
    with pytest.raises(ValueError, match="time zone"):
        await calllog.totals(SINCE.replace(tzinfo=None))


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
