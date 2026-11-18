"""The job queue: background work, always done as the person it is for.

Three rules hold the design together.

**A job names things, it never carries them.** A row holds a kind, a person
and a list of ids. No title, no text, no path. Even the error column stores
only the name of an exception class, because a message can quote what
failed.

**Row security applies here like everywhere else.** A person queues, reads
and settles their own jobs. The single exception is :func:`claim`, which
calls ``reef_claim_job`` -- the one function allowed to look across people
-- and gets back ids, nothing more. See :func:`reef.rls.job_statements`.

**Nothing schedules across people.** There is no "for every person" step
anywhere. A person's own activity queues the work that activity calls for,
which is why :func:`enqueue` is cheap to call again: a whole-person job
that is already waiting is not queued twice.

:func:`enqueue`, :func:`hold` and :func:`complete` assume they run inside
:func:`reef.db.transaction_scope`. :func:`claim` and :func:`fail` must run
outside one; each says why.
"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from asyncpg import UniqueViolationError

from reef.access import Principal, arm
from reef.db import transaction_scope
from reef.models import Job, Person
from reef.rls import JOB_MAX_ATTEMPTS

#: Bring the stored index fields of a person's pages up to date.
BACKFILL_INDEX_FIELDS = "backfill_index_fields"

#: Seconds to wait before the second attempt. Each later attempt waits four
#: times as long as the one before: 30 seconds, 2 minutes, 8, 32.
_FIRST_RETRY_SECONDS = 30


@dataclass(frozen=True)
class ClaimedJob:
    """What a worker is told about the job it has taken: ids, and no more."""

    id: UUID
    kind: str
    person_id: UUID
    subject_ids: tuple[UUID, ...]
    attempts: int


def retry_delay(attempts: int) -> timedelta:
    """Return how long to wait after a failed attempt before the next.

    :param attempts: how many attempts have been made so far, at least one
    :returns: the wait before the next attempt
    """
    return timedelta(seconds=_FIRST_RETRY_SECONDS * 4 ** (attempts - 1))


async def enqueue(
    principal: Principal,
    kind: str,
    subject_ids: tuple[UUID, ...] = (),
    *,
    delay: timedelta = timedelta(0),
) -> bool:
    """Queue a job for the principal.

    With no ``subject_ids`` the job covers everything of this person's, and
    at most one such job per kind waits at a time. Queueing another is not
    an error; it simply reports that nothing was added.

    :param principal: the person the work is for, and who it will run as
    :param kind: what to do
    :param subject_ids: what the job is about, by id; empty for "everything"
    :param delay: how long to wait before the job may start
    :returns: True if a job was added, False if one was already waiting
    """
    await arm(principal)
    added = await Job.raw(
        "INSERT INTO jobs (id, person_id, kind, subject_ids, run_after) "
        "VALUES ({}, {}, {}, {}::uuid[], "
        "LOCALTIMESTAMP + make_interval(secs => {})) "
        "ON CONFLICT DO NOTHING RETURNING id",
        uuid4(),
        principal.person_id,
        kind,
        [str(subject_id) for subject_id in subject_ids],
        delay.total_seconds(),
    )
    return bool(added)


async def claim(worker: str) -> ClaimedJob | None:
    """Take the next job that is ready, for any person.

    The one call here that is not made as a person, and so the one that
    must **not** run inside an armed transaction: it commits on its own, so
    the claim stands even if the work that follows fails.

    :param worker: a name for this worker, recorded on the job
    :returns: the claimed job, or None when nothing is ready
    """
    rows = await Job.raw("SELECT * FROM reef_claim_job({})", worker)
    if not rows:
        return None
    row = rows[0]
    return ClaimedJob(
        id=row["job_id"],
        kind=row["job_kind"],
        person_id=row["job_person_id"],
        subject_ids=tuple(row["job_subject_ids"]),
        attempts=row["job_attempts"],
    )


async def hold(job: ClaimedJob, worker: str) -> Principal | None:
    """Become the job's person and lock the job for this transaction.

    The lock is what tells other workers the job is in hand: the claim
    skips locked rows, however long ago the job was taken. It is released
    when the transaction ends, by commit, rollback or a dead connection.

    :param job: the job this worker claimed
    :param worker: the name the job was claimed under
    :returns: the person to act as, or None if the job is no longer this
        worker's to do -- its person is gone, or another worker has it
    """
    await Job.raw("SELECT set_config('app.person_id', {}, true)", str(job.person_id))
    person = await Person.objects().where(Person.id == job.person_id).first()
    if person is None:
        return None
    held = await Job.raw(
        "SELECT id FROM jobs WHERE id = {} AND status = 'running' "
        "AND locked_by = {} FOR UPDATE",
        job.id,
        worker,
    )
    if not held:
        return None
    return Principal(person_id=person.id, email=person.email)


async def complete(job: ClaimedJob) -> None:
    """Mark a held job done. Call in the transaction that did the work.

    Sharing the transaction is the point: the work and the record that it
    was done commit together or not at all.

    :param job: the job that was held with :func:`hold`
    """
    await Job.raw(
        "UPDATE jobs SET status = 'done', error = NULL, locked_by = NULL, "
        "locked_at = NULL, finished_at = LOCALTIMESTAMP WHERE id = {}",
        job.id,
    )


async def _settle_failure(
    job: ClaimedJob, worker: str, name: str, *, retry: bool
) -> None:
    """Write the outcome of a failed attempt, in a transaction of its own.

    :param job: the job whose attempt failed
    :param worker: the name the job was claimed under
    :param name: the error name to store
    :param retry: queue the job again rather than giving up on it
    """
    async with transaction_scope():
        await Job.raw(
            "SELECT set_config('app.person_id', {}, true)", str(job.person_id)
        )
        if retry:
            await Job.raw(
                "UPDATE jobs SET status = 'queued', error = {}, "
                "locked_by = NULL, locked_at = NULL, "
                "run_after = LOCALTIMESTAMP + make_interval(secs => {}) "
                "WHERE id = {} AND status = 'running' AND locked_by = {}",
                name,
                retry_delay(job.attempts).total_seconds(),
                job.id,
                worker,
            )
        else:
            await Job.raw(
                "UPDATE jobs SET status = 'failed', error = {}, "
                "locked_by = NULL, locked_at = NULL, "
                "finished_at = LOCALTIMESTAMP "
                "WHERE id = {} AND status = 'running' AND locked_by = {}",
                name,
                job.id,
                worker,
            )


async def fail(job: ClaimedJob, worker: str, error: BaseException) -> bool:
    """Record a failed attempt, and queue another unless the job is spent.

    Call this **outside** any transaction, after the one that did the work
    has rolled back: it opens its own, so the record survives the failure.

    Only the exception's class name is stored. Its message is not: it can
    quote the content the job was working on.

    :param job: the job whose attempt failed
    :param worker: the name the job was claimed under
    :param error: what went wrong
    :returns: True if the job will be tried again
    """
    name = type(error).__name__[:255]
    if job.attempts < JOB_MAX_ATTEMPTS:
        try:
            await _settle_failure(job, worker, name, retry=True)
            return True
        except UniqueViolationError:
            # The same whole-person job was queued again while this one ran.
            # That one will do the work, so this one has nothing left to try.
            name = "Superseded"
    await _settle_failure(job, worker, name, retry=False)
    return False
