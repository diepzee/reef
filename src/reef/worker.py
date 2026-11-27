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
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import timedelta

from reef import calllog, embedding, filetext, searchlog, telemetry, write_tokens
from reef.access import Principal
from reef.config import get_settings
from reef.context import backfill_index_fields
from reef.db import DB, transaction_scope
from reef.jobs import (
    BACKFILL_EMBEDDINGS,
    BACKFILL_FILES,
    BACKFILL_INDEX_FIELDS,
    BACKFILL_LINKS,
    EMBED_ENGRAM,
    EMBED_FILE,
    READ_FILE,
    REFRESH_ACTIVATION,
    ClaimedJob,
    claim,
    complete,
    fail,
    hold,
)
from reef.links import backfill_links
from reef.models import TextStatus
from reef.reading import TEXT_FORMATS, sandbox
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


async def _embed_file(principal: Principal, job: ClaimedJob) -> None:
    """Embed the chunks of the job's files.

    Does nothing with the reading switch off or no embedding service: either
    was switched off after the job was queued. Retries as
    :func:`_embed_engram` does, and logs only the class of a failure.

    :param principal: the person the job runs as
    :param job: the claimed job
    """
    embedder = embedding.get_embedder()
    if embedder is None or not get_settings().reading.enabled:
        return
    retry = get_settings().embedding
    for attachment_id in job.subject_ids:
        try:
            await embedding.embed_file(principal, attachment_id, embedder)
        except embedding.CapReached:
            await embedding.request_file(
                principal,
                attachment_id,
                delay=timedelta(seconds=retry.cap_retry_seconds),
            )
        except embedding.ServiceFailed as failure:
            log.warning("embedding: the service failed on a file (%s)", failure)
            await embedding.request_file(
                principal,
                attachment_id,
                delay=timedelta(seconds=retry.failure_retry_seconds),
            )


async def _read_file(principal: Principal, job: ClaimedJob) -> None:
    """Read the next pages of the job's files, and queue what is left.

    Does nothing with the reading switch off: it was switched off after the
    job was queued. A file with pages left is queued again at once; one
    whose person has spent today's pages, after ``reading.retry_seconds``.
    Either way the job ends normally, so the pages read and their cost are
    committed.

    Logs one line per file: pages read by text layer, by OCR and by text
    format, the outcome and the time. Never a name, a description, a text,
    a space or the person.

    Before and after the job, however it ends, every process of the
    ``reader`` user is killed: see
    :func:`reef.reading.sandbox.kill_reader_processes`. This worker runs one
    job at a time, so no read is in flight then. A sweep that fails before
    the read fails the job, so no file is read beside a process an earlier
    read left; the job is retried. One that fails after the read is only
    logged (see :func:`_sweep_after_read`).

    :param principal: the person the job runs as
    :param job: the claimed job
    """
    settings = get_settings().reading
    if not settings.enabled:
        return
    _sweep_reader()
    try:
        await _read_files(principal, job, settings.retry_seconds)
    finally:
        _sweep_after_read()


def _sweep_reader() -> None:
    """Kill what earlier reads left running as ``reader``, and log how many.

    :raises OSError: when ``/proc`` cannot be read or a process not signalled
    """
    left = sandbox.kill_reader_processes()
    if left:
        log.warning("read_file: killed %d processes left by earlier reads", left)


def _sweep_after_read() -> None:
    """Sweep as :func:`_sweep_reader` does once the read has ended, and never raise.

    An error here must not take the place of the read's own outcome: pages
    read and paid for would be rolled back, or a failed read retried under
    the sweep's error. Only the error's class is logged. The next reading
    job sweeps before it reads, and fails if that sweep fails too.
    """
    try:
        _sweep_reader()
    # Broad on purpose: whatever the sweep raises, the read's outcome stands.
    except Exception as error:  # noqa: BLE001
        log.error(
            "read_file: the sweep after the read failed (%s)", type(error).__name__
        )


async def _read_files(
    principal: Principal, job: ClaimedJob, retry_seconds: float
) -> None:
    """Read the job's files, one after the other. See :func:`_read_file`.

    :param principal: the person the job runs as
    :param job: the claimed job
    :param retry_seconds: how long a file waits when the day's pages are spent
    """
    for attachment_id in job.subject_ids:
        started = time.monotonic()
        counts: Counter[str] = Counter()
        try:
            status = await filetext.read_file(principal, attachment_id, counts=counts)
        except filetext.AllowanceSpent:
            await filetext.request_read(
                principal, attachment_id, delay=timedelta(seconds=retry_seconds)
            )
            outcome = "allowance spent"
        else:
            if status is TextStatus.WAITING:
                await filetext.request_read(principal, attachment_id)
            outcome = "skipped" if status is None else status.value
        log.info(
            "read_file: %d pages (layer %d, ocr %d, format %d) %s in %.1f s",
            sum(counts.values()),
            counts["pdf-text"],
            counts["tesseract"],
            sum(counts[kind.value] for kind in TEXT_FORMATS),
            outcome,
            time.monotonic() - started,
        )


async def _backfill_files(principal: Principal, job: ClaimedJob) -> None:
    """Queue reading and embedding for each of the person's files that needs it.

    Does nothing with the reading switch off.

    :param principal: the person the job runs as
    :param job: the claimed job
    """
    await filetext.backfill(principal)


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
    EMBED_FILE: _embed_file,
    BACKFILL_EMBEDDINGS: _backfill_embeddings,
    READ_FILE: _read_file,
    BACKFILL_FILES: _backfill_files,
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


#: How often the worker removes expired search logs, call logs and used write
#: tokens. Logs stay for 90 days as the privacy page promises; token records
#: stay for one day, well beyond their ten-minute lifetime.
PURGE_EVERY_SECONDS = 3600.0


async def purge_old_logs() -> int:
    """Delete old search logs, call logs and used write tokens.

    Not a job: a job is done for one person, and this is for everybody. It
    reads no row and needs no principal. See :func:`reef.searchlog.purge`,
    :func:`reef.calllog.purge` and :func:`reef.write_tokens.purge`.

    Each purge has its own transaction, so one that fails does
    not keep the others' rows past their time. A failure is logged by its
    class only, and tried again at the next purge.

    :returns: deleted search and call log rows; token purges return no count
    """
    searches = await _purge("search log", searchlog.purge)
    calls = await _purge("call log", calllog.purge)
    await _purge("used write tokens", write_tokens.purge)
    return searches + calls


async def _purge(log_name: str, purge: Callable[[], Awaitable[int | None]]) -> int:
    """Purge one kind of record in its own transaction, and never raise.

    :param log_name: the log, as the app log names it
    :param purge: the log's purge, such as :func:`reef.searchlog.purge`
    :returns: how many rows were deleted; 0 when unreported or failed
    """
    try:
        async with transaction_scope():
            removed = await purge()
    # Broad on purpose: one purge's failure must not keep the others' rows.
    except Exception as error:  # noqa: BLE001
        log.error("%s: old rows were not deleted (%s)", log_name, type(error).__name__)
        return 0
    if removed:
        log.info("%s: %d old rows deleted", log_name, removed)
    return removed or 0


async def serve(worker: str, stop: asyncio.Event, poll_seconds: float) -> None:
    """Drain the queue, wait, and repeat until asked to stop.

    About once an hour it also deletes expired search logs, call logs and
    used write tokens, each in its own transaction (see :func:`purge_old_logs`).
    Only log-row counts are recorded; token purges return no count.

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
                await purge_old_logs()
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
    """Adopt orphans, start the pool, serve until SIGTERM or SIGINT, and close the pool.

    A root worker on Linux adopts the orphans of the processes it starts, so
    it can collect their zombies: under ``uv run``, PID 1 never does, and
    each one counts against the reading child's cap on processes (see
    :func:`reef.reading.sandbox.adopt_orphans`). A refusal is logged by its
    class, and the worker serves anyway: reads then run out of processes
    and are retried, which the log shows.
    """
    try:
        sandbox.adopt_orphans()
    except OSError as error:
        log.error(
            "worker: orphaned processes are not adopted (%s)", type(error).__name__
        )
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
