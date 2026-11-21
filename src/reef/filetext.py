"""Read the text inside stored files, page by page, and keep it.

**When.** A file is queued for reading when it is stored
(:func:`queue_reading`, called by :func:`reef.attachments.add_attachment`).
Files stored before are queued by a backfill that the person's own index
load asks for (:func:`request_backfill`, :func:`backfill`). With the switch
off (``REEF_FILE_TEXT``) nothing is queued, read or kept.

**How.** A ``read_file`` job reads one file. A text format is read whole, in
one child process. A PDF or an image is read ``reading.pages_per_job`` pages
at a time, at most ``reading.parallel_pages`` at once, and the job then
queues itself for the rest. No job carries a page offset: what is left is
whatever ``file_pages`` does not hold yet, so a retried or duplicate job
never stores a page twice. Every file is read in a child process
(:mod:`reef.reading.local`); nothing here parses a file.

**Whose.** A job runs as the person whose act queued it, and touches only
ready files in spaces that person may write to. A viewer, a former member,
or anyone else reads nothing, and is told nothing about why.

**What it costs.** Each page read is charged to the person's day in
``reading_spend``, by reader. Once ``reading.daily_pages`` is spent,
:class:`AllowanceSpent` asks the job to try again later.

**How it ends.** ``read``, ``partly_read`` past ``reading.pages_per_file``,
``unreadable`` when the file cannot be read, with the error's class in
``text_error``, or ``not_read`` for a kind Reef does not read. Each asks for
the file's vectors (:func:`reef.embedding.request_file`), so every file is
found by meaning, read or not.

**What is logged.** Nothing here. The worker logs counts and the outcome,
never a filename, a description, a text, a space or a person.

Everything here assumes it runs inside :func:`reef.db.transaction_scope`.
"""

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from reef import embedding
from reef.access import Principal, arm
from reef.attachments import ObjectStore, S3ObjectStore
from reef.config import ReadingSettings, get_settings
from reef.jobs import BACKFILL_FILES, READ_FILE, enqueue
from reef.models import Attachment, FilePage, Job, ReadingSpend, TextStatus
from reef.reading import (
    READER_NAMES,
    TEXT_FORMATS,
    FileKind,
    NoMainPart,
    ReadFailed,
    Reading,
    ReadTimedOut,
    ScanReader,
    TooLarge,
    local,
)
from reef.reading.kinds import HEAD_BYTES, likely_kind

FINAL: frozenset[TextStatus] = frozenset(
    {
        TextStatus.READ,
        TextStatus.PARTLY_READ,
        TextStatus.UNREADABLE,
        TextStatus.NOT_READ,
    }
)
"""Where reading ends. A file in one of these is not read again."""

_WRITABLE = "space_id IN (SELECT reef_member_space_ids())"
"""The file is in a space the person may write to: a member's, not a
viewer's. A fixed literal, spliced into SQL."""


@dataclass(frozen=True)
class StoredPage:
    """One stored page of a file, as it is shown and searched."""

    number: int
    text: str
    reader: str


class AllowanceSpent(Exception):
    """Raised when the person's pages for today are spent.

    Nothing was read. The job asks for the file again after
    ``reading.retry_seconds`` and ends normally.
    """


@dataclass(frozen=True)
class _Page:
    """One page as read, before it is stored.

    ``confidence`` runs from 0 to 100, as ``file_pages.confidence`` does.
    """

    number: int
    text: str
    reader: str
    model: str | None = None
    confidence: float | None = None


async def queue_reading(
    principal: Principal, attachment: Attachment, head: bytes
) -> TextStatus | None:
    """Mark a newly stored file for reading, or as not read, and queue the work.

    Called by :func:`reef.attachments.add_attachment` in the transaction that
    makes the file ready, so the file and its job commit together. Sets
    ``attachment.text_status`` too.

    :param principal: the person who stored the file
    :param attachment: the file, ready
    :param head: the file's first bytes, which tell its kind
    :returns: ``waiting`` with a ``read_file`` job for a kind Reef reads;
        ``not_read`` with an embedding request for any other; None with
        the switch off, when nothing is changed or queued
    """
    if not get_settings().reading.enabled:
        return None
    await arm(principal)
    readable = likely_kind(head, attachment.filename or "") is not FileKind.OTHER
    status = TextStatus.WAITING if readable else TextStatus.NOT_READ
    await _mark(attachment.id, status)
    attachment.text_status = status.value
    if readable:
        await request_read(principal, attachment.id)
    else:
        await embedding.request_file(principal, attachment.id)
    return status


async def request_read(
    principal: Principal, attachment_id: UUID, delay: timedelta = timedelta(0)
) -> bool:
    """Ask for a file to be read, unless a job of this person's already waits.

    :param principal: the person the job runs as
    :param attachment_id: the file
    :param delay: how long to wait before the job may start
    :returns: True if a job was added; False with the switch off, or when
        one waits already
    """
    if not get_settings().reading.enabled:
        return False
    await arm(principal)
    waiting = await Job.raw(
        "SELECT 1 FROM jobs WHERE person_id = {} AND kind = {} "
        "AND status = 'queued' AND subject_ids = ARRAY[{}]::uuid[]",
        principal.person_id,
        READ_FILE,
        str(attachment_id),
    )
    if waiting:
        return False
    return await enqueue(principal, READ_FILE, (attachment_id,), delay=delay)


async def spent_pages_today(principal: Principal) -> int:
    """Return how many pages were read for the person today, by every reader.

    :param principal: the person
    :returns: the count, zero if none
    """
    await arm(principal)
    rows = await ReadingSpend.raw(
        "SELECT COALESCE(SUM(pages), 0)::int AS pages FROM reading_spend "
        "WHERE person_id = {} AND day = CURRENT_DATE",
        principal.person_id,
    )
    return rows[0]["pages"]


async def read_file(
    principal: Principal,
    attachment_id: UUID,
    store: ObjectStore | None = None,
    *,
    counts: Counter[str] | None = None,
) -> TextStatus | None:
    """Read the next pages of one file and keep them.

    A text format is read whole. A PDF or an image has its next
    ``reading.pages_per_job`` pages read, those not in ``file_pages`` yet, up
    to ``reading.pages_per_file`` and to what is left of the day's
    allowance. A page refused as too large is kept empty, and reading goes
    on. Any other failure keeps the pages read so far and ends reading:
    the file is ``unreadable``, with the failure's class in ``text_error``.

    :param principal: the person the job runs as
    :param attachment_id: the file
    :param store: where the bytes are; the object store by default
    :param counts: when given, how many pages each reader read in this call
        is added to it
    :returns: ``waiting`` when pages are left for another job; else where
        reading ended, also when it had ended before this call. None with
        the switch off, or for a file that is gone, not ready, or not in a
        space the person may write to.
    :raises AllowanceSpent: when the person's pages for today are spent;
        nothing was read
    :raises SandboxUnavailable: when no child can be started safely; the
        job fails and the queue tries it again
    """
    settings = get_settings().reading
    if not settings.enabled:
        return None
    await arm(principal)
    rows = await Attachment.raw(
        "SELECT object_key, filename, text_status FROM attachments "
        f"WHERE id = {{}} AND status = 'ready' AND {_WRITABLE}",
        attachment_id,
    )
    if not rows:
        return None
    file = rows[0]
    if file["text_status"] in FINAL:
        return TextStatus(file["text_status"])
    left = settings.daily_pages - await spent_pages_today(principal)
    if left <= 0:
        raise AllowanceSpent
    data = await (store or S3ObjectStore()).get(file["object_key"])
    kind = likely_kind(data[:HEAD_BYTES], file["filename"] or "")
    tally = counts if counts is not None else Counter()
    try:
        if kind is FileKind.OTHER:
            return await _finish(principal, attachment_id, TextStatus.NOT_READ)
        if kind in TEXT_FORMATS:
            return await _read_whole(principal, attachment_id, data, kind, tally)
        return await _read_paged(principal, attachment_id, data, kind, left, tally)
    except NoMainPart:
        return await _finish(principal, attachment_id, TextStatus.NOT_READ)
    except ReadFailed as failure:
        return await _finish(
            principal,
            attachment_id,
            TextStatus.UNREADABLE,
            error=type(failure).__name__,
        )


async def _read_whole(
    principal: Principal,
    attachment_id: UUID,
    data: bytes,
    kind: FileKind,
    tally: Counter[str],
) -> TextStatus:
    """Read a text format whole, keep its pages and end reading.

    :param principal: the person the job runs as
    :param attachment_id: the file
    :param data: its bytes
    :param kind: one of :data:`reef.reading.TEXT_FORMATS`
    :param tally: pages read, by reader, added to
    :returns: ``read``, or ``partly_read`` when it has more pages than are read
    :raises ReadFailed: when the file cannot be read
    """
    limit = get_settings().reading.pages_per_file
    parts, total = await local.read_parts(data, kind)
    pages = [
        _Page(number, part, kind.value)
        for number, part in enumerate(parts[:limit], start=1)
    ]
    await _keep(principal, attachment_id, pages, tally)
    status = TextStatus.PARTLY_READ if total > limit else TextStatus.READ
    return await _finish(principal, attachment_id, status, pages=total)


async def _read_paged(
    principal: Principal,
    attachment_id: UUID,
    data: bytes,
    kind: FileKind,
    left: int,
    tally: Counter[str],
) -> TextStatus:
    """Read the next pages of a PDF or an image, and keep them.

    :param principal: the person the job runs as
    :param attachment_id: the file
    :param data: its bytes
    :param kind: ``PDF`` or ``IMAGE``
    :param left: how many pages the person may still read today
    :param tally: pages read, by reader, added to
    :returns: ``waiting`` when pages are left; else ``read``, or
        ``partly_read`` when the file has more pages than are read
    :raises ReadFailed: when the file, or a page of it, cannot be read. The
        pages read before are kept.
    """
    settings = get_settings().reading
    total = await local.survey(data, kind)
    limit = min(total, settings.pages_per_file)
    have = await _numbers(attachment_id)
    wanted = [number for number in range(1, limit + 1) if number not in have]
    todo = wanted[: min(settings.pages_per_job, left)]
    pages, failure = await _read_pages(data, kind, todo, settings)
    await _keep(principal, attachment_id, pages, tally)
    if failure is not None:
        raise failure
    if len(wanted) > len(todo):
        await _mark(attachment_id, TextStatus.WAITING)
        return TextStatus.WAITING
    status = (
        TextStatus.PARTLY_READ if total > settings.pages_per_file else TextStatus.READ
    )
    return await _finish(principal, attachment_id, status, pages=total)


async def _read_pages(
    data: bytes, kind: FileKind, numbers: list[int], settings: ReadingSettings
) -> tuple[list[_Page], ReadFailed | None]:
    """Read pages, ``reading.parallel_pages`` at a time.

    Each running read holds one page's image, and lets go of it once the
    page is read: what comes back is text. After a failure no further page
    is started; those running finish.

    :param data: the file's bytes
    :param kind: ``PDF`` or ``IMAGE``
    :param numbers: the pages to read
    :param settings: the reading settings
    :returns: the pages read, a page refused as too large among them with
        empty text; and the failure of the first page that failed otherwise,
        or None
    :raises SandboxUnavailable: when no child can be started safely; the
        reads still running are stopped
    """
    gate = asyncio.Semaphore(settings.parallel_pages)
    reader = local.TesseractReader()
    failures: dict[int, ReadFailed] = {}

    async def one(number: int) -> _Page | None:
        async with gate:
            if failures:
                return None
            try:
                return await _read_page(data, kind, number, reader, settings)
            except TooLarge:
                # Too large to read even at the lowest resolution: kept
                # empty, as read by the reader that would have read it.
                return _Page(number, "", reader.name)
            except ReadFailed as failure:
                failures[number] = failure
                return None

    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(one(number)) for number in numbers]
    except BaseExceptionGroup as group:
        # A failure that is not the file's, such as a missing sandbox. The
        # job must fail with it, by its own class, so the queue retries.
        raise group.exceptions[0] from None
    pages = [page for task in tasks if (page := task.result()) is not None]
    return pages, failures[min(failures)] if failures else None


async def _read_page(
    data: bytes,
    kind: FileKind,
    number: int,
    reader: ScanReader,
    settings: ReadingSettings,
) -> _Page:
    """Read one page: from its text layer, or as a scan.

    A scan page takes two child calls, one to render it and one to read it.
    Together they get ``reading.page_seconds``, the time one page may take;
    a child still running when it is up is stopped.

    :param data: the file's bytes
    :param kind: ``PDF`` or ``IMAGE``
    :param number: the page, from 1
    :param reader: reads a scan page
    :param settings: the reading settings
    :returns: the page as read
    :raises ReadTimedOut: when the page takes longer than it may
    :raises ReadFailed: when the page cannot be read
    """
    try:
        async with asyncio.timeout(settings.page_seconds):
            opened = await local.open_page(data, kind, number, dpi=settings.dpi)
            if isinstance(opened, Reading):
                reading = opened
            else:
                reading = await reader.read(opened)
    except TimeoutError:
        raise ReadTimedOut from None
    confidence = reading.confidence
    return _Page(
        number,
        reading.text,
        reading.reader,
        reading.model or None,
        None if confidence is None else min(max(confidence, 0.0), 1.0) * 100,
    )


async def _numbers(attachment_id: UUID) -> set[int]:
    """Return the numbers of the pages a file has stored.

    :param attachment_id: the file
    :returns: the page numbers
    """
    rows = await FilePage.raw(
        "SELECT number FROM file_pages WHERE attachment_id = {}", attachment_id
    )
    return {row["number"] for row in rows}


async def _keep(
    principal: Principal,
    attachment_id: UUID,
    pages: list[_Page],
    tally: Counter[str],
) -> None:
    """Store pages that are not stored yet, and charge them to the person.

    A page another job stored meanwhile is left as it is. Each text is cut
    to ``reading.page_chars`` and made fit to store (:func:`_storable`).

    :param principal: the person the job runs as
    :param attachment_id: the file
    :param pages: the pages read
    :param tally: pages read, by reader, added to
    """
    chars = get_settings().reading.page_chars
    for page in sorted(pages, key=lambda page: page.number):
        await FilePage.raw(
            "INSERT INTO file_pages "
            "(id, attachment_id, number, text, reader, model, confidence) "
            "VALUES ({}, {}, {}, {}, {}, {}, {}) "
            "ON CONFLICT (attachment_id, number) DO NOTHING",
            uuid4(),
            attachment_id,
            page.number,
            _storable(page.text)[:chars],
            page.reader,
            page.model,
            page.confidence,
        )
    read = Counter(page.reader for page in pages)
    tally.update(read)
    for reader, count in read.items():
        await ReadingSpend.raw(
            "INSERT INTO reading_spend (id, person_id, day, reader, pages) "
            "VALUES ({}, {}, CURRENT_DATE, {}, {}) "
            "ON CONFLICT (person_id, day, reader) "
            "DO UPDATE SET pages = reading_spend.pages + EXCLUDED.pages",
            uuid4(),
            principal.person_id,
            reader,
            count,
        )


def _storable(text: str) -> str:
    """Make a text fit to store in Postgres.

    Drops NUL characters, which Postgres cannot store, and turns a lone
    surrogate into ``?``: it is not UTF-8, so the insert would fail and the
    job be retried again and again. A child only gives either if something
    went wrong in it.

    :param text: a page's text, as a reader gave it
    :returns: the text, NUL-free and valid UTF-8
    """
    return text.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")


async def _mark(
    attachment_id: UUID,
    status: TextStatus,
    *,
    pages: int | None = None,
    error: str | None = None,
) -> None:
    """Store how far reading a file got.

    :param attachment_id: the file
    :param status: where reading is
    :param pages: the file's page count, when known; else left as it is
    :param error: the class of the failure that made it unreadable
    """
    if pages is None:
        await Attachment.raw(
            "UPDATE attachments SET text_status = {}, text_error = {} WHERE id = {}",
            status.value,
            error,
            attachment_id,
        )
        return
    await Attachment.raw(
        "UPDATE attachments SET text_status = {}, text_error = {}, pages = {} "
        "WHERE id = {}",
        status.value,
        error,
        pages,
        attachment_id,
    )


async def _finish(
    principal: Principal,
    attachment_id: UUID,
    status: TextStatus,
    *,
    pages: int | None = None,
    error: str | None = None,
) -> TextStatus:
    """End reading a file, and ask for its vectors.

    :param principal: the person the job runs as
    :param attachment_id: the file
    :param status: where reading ended
    :param pages: the file's page count, when known
    :param error: the class of the failure that made it unreadable
    :returns: ``status``
    """
    await _mark(attachment_id, status, pages=pages, error=error)
    await embedding.request_file(principal, attachment_id)
    return status


async def request_backfill(principal: Principal) -> bool:
    """Ask for the person's files to be read and embedded, if any needs it.

    Called when the person's index is loaded, as
    :func:`reef.embedding.request_backfill` is. A file needs it when it is
    ready, in a space the person may write to, and either never looked at,
    or ``waiting`` with no ``read_file`` job of this person's queued or
    running: its job failed for good, or ended with the switch off. Left
    alone, such a file would never be read, nor embedded. With an embedding
    service, a file also needs it when its reading has ended and it has no
    vector. Without a service vectors are not asked about: the files would
    lack them on every load.

    :param principal: the person whose index was just built
    :returns: True if a job was added; False with the switch off, when
        nothing needs it, or when one waits already
    """
    settings = get_settings()
    if not settings.reading.enabled:
        return False
    await arm(principal)
    unread = await Attachment.raw(
        "SELECT 1 FROM attachments a WHERE a.status = 'ready' "
        "AND a.space_id IN (SELECT reef_member_space_ids()) "
        "AND (a.text_status IS NULL OR (a.text_status = 'waiting' AND NOT EXISTS ("
        "SELECT 1 FROM jobs j WHERE j.person_id = {} AND j.kind = {} "
        "AND j.status IN ('queued', 'running') "
        "AND j.subject_ids = ARRAY[a.id]))) LIMIT 1",
        principal.person_id,
        READ_FILE,
    )
    if not unread and not (
        settings.embedding.url and await embedding.files_lacking(principal, limit=1)
    ):
        return False
    return await enqueue(principal, BACKFILL_FILES)


async def backfill(principal: Principal) -> int:
    """Queue reading and embedding for every file of the person's that needs it.

    Reads nothing itself. Files never looked at become ``waiting`` and get a
    ``read_file`` job; a ``waiting`` file without a job of this person's
    waiting gets one again; a file whose reading ended without a vector gets
    an ``embed_file`` job. Only ready files in spaces the person may write
    to count.

    :param principal: the person the work is for
    :returns: how many jobs were added; 0 with the switch off
    """
    if not get_settings().reading.enabled:
        return 0
    await arm(principal)
    await Attachment.raw(
        "UPDATE attachments SET text_status = 'waiting' WHERE status = 'ready' "
        f"AND text_status IS NULL AND {_WRITABLE}"
    )
    waiting = await Attachment.raw(
        "SELECT id FROM attachments WHERE status = 'ready' "
        f"AND text_status = 'waiting' AND {_WRITABLE} ORDER BY created_at DESC"
    )
    added = 0
    for row in waiting:
        added += await request_read(principal, row["id"])
    for attachment_id in await embedding.files_lacking(principal):
        added += await embedding.request_file(principal, attachment_id)
    return added


async def stored_pages(attachment_ids: list[UUID]) -> dict[UUID, list[StoredPage]]:
    """Return the stored pages of files, as the caller's row security shows them.

    The caller arms the principal first.

    :param attachment_ids: the files
    :returns: each file's pages in page order; an empty list for a file
        with none, or one the principal may not see
    """
    found: dict[UUID, list[StoredPage]] = {file: [] for file in attachment_ids}
    if not attachment_ids:
        return found
    rows = await FilePage.raw(
        "SELECT attachment_id, number, text, reader FROM file_pages "
        "WHERE attachment_id = ANY({}::uuid[]) ORDER BY attachment_id, number",
        [str(file) for file in attachment_ids],
    )
    for row in rows:
        found[row["attachment_id"]].append(
            StoredPage(row["number"], row["text"], row["reader"])
        )
    return found


async def has_pages(attachment_id: UUID) -> bool:
    """Report whether Reef holds pages read from a file.

    The caller arms the principal first, so a file the principal may not see
    has none.

    :param attachment_id: the file
    :returns: True when at least one page is stored
    """
    rows = await FilePage.raw(
        "SELECT 1 AS found FROM file_pages WHERE attachment_id = {} LIMIT 1",
        attachment_id,
    )
    return bool(rows)


def _marked(page: StoredPage) -> str:
    """Write one page as it is shown: its number on a line, then its text.

    :param page: the page
    :returns: the marked page
    """
    return f"--- page {page.number} ---\n{page.text}"


def render_text(
    pages: list[StoredPage], *, start: int | None = None, cap: int | None = None
) -> tuple[str, int | None]:
    """Write pages as one text, each page marked with its number.

    The marked pages are joined by a blank line. When the whole text does not
    fit ``cap``, whole pages are kept up to it, from page ``min(start, last)``
    when ``start`` is given, else from the first. A first page that alone
    passes ``cap`` is cut at ``cap``, so a text is never empty for want of
    room.

    :param pages: the pages, in page order
    :param start: the page to read from when the text must be cut
    :param cap: the most characters to return; no limit when None
    :returns: the text, and the number of the last page included when pages
        were left out after it, else None
    """
    if not pages:
        return "", None
    whole = "\n\n".join(_marked(page) for page in pages)
    if cap is None or len(whole) <= cap:
        return whole, None
    first = min(start, pages[-1].number) if start is not None else pages[0].number
    kept: list[str] = []
    used = 0
    last = None
    for page in pages:
        if page.number < first:
            continue
        marked = _marked(page)
        cost = len(marked) + (2 if kept else 0)
        if used + cost > cap:
            if not kept:
                kept.append(marked[:cap])
                last = page.number
            return "\n\n".join(kept), last
        kept.append(marked)
        used += cost
        last = page.number
    return "\n\n".join(kept), None


def read_by(pages: list[StoredPage]) -> list[str]:
    """Name who read a file's pages, as a person is told.

    :param pages: the pages, in page order
    :returns: the distinct names of the readers of pages that hold text, in
        the order they first appear
    """
    names: list[str] = []
    for page in pages:
        name = READER_NAMES[page.reader]
        if page.text and name not in names:
            names.append(name)
    return names
