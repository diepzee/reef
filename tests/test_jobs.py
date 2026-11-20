"""The job queue and the worker that drains it.

The queue is under row security like every other table, with one function
allowed to look across people. Most of what is worth proving here is about
that boundary: what the function hands out, and that everything after it
happens as the job's person.

These tests manage their own transactions rather than requesting ``tx``:
the worker opens its own, and has to see committed rows.
"""

import asyncio
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest

from reef import worker as worker_module
from reef.access import Principal, arm
from reef.context import build_index
from reef.db import DB, transaction_scope
from reef.engrams import save_engram
from reef.jobs import (
    BACKFILL_INDEX_FIELDS,
    claim,
    enqueue,
    retry_delay,
)
from reef.models import Engram, Job
from reef.rls import AUTHZ_ROLE, FORMER_AUTHZ_ROLE, JOB_LEASE_SECONDS, JOB_MAX_ATTEMPTS
from reef.worker import drain, run_job, serve

WORKER = "test-worker"

_SECRET = "the boiler is a Vaillant"


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def _queue(person, kind=BACKFILL_INDEX_FIELDS, subject_ids=(), **kwargs) -> bool:
    """Queue a job as ``person``, in a committed transaction of its own."""
    async with transaction_scope():
        return await enqueue(principal_for(person), kind, subject_ids, **kwargs)


async def _jobs_seen_by(person, kind: str | None = None) -> list[dict]:
    """Return the job rows ``person`` can read, oldest first.

    Saving an engram queues an activation refresh as a side effect, so a test
    that saves engrams and cares about one kind of job names it.
    """
    async with transaction_scope():
        await arm(principal_for(person))
        query = Job.select().order_by(Job.created_at)
        if kind is not None:
            query = query.where(Job.kind == kind)
        return await query


async def _expire_lease(seed, attempts: int | None = None) -> None:
    """Make every running job look as if its worker vanished long ago."""
    await seed.execute(
        "UPDATE jobs SET locked_at = LOCALTIMESTAMP - make_interval(secs => $1), "
        "attempts = COALESCE($2, attempts) WHERE status = 'running'",
        float(JOB_LEASE_SECONDS + 60),
        attempts,
    )


@pytest.fixture
def handlers(monkeypatch):
    """Give the worker a private handler table for one test.

    :returns: the dict to register test handlers in
    """
    table: dict = {}
    monkeypatch.setattr(worker_module, "HANDLERS", table)
    return table


# --- queueing, as a person ----------------------------------------------------


async def test_a_queued_job_is_visible_to_its_person_only(household):
    assert await _queue(household["wouter"]) is True
    mine = await _jobs_seen_by(household["wouter"])
    assert [(job["kind"], job["status"]) for job in mine] == [
        (BACKFILL_INDEX_FIELDS, "queued")
    ]
    assert mine[0]["person_id"] == household["wouter"].id
    assert await _jobs_seen_by(household["partner"]) == []


async def test_an_unarmed_connection_sees_no_jobs(household):
    """A forgotten filter returns nothing, here as everywhere else."""
    await _queue(household["wouter"])
    assert await Job.select() == []
    assert await Job.count() == 0


async def test_a_whole_person_job_is_queued_once(household):
    """Asking again while one waits adds nothing and is not an error."""
    assert await _queue(household["wouter"]) is True
    assert await _queue(household["wouter"]) is False
    assert len(await _jobs_seen_by(household["wouter"])) == 1
    # Another person's identical job is unaffected, and so is another kind.
    assert await _queue(household["partner"]) is True
    assert await _queue(household["wouter"], kind="something_else") is True


async def test_jobs_about_specific_things_are_not_deduplicated(household):
    subject = uuid4()
    assert await _queue(household["wouter"], subject_ids=(subject,)) is True
    assert await _queue(household["wouter"], subject_ids=(subject,)) is True
    jobs = await _jobs_seen_by(household["wouter"])
    assert [job["subject_ids"] for job in jobs] == [[subject], [subject]]


async def test_nobody_can_queue_a_job_for_somebody_else(household):
    """The policy checks the new row, so a job cannot be planted on a person."""
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with transaction_scope():
            await arm(principal_for(household["wouter"]))
            await Job.raw(
                "INSERT INTO jobs (id, person_id, kind) VALUES ({}, {}, 'x')",
                uuid4(),
                household["partner"].id,
            )


async def test_nobody_can_change_or_delete_somebody_elses_job(household, seed):
    await _queue(household["partner"])
    async with transaction_scope():
        await arm(principal_for(household["wouter"]))
        changed = await Job.raw("UPDATE jobs SET status = 'done' RETURNING id")
        deleted = await Job.raw("DELETE FROM jobs RETURNING id")
    assert changed == [] and deleted == []
    assert await seed.fetchval("SELECT status FROM jobs") == "queued"


async def test_an_unknown_status_is_refused(household):
    await _queue(household["wouter"])
    with pytest.raises(asyncpg.CheckViolationError):
        async with transaction_scope():
            await arm(principal_for(household["wouter"]))
            await Job.raw("UPDATE jobs SET status = 'paused'")


async def test_deleting_a_person_takes_their_jobs_along(household, graph, seed):
    await _queue(household["partner"])
    await seed.execute(
        "DELETE FROM spaces WHERE owner_person_id = $1", household["partner"].id
    )
    await graph.erase_person(household["partner"])
    assert await seed.fetchval("SELECT count(*) FROM jobs") == 0


# --- the claim: the one function that looks across people ----------------------


async def test_claim_hands_out_one_job_and_only_its_ids(household):
    subject = uuid4()
    await _queue(household["wouter"], subject_ids=(subject,))
    job = await claim(WORKER)
    assert job is not None
    assert job.kind == BACKFILL_INDEX_FIELDS
    assert job.person_id == household["wouter"].id
    assert job.subject_ids == (subject,)
    assert job.attempts == 1
    assert await claim(WORKER) is None
    [row] = await _jobs_seen_by(household["wouter"])
    assert (row["status"], row["locked_by"]) == ("running", WORKER)


async def test_the_claim_function_can_return_nothing_but_ids():
    """Checked on the function's own signature, not on today's body.

    Whatever the body is changed to later, a result column that could hold
    text other than the job's kind has to show up here first.
    """
    [row] = await DB._run_in_new_connection(
        "SELECT pg_get_function_result(p.oid) AS result, prosecdef, proconfig, "
        "pg_get_userbyid(p.proowner) AS owner FROM pg_proc p "
        "WHERE p.proname = 'reef_claim_job'"
    )
    assert row["result"] == (
        "TABLE(job_id uuid, job_kind text, job_person_id uuid, "
        "job_subject_ids uuid[], job_attempts integer)"
    )
    assert row["prosecdef"] is True
    assert "search_path=public, pg_catalog" in row["proconfig"]
    assert row["owner"] in (AUTHZ_ROLE, FORMER_AUTHZ_ROLE)


async def test_the_claim_function_is_closed_to_public():
    granted = await DB._run_in_new_connection(
        "SELECT has_function_privilege('public', 'reef_claim_job(text)', 'EXECUTE') "
        "AS open"
    )
    assert granted[0]["open"] is False


async def test_the_app_role_can_claim_but_cannot_read_the_queue(household, probe):
    """The non-owner role gets a job from the function, and nothing by itself."""
    await _queue(household["wouter"])
    assert await probe.fetchval("SELECT count(*) FROM jobs") == 0
    claimed = await probe.fetch("SELECT * FROM reef_claim_job('probe')")
    assert [row["job_person_id"] for row in claimed] == [household["wouter"].id]
    # Still nothing readable without becoming the person.
    assert await probe.fetchval("SELECT count(*) FROM jobs") == 0


async def test_jobs_are_claimed_oldest_first_across_people(household, seed):
    await _queue(household["partner"])
    await _queue(household["wouter"])
    await seed.execute(
        "UPDATE jobs SET run_after = run_after - interval '1 hour' WHERE person_id = $1",
        household["wouter"].id,
    )
    first, second = await claim(WORKER), await claim(WORKER)
    assert [first.person_id, second.person_id] == [
        household["wouter"].id,
        household["partner"].id,
    ]


async def test_a_delayed_job_is_not_claimed_early(household, seed):
    await _queue(household["wouter"], delay=timedelta(hours=1))
    assert await claim(WORKER) is None
    await seed.execute(
        "UPDATE jobs SET run_after = LOCALTIMESTAMP - interval '1 second'"
    )
    assert await claim(WORKER) is not None


async def test_a_job_whose_worker_vanished_is_claimed_again(household, seed):
    await _queue(household["wouter"])
    first = await claim("worker-a")
    assert await claim("worker-b") is None
    await _expire_lease(seed)
    second = await claim("worker-b")
    assert second.id == first.id
    assert second.attempts == 2
    [row] = await _jobs_seen_by(household["wouter"])
    assert row["locked_by"] == "worker-b"


async def test_a_job_in_hand_is_never_taken_however_old_its_claim(household, seed):
    """A live worker holds a row lock; the lease only matters once it is gone."""
    await _queue(household["wouter"])
    job = await claim("worker-a")
    await _expire_lease(seed)
    holder = await asyncpg.connect(dsn=DB.config["dsn"])
    try:
        transaction = holder.transaction()
        await transaction.start()
        await holder.execute(
            "SELECT set_config('app.person_id', $1, true)", str(job.person_id)
        )
        assert await holder.fetch(
            "SELECT id FROM jobs WHERE id = $1 FOR UPDATE", job.id
        )
        assert await claim("worker-b") is None
        await transaction.rollback()
    finally:
        await holder.close()
    assert (await claim("worker-b")).id == job.id


async def test_a_job_that_keeps_killing_its_worker_is_given_up_on(household, seed):
    await _queue(household["wouter"])
    await claim(WORKER)
    await _expire_lease(seed, attempts=JOB_MAX_ATTEMPTS)
    assert await claim(WORKER) is None
    [row] = await _jobs_seen_by(household["wouter"])
    assert (row["status"], row["error"]) == ("failed", "LeaseExpired")
    assert row["finished_at"] is not None


# --- the worker: everything after the claim happens as the person --------------


async def test_the_worker_runs_a_job_as_its_person(household, handlers):
    """What the handler can see is what the person can see, and no more."""
    wouter, partner = household["wouter"], household["partner"]
    async with transaction_scope():
        await save_engram(
            principal_for(wouter), "personal", "mine.md", "x", message="x"
        )
        await save_engram(
            principal_for(partner), "personal", "theirs.md", "x", message="x"
        )
    seen: list = []

    async def look(principal, job):
        seen.append(principal)
        seen.append(sorted(row["path"] for row in await Engram.select(Engram.path)))

    handlers["look"] = look
    await _queue(wouter, kind="look")
    assert await drain(WORKER) == 1
    assert seen == [principal_for(wouter), ["mine.md"]]
    [row] = await _jobs_seen_by(wouter, kind="look")
    assert (row["status"], row["locked_by"], row["error"]) == ("done", None, None)
    assert row["finished_at"] is not None


async def test_a_failed_job_is_retried_later_and_its_work_rolled_back(
    household, handlers
):
    wouter = household["wouter"]

    async def write_then_fail(principal, job):
        await save_engram(principal, "personal", "half.md", "x", message="x")
        raise RuntimeError(_SECRET)

    handlers["flaky"] = write_then_fail
    await _queue(wouter, kind="flaky")
    job = await claim(WORKER)
    assert await run_job(job, WORKER) == "retry"

    [row] = await _jobs_seen_by(wouter)
    assert (row["status"], row["attempts"], row["locked_by"]) == ("queued", 1, None)
    assert row["run_after"] > row["created_at"] + timedelta(seconds=20)
    async with transaction_scope():
        await arm(principal_for(wouter))
        assert await Engram.count() == 0
    # Not ready yet, so the queue is empty for now.
    assert await claim(WORKER) is None


async def test_a_failure_records_the_error_class_and_never_its_message(
    household, handlers, caplog
):
    """An exception message can quote content. Neither the row nor the log keeps it."""

    async def fail_loudly(principal, job):
        raise RuntimeError(_SECRET)

    handlers["flaky"] = fail_loudly
    await _queue(household["wouter"], kind="flaky")
    with caplog.at_level("INFO", logger="reef.worker"):
        await drain(WORKER)
    [row] = await _jobs_seen_by(household["wouter"])
    assert row["error"] == "RuntimeError"
    assert _SECRET not in caplog.text
    assert household["wouter"].email not in caplog.text
    assert str(household["wouter"].id) not in caplog.text


async def test_a_job_reports_its_outcome_and_error_class_to_telemetry(
    household, handlers, monkeypatch
):
    """The span gets the kind, the outcome and the error's class. No content."""
    reported = []

    @contextmanager
    def recording_span(job_id, kind, attempt):
        result = {}
        yield result
        reported.append((kind, attempt, dict(result)))

    monkeypatch.setattr(worker_module.telemetry, "job_span", recording_span)

    async def fail_loudly(principal, job):
        raise RuntimeError(_SECRET)

    async def succeed(principal, job):
        return None

    handlers["flaky"] = fail_loudly
    handlers["fine"] = succeed
    await _queue(household["wouter"], kind="flaky")
    await _queue(household["wouter"], kind="fine")
    await drain(WORKER)
    assert sorted(reported, key=lambda r: r[0]) == [
        ("fine", 1, {"outcome": "done"}),
        ("flaky", 1, {"outcome": "retry", "error_type": "RuntimeError"}),
    ]
    assert _SECRET not in str(reported)


async def test_a_job_is_given_up_on_after_its_last_attempt(household, handlers, seed):
    async def always_fail(principal, job):
        raise ValueError

    handlers["doomed"] = always_fail
    await _queue(household["wouter"], kind="doomed")
    outcomes = []
    for _ in range(JOB_MAX_ATTEMPTS):
        await seed.execute(
            "UPDATE jobs SET run_after = LOCALTIMESTAMP - interval '1 second'"
        )
        outcomes.append(await run_job(await claim(WORKER), WORKER))
    assert outcomes == ["retry"] * (JOB_MAX_ATTEMPTS - 1) + ["failed"]
    [row] = await _jobs_seen_by(household["wouter"])
    assert (row["status"], row["error"]) == ("failed", "ValueError")
    assert await claim(WORKER) is None


async def test_a_kind_the_worker_does_not_know_is_retried_not_lost(household, handlers):
    """During a deploy the server may queue a kind the old worker lacks."""
    await _queue(household["wouter"], kind="from_the_future")
    assert await run_job(await claim(WORKER), WORKER) == "retry"
    [row] = await _jobs_seen_by(household["wouter"])
    assert (row["status"], row["error"]) == ("queued", "UnknownJobKind")


async def test_a_retry_gives_way_to_the_same_job_queued_meanwhile(household, handlers):
    """Two waiting whole-person jobs of a kind cannot exist; the newer one wins."""
    wouter = household["wouter"]

    async def queue_again_then_fail(principal, job):
        raise RuntimeError

    handlers["flaky"] = queue_again_then_fail
    await _queue(wouter, kind="flaky")
    job = await claim(WORKER)
    assert await _queue(wouter, kind="flaky") is True
    assert await run_job(job, WORKER) == "failed"
    rows = await _jobs_seen_by(wouter)
    assert sorted((row["status"], row["error"]) for row in rows) == [
        ("failed", "Superseded"),
        ("queued", None),
    ]


async def test_a_job_another_worker_took_over_is_dropped(household, handlers, seed):
    ran = []

    async def note(principal, job):
        ran.append(job.id)

    handlers["note"] = note
    await _queue(household["wouter"], kind="note")
    stale = await claim("worker-a")
    await _expire_lease(seed)
    await claim("worker-b")
    assert await run_job(stale, "worker-a") == "dropped"
    assert ran == []
    [row] = await _jobs_seen_by(household["wouter"])
    assert (row["status"], row["locked_by"]) == ("running", "worker-b")


async def test_retries_wait_longer_each_time():
    waits = [retry_delay(attempt).total_seconds() for attempt in range(1, 5)]
    assert waits == [30, 120, 480, 1920]


async def test_serve_stops_when_asked_and_survives_a_broken_claim(
    household, handlers, monkeypatch
):
    """One bad round must not end the process; a stop request must."""
    ran = []
    stop = asyncio.Event()

    async def note(principal, job):
        ran.append(job.id)
        stop.set()

    handlers["note"] = note
    await _queue(household["wouter"], kind="note")
    real_claim, calls = worker_module.claim, []

    async def claim_failing_once(name):
        calls.append(name)
        if len(calls) == 1:
            raise OSError("database went away")
        return await real_claim(name)

    monkeypatch.setattr(worker_module, "claim", claim_failing_once)
    await asyncio.wait_for(serve(WORKER, stop, poll_seconds=0.01), timeout=10)
    assert len(ran) == 1 and len(calls) >= 2


# --- the first real job: index fields, queued by the person's own use ----------


async def _forget_index_fields(seed) -> None:
    """Make every engram look as it did before the index fields existed."""
    await seed.execute(
        "UPDATE engrams SET description = NULL, size = NULL, "
        "reference_targets = NULL, indexed_version = NULL"
    )


async def test_loading_the_index_queues_a_backfill_which_the_worker_carries_out(
    household, seed
):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(me, "personal", "house.md", _SECRET, message="x")
        before = await build_index(me)
    assert await _jobs_seen_by(household["wouter"], BACKFILL_INDEX_FIELDS) == []

    await _forget_index_fields(seed)
    async with transaction_scope():
        assert await build_index(me) == before
    async with transaction_scope():
        await build_index(me)
    assert [
        job["status"]
        for job in await _jobs_seen_by(household["wouter"], BACKFILL_INDEX_FIELDS)
    ] == ["queued"]

    assert await drain(WORKER) == 1
    assert (
        await seed.fetchval(
            "SELECT description FROM engrams WHERE indexed_version = version"
        )
        == _SECRET
    )
    async with transaction_scope():
        assert await build_index(me) == before
    # Nothing left to do, so nothing more is queued.
    assert [
        job["status"]
        for job in await _jobs_seen_by(household["wouter"], BACKFILL_INDEX_FIELDS)
    ] == ["done"]


async def test_a_viewer_does_not_queue_a_backfill_they_could_not_carry_out(
    household, graph, seed
):
    """Otherwise every index load would queue a job that changes nothing."""
    owner = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(owner, "household", "house.md", _SECRET, message="x")
    await _forget_index_fields(seed)
    person = await graph.person("viewer@example.test", "Viewer")
    await graph.personal_space(person, slug="viewer")
    await graph.add_membership(person, household["shared"], "viewer", alias="household")
    async with transaction_scope():
        await build_index(principal_for(person))
    assert await _jobs_seen_by(person) == []
    assert (
        await seed.fetchval(
            "SELECT count(*) FROM jobs WHERE kind = $1", BACKFILL_INDEX_FIELDS
        )
        == 0
    )
