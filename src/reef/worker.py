"""The worker: a separate process that does queued work, one job at a time.

It holds the same database credential as the server and no other. It cannot
read anything on its own: for each job it becomes the job's person, exactly
as a request does, and row security decides what it may touch.

Run as: ``python -m reef.worker``
"""

import asyncio
import logging
import os
import signal
import socket
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta

from reef import calllog, embedding, searchlog, telemetry
from reef.access import Principal
from reef.config import get_settings
from reef.context import backfill_index_fields
from reef.db import DB, transaction_scope
from reef.jobs import (
    BACKFILL_EMBEDDINGS,
    BACKFILL_INDEX_FIELDS,
    BACKFILL_LINKS,
    EMBED_ENGRAM,
    REFRESH_ACTIVATION,
    ClaimedJob,
    claim,
    complete,
    fail,
    hold,
)
from reef.links import backfill_links
from reef.usage import refresh_activation

log = logging.getLogger("reef.worker")

Handler = Callable[[Principal, ClaimedJob], Awaitable[None]]


class UnknownJobKind(Exception):
    """Raised for a job this worker has no handler for.

    Treated like any other failure, so it is retried: during a deploy the
    server can queue a kind that the old worker has not learned yet.
    """


async def _backfill_index_fields(principal: Principal, job: ClaimedJob) -> None:
    """Store current index fields on the person's engrams.

    :param principal: the person the job runs as
    :param job: the claimed job
    """
    await backfill_index_fields(principal)


async def _backfill_links(principal: Principal, job: ClaimedJob) -> None:
    """Store the wiki links of the person's engrams.

    :param principal: the person the job runs as
    :param job: the claimed job
    """
    await backfill_links(principal)


async def _refresh_activation(principal: Principal, job: ClaimedJob) -> None:
    """Recompute the person's activation from their usage log.

    :param principal: the person the job runs as
    :param job: the claimed job
    """
    await refresh_activation(principal)


async def _embed_engram(principal: Principal, job: ClaimedJob) -> None:
    """Embed the changed pieces of the job's engrams.

    With no embedding service the job is done and nothing happened: the
    service was switched off after the job was queued.

    An engram that does not fit in today's cap, or that the service failed
    on, is queued again for later and the job ends normally. It must: the
    pieces sent are charged in this transaction, and a job that raises is
    rolled back. Only the class of a failure is logged.

    :param principal: the person the job runs as
    :param job: the claimed job
    """
    embedder = embedding.get_embedder()
    if embedder is None:
        return
    retry = get_settings().embedding
    for engram_id in job.subject_ids:
        try:
            await embedding.embed_engram(principal, engram_id, embedder)
        except embedding.CapReached:
            await embedding.request(
                principal, engram_id, delay=timedelta(seconds=retry.cap_retry_seconds)
            )
        except embedding.ServiceFailed as failure:
            log.warning("embedding: the service failed on an engram (%s)", failure)
            await embedding.request(
                principal,
                engram_id,
                delay=timedelta(seconds=retry.failure_retry_seconds),
            )


async def _backfill_embeddings(principal: Principal, job: ClaimedJob) -> None:
    """Queue a job for each of the person's engrams that has no vectors.

    :param principal: the person the job runs as
    :param job: the claimed job
    """
    if embedding.get_embedder() is not None:
        await embedding.backfill(principal)


#: What this worker knows how to do, by job kind.
#:
#: A handler acts as the principal it is given and nobody else. It must never
#: arm a different person: the binding lasts for the rest of the transaction,
#: so the job would then be settled as the wrong person.
HANDLERS: dict[str, Handler] = {
    BACKFILL_INDEX_FIELDS: _backfill_index_fields,
    BACKFILL_LINKS: _backfill_links,
    REFRESH_ACTIVATION: _refresh_activation,
    EMBED_ENGRAM: _embed_engram,
    BACKFILL_EMBEDDINGS: _backfill_embeddings,
}


def worker_name() -> str:
    """Return a name that tells this worker process from any other.

    Kept within the width of ``jobs.locked_by``. The claim stores the name
    and every later step matches on it, so a name the column would cut short
    would never match again and each job would look taken by someone else.

    :returns: host name and process id, at most 255 characters
    """
    return f"{socket.gethostname()[:200]}:{os.getpid()}"


async def run_job(job: ClaimedJob, worker: str) -> str:
    """Do one claimed job, and record how it went.

    The work and the "done" mark share one transaction, so a job is never
    marked done for work that was rolled back. A failure is recorded in a
    second transaction, after the first has rolled back.

    Logs and the telemetry span carry the job's id, kind and outcome, and
    the *class* of any error. Never the error's message, and never the
    person.

    :param job: the job this worker claimed
    :param worker: the name the job was claimed under
    :returns: ``done``, ``retry``, ``failed``, or ``dropped`` when the job
        was no longer this worker's to do
    """
    with telemetry.job_span(job.id, job.kind, job.attempts) as result:
        try:
            async with transaction_scope():
                principal = await hold(job, worker)
                if principal is None:
                    log.info("job %s (%s) dropped", job.id, job.kind)
                    result["outcome"] = "dropped"
                    return "dropped"
                handler = HANDLERS.get(job.kind)
                if handler is None:
                    raise UnknownJobKind(job.kind)
                await handler(principal, job)
                await complete(job)
        # Broad on purpose: whatever a handler raises, the queue must hear of it.
        except Exception as error:  # noqa: BLE001
            retrying = await fail(job, worker, error)
            outcome = "retry" if retrying else "failed"
            log.warning(
                "job %s (%s) attempt %d: %s, %s",
                job.id,
                job.kind,
                job.attempts,
                type(error).__name__,
                outcome,
            )
            result["outcome"] = outcome
            result["error_type"] = type(error).__name__
            return outcome
        log.info("job %s (%s) done", job.id, job.kind)
        result["outcome"] = "done"
        return "done"


async def drain(worker: str) -> int:
    """Do every job that is ready now, then return.

    :param worker: the name to claim jobs under
    :returns: how many jobs were attempted
    """
    attempted = 0
    while (job := await claim(worker)) is not None:
        await run_job(job, worker)
        attempted += 1
    return attempted


#: How often the worker deletes search log rows that are past their time.
#: A constant, not a setting: the privacy page promises deletion after 90
#: days, and only this keeps the promise on time.
PURGE_EVERY_SECONDS = 3600.0


async def purge_search_log() -> int:
    """Delete logged searches and calls that are older than they may be kept.

    Not a job: a job is done for one person, and this is for everybody. It
    reads no row and needs no principal. See :func:`reef.searchlog.purge` and
    :func:`reef.calllog.purge`.

    :returns: how many rows were deleted, both logs together
    """
    async with transaction_scope():
        searches = await searchlog.purge()
        calls = await calllog.purge()
    if searches:
        log.info("search log: %d old rows deleted", searches)
    if calls:
        log.info("call log: %d old rows deleted", calls)
    return searches + calls


async def serve(worker: str, stop: asyncio.Event, poll_seconds: float) -> None:
    """Drain the queue, wait, and repeat until asked to stop.

    About once an hour it also deletes old search and call log rows. A count is
    logged, never a query.

    A stop request is honoured between jobs, never during one: the job in
    hand is finished first.

    A failure to reach the database is logged and waited out rather than
    allowed to end the process. The queue is in the database, so there is
    nothing to lose by trying again.

    :param worker: the name to claim jobs under
    :param stop: set to end the loop
    :param poll_seconds: how long to wait when the queue is empty
    """
    next_purge = 0.0
    while not stop.is_set():
        try:
            if time.monotonic() >= next_purge:
                # Set first, so a purge that fails is tried again in an hour
                # and not on every pass.
                next_purge = time.monotonic() + PURGE_EVERY_SECONDS
                await purge_search_log()
            while not stop.is_set() and (job := await claim(worker)) is not None:
                await run_job(job, worker)
        # Broad on purpose: no error may end the loop. See the docstring.
        except Exception as error:  # noqa: BLE001
            log.error("worker loop: %s", type(error).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            pass


async def _main() -> None:
    """Start the pool, serve until SIGTERM or SIGINT, and close the pool."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    worker = worker_name()
    await DB.start_connection_pool()
    log.info("worker %s started", worker)
    try:
        await serve(worker, stop, get_settings().worker.poll_seconds)
    finally:
        await DB.close_connection_pool()
        log.info("worker %s stopped", worker)


def main() -> None:
    """Run the worker until it is told to stop."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    # httpx logs every request with its full address at INFO. The embedding
    # service's address is configuration and has no place in the log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    telemetry.configure()
    telemetry.forward_logs(log)
    asyncio.run(_main())


if __name__ == "__main__":
    main()
