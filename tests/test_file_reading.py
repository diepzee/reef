"""Reading the text inside stored files: the job, the backfill and the hooks.

These tests manage their own transactions: the worker opens its own, and has
to see committed rows. Files are kept in a :class:`conftest.MemoryStore`,
which stands in for the object store the job reads from.
"""

import asyncio
import logging
import re
import signal
from types import SimpleNamespace
from uuid import UUID

import asyncpg
import pytest
from conftest import MemoryStore, caller_for, store_as
from filefixtures import (
    docx_bytes,
    paragraphs,
    scan_image,
    scan_pdf,
    text_pdf,
    zip_bytes,
)

from reef import filetext, worker
from reef.access import Principal, arm
from reef.attachments import delete_attachment
from reef.config import get_settings
from reef.context import build_index
from reef.db import transaction_scope
from reef.jobs import BACKFILL_FILES, EMBED_FILE, READ_FILE, enqueue
from reef.models import FilePage, Job, TextStatus
from reef.reading import (
    OutOfProcesses,
    Reading,
    SandboxUnavailable,
    ScanPage,
    TooLarge,
    Unreadable,
    local,
    sandbox,
)
from reef.worker import HANDLERS, drain

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DESCRIPTION = "Car insurance"


@pytest.fixture(autouse=True)
def reading(monkeypatch):
    """Switch reading on, keep files in memory, and leave embedding off.

    :returns: the reading settings, to change per test
    """
    settings = get_settings()
    monkeypatch.setattr(settings.reading, "enabled", True)
    monkeypatch.setattr(settings.embedding, "url", "")
    monkeypatch.setattr(filetext, "S3ObjectStore", MemoryStore)
    MemoryStore.objects.clear()
    return settings.reading


@pytest.fixture
def embedder(monkeypatch):
    """Set an embedding service, so embedding is asked for.

    Its jobs wait two minutes, so a drain never sends anything to it.
    """
    monkeypatch.setattr(get_settings().embedding, "url", "http://embedder.internal")
    monkeypatch.setattr(get_settings().embedding, "delay_seconds", 120.0)


def _pdf(count: int) -> bytes:
    """Make a PDF whose pages each carry a text layer.

    :param count: how many pages
    :returns: the PDF
    """
    return text_pdf([f"Polisnummer 4711-2026, pagina {n}" for n in range(1, count + 1)])


async def _pages(principal: Principal, file: UUID) -> list[dict]:
    """Return a file's stored pages as the principal sees them.

    :param principal: who looks
    :param file: the file's id
    :returns: the rows, in page order
    """
    async with transaction_scope():
        await arm(principal)
        return await FilePage.raw(
            "SELECT number, text, reader, model, confidence FROM file_pages "
            "WHERE attachment_id = {} ORDER BY number",
            file,
        )


async def _status(principal: Principal, file: UUID) -> tuple:
    """Return how far reading a file got.

    :param principal: who looks
    :param file: the file's id
    :returns: ``(text_status, pages, text_error)``
    """
    async with transaction_scope():
        await arm(principal)
        rows = await Job.raw(
            "SELECT text_status, pages, text_error FROM attachments WHERE id = {}",
            file,
        )
    row = rows[0]
    return row["text_status"], row["pages"], row["text_error"]


async def _jobs(principal: Principal, kind: str) -> list[dict]:
    """Return the principal's jobs of one kind, oldest first.

    :param principal: whose jobs
    :param kind: which kind
    :returns: each job's status, subjects, error and seconds until it may run
    """
    async with transaction_scope():
        await arm(principal)
        return await Job.raw(
            "SELECT status, subject_ids, error, "
            "EXTRACT(EPOCH FROM run_after - LOCALTIMESTAMP) AS wait "
            "FROM jobs WHERE kind = {} ORDER BY created_at",
            kind,
        )


async def _queue(principal: Principal, kind: str, *subjects: UUID) -> None:
    """Queue a job in a committed transaction of its own.

    :param principal: who the job is for
    :param kind: what to do
    :param subjects: the files it is about
    """
    async with transaction_scope():
        await enqueue(principal, kind, subjects)


async def test_a_stored_file_is_queued_only_with_the_switch_on(
    household, reading, monkeypatch
):
    me = caller_for(household["wouter"])
    monkeypatch.setattr(reading, "enabled", False)
    off = await store_as(me, "personal", "polis.pdf", _pdf(1), PDF, DESCRIPTION)
    assert off.text_status is None
    assert await _jobs(me.principal, READ_FILE) == []
    monkeypatch.setattr(reading, "enabled", True)
    on = await store_as(me, "personal", "polis.pdf", _pdf(1), PDF, DESCRIPTION)
    assert on.text_status == TextStatus.WAITING
    jobs = await _jobs(me.principal, READ_FILE)
    assert [(job["status"], job["subject_ids"]) for job in jobs] == [
        ("queued", [on.id])
    ]
    assert await _status(me.principal, off.id) == (None, None, None)
    assert await _status(me.principal, on.id) == ("waiting", None, None)


async def test_a_text_pdf_is_read_and_marked_read(household, embedder):
    me = caller_for(household["wouter"])
    file = await store_as(me, "personal", "polis.pdf", _pdf(3), PDF, DESCRIPTION)
    assert await drain("test") == 1
    rows = await _pages(me.principal, file.id)
    assert [row["number"] for row in rows] == [1, 2, 3]
    assert {row["reader"] for row in rows} == {"pdf-text"}
    assert "pagina 2" in rows[1]["text"]
    assert await _status(me.principal, file.id) == ("read", 3, None)
    embeds = await _jobs(me.principal, EMBED_FILE)
    assert [job["subject_ids"] for job in embeds] == [[file.id]]
    async with transaction_scope():
        assert await filetext.spent_pages_today(me.principal) == 3

    # A job that comes after the end, such as a duplicate, reads nothing more.
    async with transaction_scope():
        assert await filetext.read_file(me.principal, file.id) is TextStatus.READ
        assert await filetext.spent_pages_today(me.principal) == 3


async def test_a_photo_is_read_by_tesseract(household):
    me = caller_for(household["wouter"])
    data = scan_image(["Polisnummer 4711", "Verzekering van de woning"], "PNG")
    file = await store_as(me, "personal", "foto.png", data, "image/png", DESCRIPTION)
    assert await drain("test") == 1
    (row,) = await _pages(me.principal, file.id)
    assert row["reader"] == "tesseract"
    assert "4711" in row["text"]
    assert await _status(me.principal, file.id) == ("read", 1, None)


async def test_a_long_file_is_read_ten_pages_a_job(household):
    me = caller_for(household["wouter"])
    file = await store_as(me, "personal", "polis.pdf", _pdf(25), PDF, DESCRIPTION)
    assert await drain("test") == 3
    rows = await _pages(me.principal, file.id)
    assert [row["number"] for row in rows] == list(range(1, 26))
    jobs = await _jobs(me.principal, READ_FILE)
    assert [job["status"] for job in jobs] == ["done"] * 3
    assert await _status(me.principal, file.id) == ("read", 25, None)


async def test_reading_resumes_without_duplicates_after_a_crash(household):
    me = caller_for(household["wouter"])
    file = await store_as(me, "personal", "polis.pdf", _pdf(25), PDF, DESCRIPTION)
    async with transaction_scope():
        await arm(me.principal)
        for number in range(1, 11):
            await FilePage.raw(
                "INSERT INTO file_pages (id, attachment_id, number, text, reader) "
                "VALUES (gen_random_uuid(), {}, {}, 'earlier', 'pdf-text')",
                file.id,
                number,
            )
    async with transaction_scope():
        assert await filetext.read_file(me.principal, file.id) is TextStatus.WAITING
    rows = await _pages(me.principal, file.id)
    assert [row["number"] for row in rows] == list(range(1, 21))
    assert [row["text"] for row in rows[:10]] == ["earlier"] * 10
    assert "pagina 11" in rows[10]["text"]
    assert await _status(me.principal, file.id) == ("waiting", None, None)


async def test_a_page_stored_meanwhile_by_another_job_is_kept_once(
    household, monkeypatch, seed
):
    """Another job storing the same page first does not fail this one."""
    me = caller_for(household["wouter"])
    file = await store_as(me, "personal", "polis.pdf", _pdf(3), PDF, DESCRIPTION)
    opened = local.open_page

    async def open_page(data, kind, number, *, dpi):
        if number == 2:
            await seed.execute(
                "INSERT INTO file_pages (id, attachment_id, number, text, reader) "
                "VALUES (gen_random_uuid(), $1, 2, 'other job', 'pdf-text')",
                file.id,
            )
        return await opened(data, kind, number, dpi=dpi)

    monkeypatch.setattr(local, "open_page", open_page)
    assert await drain("test") == 1
    rows = await _pages(me.principal, file.id)
    assert [(row["number"], row["text"] == "other job") for row in rows] == [
        (1, False),
        (2, True),
        (3, False),
    ]
    assert await _status(me.principal, file.id) == ("read", 3, None)


async def test_pages_past_the_limit_are_not_read(household, reading, monkeypatch):
    me = caller_for(household["wouter"])
    monkeypatch.setattr(reading, "pages_per_file", 3)
    file = await store_as(me, "personal", "polis.pdf", _pdf(5), PDF, DESCRIPTION)
    assert await drain("test") == 1
    rows = await _pages(me.principal, file.id)
    assert [row["number"] for row in rows] == [1, 2, 3]
    assert await _status(me.principal, file.id) == ("partly_read", 5, None)


async def test_a_scan_is_read_by_tesseract(household):
    me = caller_for(household["wouter"])
    data = scan_pdf(["Polisnummer 4711", "Verzekering van de woning"])
    file = await store_as(me, "personal", "scan.pdf", data, PDF, DESCRIPTION)
    assert await drain("test") == 1
    (row,) = await _pages(me.principal, file.id)
    assert row["reader"] == "tesseract"
    assert "4711" in row["text"]
    assert row["model"].startswith("tesseract")
    # Stored from 0 to 100, as the column says; Tesseract is sure of a clean scan.
    assert 50 < row["confidence"] <= 100
    assert await _status(me.principal, file.id) == ("read", 1, None)


async def test_a_page_refused_as_too_large_is_stored_empty(household, monkeypatch):
    """One page too large to read does not lose the rest of the file."""
    me = caller_for(household["wouter"])
    file = await store_as(me, "personal", "plan.pdf", _pdf(3), PDF, DESCRIPTION)
    opened = local.open_page

    async def open_page(data, kind, number, *, dpi):
        if number == 2:
            raise TooLarge
        return await opened(data, kind, number, dpi=dpi)

    monkeypatch.setattr(local, "open_page", open_page)
    assert await drain("test") == 1
    rows = await _pages(me.principal, file.id)
    assert [(row["number"], row["text"] == "") for row in rows] == [
        (1, False),
        (2, True),
        (3, False),
    ]
    assert rows[1]["reader"] == "tesseract"
    assert await _status(me.principal, file.id) == ("read", 3, None)


async def test_a_word_file_is_read_whole_in_one_job(household, reading, monkeypatch):
    me = caller_for(household["wouter"])
    monkeypatch.setattr(reading, "page_chars", 1_000)
    monkeypatch.setattr(reading, "pages_per_job", 2)
    data = docx_bytes(paragraphs(5, 600))
    file = await store_as(me, "personal", "notes.docx", data, DOCX, DESCRIPTION)
    assert await drain("test") == 1
    rows = await _pages(me.principal, file.id)
    assert [row["number"] for row in rows] == [1, 2, 3, 4, 5]
    assert {row["reader"] for row in rows} == {"docx"}
    assert rows[0]["text"].startswith("00000")
    assert await _status(me.principal, file.id) == ("read", 5, None)


async def test_an_unread_format_is_marked_not_read_and_still_embedded(
    household, reading, monkeypatch, embedder
):
    me = caller_for(household["wouter"])
    postscript = b"%!PS-Adobe-3.0\n%%Title: plan\nshowpage\n"
    file = await store_as(me, "personal", "plan.ps", postscript, "text/plain", "Plan")
    assert file.text_status == TextStatus.NOT_READ
    assert await _jobs(me.principal, READ_FILE) == []
    embeds = await _jobs(me.principal, EMBED_FILE)
    assert [job["subject_ids"] for job in embeds] == [[file.id]]

    # A ZIP named as a Word file, without Word's main part, is no Word file.
    renamed = zip_bytes({"hello.txt": b"hi"})
    zipped = await store_as(me, "personal", "notes.docx", renamed, DOCX, "Notes")
    assert zipped.text_status == TextStatus.WAITING
    assert await drain("test") == 1
    assert await _status(me.principal, zipped.id) == ("not_read", None, None)
    assert await _pages(me.principal, zipped.id) == []
    embeds = await _jobs(me.principal, EMBED_FILE)
    assert [job["subject_ids"] for job in embeds] == [[file.id], [zipped.id]]


async def test_a_broken_file_is_unreadable_and_not_retried(household, embedder):
    me = caller_for(household["wouter"])
    data = b"%PDF-1.7 garbage"
    file = await store_as(me, "personal", "polis.pdf", data, PDF, DESCRIPTION)
    assert await drain("test") == 1
    assert await _status(me.principal, file.id) == ("unreadable", None, "Unreadable")
    jobs = await _jobs(me.principal, READ_FILE)
    assert [job["status"] for job in jobs] == ["done"]
    embeds = await _jobs(me.principal, EMBED_FILE)
    assert [job["subject_ids"] for job in embeds] == [[file.id]]


async def test_a_page_that_runs_out_of_time_makes_the_file_unreadable(
    household, reading, monkeypatch
):
    me = caller_for(household["wouter"])
    monkeypatch.setattr(reading, "page_seconds", 0.001)
    file = await store_as(me, "personal", "polis.pdf", _pdf(2), PDF, DESCRIPTION)
    assert await drain("test") == 1
    assert await _status(me.principal, file.id) == (
        "unreadable",
        None,
        "ReadTimedOut",
    )
    assert [job["status"] for job in await _jobs(me.principal, READ_FILE)] == ["done"]


async def test_a_failing_page_keeps_the_pages_read_before_it(household, monkeypatch):
    me = caller_for(household["wouter"])
    file = await store_as(me, "personal", "polis.pdf", _pdf(3), PDF, DESCRIPTION)
    opened = local.open_page

    async def open_page(data, kind, number, *, dpi):
        if number == 3:
            raise Unreadable
        return await opened(data, kind, number, dpi=dpi)

    monkeypatch.setattr(local, "open_page", open_page)
    assert await drain("test") == 1
    rows = await _pages(me.principal, file.id)
    assert [row["number"] for row in rows] == [1, 2]
    assert await _status(me.principal, file.id) == ("unreadable", None, "Unreadable")


async def test_a_broken_file_does_not_stop_the_next_job(household):
    me = caller_for(household["wouter"])
    hostile = await store_as(
        me, "personal", "bad.pdf", b"%PDF-1.7 garbage", PDF, DESCRIPTION
    )
    good = await store_as(me, "personal", "polis.pdf", _pdf(1), PDF, DESCRIPTION)
    assert await drain("test") == 2
    assert (await _status(me.principal, hostile.id))[0] == "unreadable"
    assert await _status(me.principal, good.id) == ("read", 1, None)


async def test_the_reader_is_swept_before_and_after_each_reading_job(
    household, monkeypatch
):
    """However the job ends, no process of ``reader`` outlives it."""
    me = caller_for(household["wouter"])
    await store_as(me, "personal", "polis.pdf", _pdf(1), PDF, DESCRIPTION)
    happened: list[str] = []

    def sweep() -> int:
        """Note a sweep that finds nothing.

        :returns: how many processes it killed
        """
        happened.append("sweep")
        return 0

    async def read_file(*args, **kwargs):
        """Note the read, and fail it.

        :raises SandboxUnavailable: always
        """
        happened.append("read")
        raise SandboxUnavailable

    monkeypatch.setattr(sandbox, "kill_reader_processes", sweep)
    monkeypatch.setattr(filetext, "read_file", read_file)
    assert await drain("test") == 1
    assert happened == ["sweep", "read", "sweep"]


def _sweeps_failing(at: int, happened: list[str]):
    """Make a sweep that fails on one call, as ``/proc`` or ``kill`` might.

    :param at: which call fails, counted from 1
    :param happened: where each call is noted
    :returns: the sweep
    """

    def sweep() -> int:
        """Note the sweep; fail it on the chosen call.

        :returns: how many processes it killed
        :raises PermissionError: on the chosen call, with a path in the message
        """
        happened.append("sweep")
        if happened.count("sweep") == at:
            raise PermissionError(13, "Permission denied", "/proc/4711/status")
        return 0

    return sweep


async def test_a_sweep_that_fails_after_a_read_keeps_the_pages(
    household, monkeypatch, caplog
):
    """The pages read are kept, and only the error's class is logged."""
    me = caller_for(household["wouter"])
    file = await store_as(me, "personal", "polis.pdf", _pdf(1), PDF, DESCRIPTION)
    happened: list[str] = []
    monkeypatch.setattr(sandbox, "kill_reader_processes", _sweeps_failing(2, happened))
    with caplog.at_level(logging.INFO, logger="reef.worker"):
        assert await drain("test") == 1
    assert happened == ["sweep", "sweep"]
    assert [job["status"] for job in await _jobs(me.principal, READ_FILE)] == ["done"]
    assert await _status(me.principal, file.id) == ("read", 1, None)
    lines = [record.getMessage() for record in caplog.records]
    assert "read_file: the sweep after the read failed (PermissionError)" in lines
    assert not any("4711" in line for line in lines)


async def test_a_sweep_that_fails_after_a_failed_read_keeps_its_error(
    household, monkeypatch
):
    """The job is retried under the read's own error, not the sweep's."""
    me = caller_for(household["wouter"])
    await store_as(me, "personal", "polis.pdf", _pdf(1), PDF, DESCRIPTION)
    happened: list[str] = []

    async def read_file(*args, **kwargs):
        """Fail the read.

        :raises SandboxUnavailable: always
        """
        raise SandboxUnavailable

    monkeypatch.setattr(sandbox, "kill_reader_processes", _sweeps_failing(2, happened))
    monkeypatch.setattr(filetext, "read_file", read_file)
    assert await drain("test") == 1
    jobs = await _jobs(me.principal, READ_FILE)
    assert [(job["status"], job["error"]) for job in jobs] == [
        ("queued", "SandboxUnavailable")
    ]


async def test_a_sweep_that_fails_before_a_read_fails_the_job(household, monkeypatch):
    """No file is read while a process an earlier read left may still run.

    The job is retried, so the next attempt sweeps again.
    """
    me = caller_for(household["wouter"])
    file = await store_as(me, "personal", "polis.pdf", _pdf(1), PDF, DESCRIPTION)
    happened: list[str] = []
    monkeypatch.setattr(sandbox, "kill_reader_processes", _sweeps_failing(1, happened))
    assert await drain("test") == 1
    assert happened == ["sweep"]
    jobs = await _jobs(me.principal, READ_FILE)
    assert [(job["status"], job["error"]) for job in jobs] == [
        ("queued", "PermissionError")
    ]
    assert await _status(me.principal, file.id) == ("waiting", None, None)


async def test_a_missing_sandbox_fails_the_job_so_the_queue_retries(
    household, monkeypatch
):
    me = caller_for(household["wouter"])
    file = await store_as(me, "personal", "polis.pdf", _pdf(1), PDF, DESCRIPTION)

    async def survey(data, kind):
        raise SandboxUnavailable

    monkeypatch.setattr(local, "survey", survey)
    assert await drain("test") == 1
    jobs = await _jobs(me.principal, READ_FILE)
    assert [(job["status"], job["error"]) for job in jobs] == [
        ("queued", "SandboxUnavailable")
    ]
    assert await _status(me.principal, file.id) == ("waiting", None, None)


async def test_a_full_process_count_is_retried_and_the_file_keeps_no_failure(
    household, monkeypatch
):
    """The worker is short of processes; the file is not at fault."""
    me = caller_for(household["wouter"])
    file = await store_as(me, "personal", "polis.pdf", _pdf(1), PDF, DESCRIPTION)

    async def survey(data, kind):
        """Fail as a child that could not start a process does.

        :raises OutOfProcesses: always
        """
        raise OutOfProcesses

    monkeypatch.setattr(local, "survey", survey)
    assert await drain("test") == 1
    jobs = await _jobs(me.principal, READ_FILE)
    assert [(job["status"], job["error"]) for job in jobs] == [
        ("queued", "OutOfProcesses")
    ]
    assert await _status(me.principal, file.id) == ("waiting", None, None)


async def test_the_worker_adopts_orphans_before_it_serves(monkeypatch, caplog):
    """Its children's orphans come to it, so the sweep can collect them; a
    refusal is logged by class and the worker serves anyway."""
    happened: list[str] = []

    def adopt() -> bool:
        """Note the adoption, and fail it.

        :raises OSError: always
        """
        happened.append("adopt")
        raise OSError(22, "Invalid argument")

    async def nothing() -> None:
        """Stand in for the connection pool."""

    async def serve(name: str, stop: asyncio.Event, poll_seconds: float) -> None:
        """Note that the worker serves.

        :param name: the worker's name
        :param stop: when to stop
        :param poll_seconds: how long to wait for jobs
        """
        happened.append("serve")

    monkeypatch.setattr(sandbox, "adopt_orphans", adopt)
    monkeypatch.setattr(worker, "serve", serve)
    monkeypatch.setattr(
        worker,
        "DB",
        SimpleNamespace(start_connection_pool=nothing, close_connection_pool=nothing),
    )
    loop = asyncio.get_running_loop()
    try:
        with caplog.at_level(logging.INFO, logger="reef.worker"):
            await worker._main()
    finally:
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(signum)
    assert happened == ["adopt", "serve"]
    lines = [record.getMessage() for record in caplog.records]
    assert "worker: orphaned processes are not adopted (OSError)" in lines


async def test_a_missing_sandbox_while_reading_pages_fails_the_job(
    household, monkeypatch
):
    """Raised inside the reads running together, it still fails the job by name."""
    me = caller_for(household["wouter"])
    file = await store_as(me, "personal", "polis.pdf", _pdf(3), PDF, DESCRIPTION)
    opened = local.open_page

    async def open_page(data, kind, number, *, dpi):
        if number == 2:
            raise SandboxUnavailable
        return await opened(data, kind, number, dpi=dpi)

    monkeypatch.setattr(local, "open_page", open_page)
    assert await drain("test") == 1
    jobs = await _jobs(me.principal, READ_FILE)
    assert [(job["status"], job["error"]) for job in jobs] == [
        ("queued", "SandboxUnavailable")
    ]
    assert await _pages(me.principal, file.id) == []
    assert await _status(me.principal, file.id) == ("waiting", None, None)
    async with transaction_scope():
        assert await filetext.spent_pages_today(me.principal) == 0


async def test_a_scan_page_has_one_time_limit_for_render_and_ocr(
    household, reading, monkeypatch
):
    """Rendering and reading each fit in the page's time, but not together."""
    me = caller_for(household["wouter"])
    monkeypatch.setattr(reading, "page_seconds", 0.3)

    async def survey(data, kind):
        return 1

    async def open_page(data, kind, number, *, dpi):
        await asyncio.sleep(0.2)
        return ScanPage(number=number, image=b"", source="pdf", dpi=dpi)

    async def read(self, page):
        await asyncio.sleep(0.2)
        return Reading(
            text="Polisnummer 4711 van de woning", reader="tesseract", model="t"
        )

    monkeypatch.setattr(local, "survey", survey)
    monkeypatch.setattr(local, "open_page", open_page)
    monkeypatch.setattr(local.TesseractReader, "read", read)
    file = await store_as(me, "personal", "scan.pdf", _pdf(1), PDF, DESCRIPTION)
    assert await drain("test") == 1
    assert await _status(me.principal, file.id) == (
        "unreadable",
        None,
        "ReadTimedOut",
    )
    assert await _pages(me.principal, file.id) == []


async def test_a_lone_surrogate_is_stored_as_a_question_mark(household, monkeypatch):
    """Text that is not UTF-8 cannot make the insert fail and the job retry."""
    me = caller_for(household["wouter"])

    async def open_page(data, kind, number, *, dpi):
        text = "Polisnummer\ud8004711\x00 van de woning"
        return Reading(text=text, reader="pdf-text", model="pdfium")

    monkeypatch.setattr(local, "open_page", open_page)
    file = await store_as(me, "personal", "polis.pdf", _pdf(1), PDF, DESCRIPTION)
    assert await drain("test") == 1
    (row,) = await _pages(me.principal, file.id)
    assert row["text"] == "Polisnummer?4711 van de woning"
    assert await _status(me.principal, file.id) == ("read", 1, None)


async def test_a_finished_file_is_not_read_again(household, reading, monkeypatch):
    """An unreadable file and a read Word file start no child and cost nothing."""
    me = caller_for(household["wouter"])
    monkeypatch.setattr(reading, "page_chars", 1_000)
    broken = await store_as(
        me, "personal", "bad.pdf", b"%PDF-1.7 garbage", PDF, DESCRIPTION
    )
    data = docx_bytes(paragraphs(3, 600))
    word = await store_as(me, "personal", "notes.docx", data, DOCX, DESCRIPTION)
    assert await drain("test") == 2
    assert (await _status(me.principal, broken.id))[0] == "unreadable"
    assert (await _status(me.principal, word.id))[0] == "read"
    async with transaction_scope():
        spent = await filetext.spent_pages_today(me.principal)
    children: list[list[str]] = []

    async def run_child(args, data, **kwargs):
        children.append(args)
        raise AssertionError("no child may be started")

    monkeypatch.setattr(local, "run_child", run_child)
    async with transaction_scope():
        assert await filetext.read_file(me.principal, broken.id) is (
            TextStatus.UNREADABLE
        )
        assert await filetext.read_file(me.principal, word.id) is TextStatus.READ
        assert await filetext.spent_pages_today(me.principal) == spent
    assert children == []


async def test_a_spent_allowance_requeues_the_file_for_later(
    household, reading, monkeypatch
):
    me = caller_for(household["wouter"])
    monkeypatch.setattr(reading, "daily_pages", 2)
    file = await store_as(me, "personal", "polis.pdf", _pdf(5), PDF, DESCRIPTION)
    assert await drain("test") == 2
    rows = await _pages(me.principal, file.id)
    assert [row["number"] for row in rows] == [1, 2]
    jobs = await _jobs(me.principal, READ_FILE)
    assert [job["status"] for job in jobs] == ["done", "done", "queued"]
    assert jobs[-1]["subject_ids"] == [file.id]
    assert jobs[-1]["wait"] >= reading.retry_seconds - 60
    async with transaction_scope():
        assert await filetext.spent_pages_today(me.principal) == 2
        await arm(me.principal)
        spend = await Job.raw("SELECT reader, pages FROM reading_spend")
    assert [(row["reader"], row["pages"]) for row in spend] == [("pdf-text", 2)]
    assert await _status(me.principal, file.id) == ("waiting", None, None)


async def test_a_job_for_a_deleted_file_ends_quietly(household):
    me = caller_for(household["wouter"])
    file = await store_as(me, "personal", "polis.pdf", _pdf(1), PDF, DESCRIPTION)
    assert await delete_attachment(
        me.principal, "personal", file.object_key, MemoryStore()
    )
    assert await drain("test") == 1
    assert [job["status"] for job in await _jobs(me.principal, READ_FILE)] == ["done"]
    assert await _pages(me.principal, file.id) == []


async def test_a_job_reads_only_files_its_person_may_write(
    household, graph, reading, monkeypatch
):
    me = caller_for(household["wouter"])
    partner = caller_for(household["partner"])
    monkeypatch.setattr(reading, "enabled", False)
    personal = await store_as(me, "personal", "mine.pdf", _pdf(1), PDF, DESCRIPTION)
    shared = await store_as(me, "household", "ours.pdf", _pdf(1), PDF, DESCRIPTION)
    monkeypatch.setattr(reading, "enabled", True)
    await graph.set_role(household["partner"], household["shared"], "viewer")
    await _queue(partner.principal, READ_FILE, personal.id)
    await _queue(partner.principal, READ_FILE, shared.id)
    assert await drain("test") == 2
    assert [job["status"] for job in await _jobs(partner.principal, READ_FILE)] == [
        "done",
        "done",
    ]
    for file in (personal, shared):
        assert await _pages(me.principal, file.id) == []
        assert await _status(me.principal, file.id) == (None, None, None)
    async with transaction_scope():
        assert await filetext.spent_pages_today(partner.principal) == 0


async def test_a_former_members_job_reads_nothing(
    household, graph, reading, monkeypatch
):
    me = caller_for(household["wouter"])
    partner = caller_for(household["partner"])
    monkeypatch.setattr(reading, "enabled", False)
    shared = await store_as(me, "household", "ours.pdf", _pdf(1), PDF, DESCRIPTION)
    monkeypatch.setattr(reading, "enabled", True)
    await _queue(partner.principal, READ_FILE, shared.id)
    await graph.drop_membership(household["partner"], household["shared"])
    assert await drain("test") == 1
    assert [job["status"] for job in await _jobs(partner.principal, READ_FILE)] == [
        "done"
    ]
    assert await _pages(me.principal, shared.id) == []
    assert await _status(me.principal, shared.id) == (None, None, None)


async def test_one_reading_job_waits_per_file_and_person(household, reading):
    me = caller_for(household["wouter"])
    partner = caller_for(household["partner"])
    reading.enabled = False
    try:
        file = await store_as(me, "household", "ours.pdf", _pdf(1), PDF, DESCRIPTION)
    finally:
        reading.enabled = True
    async with transaction_scope():
        assert await filetext.request_read(me.principal, file.id) is True
        assert await filetext.request_read(me.principal, file.id) is False
        assert await filetext.request_read(partner.principal, file.id) is True


async def test_loading_the_index_asks_for_the_backfill_once(
    household, graph, reading, monkeypatch
):
    me = caller_for(household["wouter"])
    partner = caller_for(household["partner"])
    monkeypatch.setattr(reading, "enabled", False)
    await store_as(me, "household", "ours.pdf", _pdf(1), PDF, DESCRIPTION)
    async with transaction_scope():
        await build_index(me.principal)
    assert await _jobs(me.principal, BACKFILL_FILES) == []

    monkeypatch.setattr(reading, "enabled", True)
    for _ in range(2):
        async with transaction_scope():
            await build_index(me.principal)
    assert len(await _jobs(me.principal, BACKFILL_FILES)) == 1

    await graph.set_role(household["partner"], household["shared"], "viewer")
    async with transaction_scope():
        await build_index(partner.principal)
    assert await _jobs(partner.principal, BACKFILL_FILES) == []


async def test_the_index_asks_for_vectors_only_with_an_embedding_service(
    household, reading, monkeypatch
):
    """A read file without a vector asks for the backfill only with a service."""
    me = caller_for(household["wouter"])
    file = await store_as(me, "personal", "polis.pdf", _pdf(1), PDF, DESCRIPTION)
    assert await drain("test") == 1
    assert (await _status(me.principal, file.id))[0] == "read"
    async with transaction_scope():
        assert await filetext.request_backfill(me.principal) is False
    monkeypatch.setattr(get_settings().embedding, "url", "http://embedder.internal")
    async with transaction_scope():
        assert await filetext.request_backfill(me.principal) is True


async def test_the_backfill_reads_never_read_files_and_requeues_stuck_ones(
    household, reading, monkeypatch
):
    me = caller_for(household["wouter"])
    monkeypatch.setattr(reading, "enabled", False)
    never = await store_as(me, "personal", "a.pdf", _pdf(1), PDF, DESCRIPTION)
    stuck = await store_as(me, "personal", "b.pdf", _pdf(1), PDF, DESCRIPTION)
    monkeypatch.setattr(reading, "enabled", True)
    queued = await store_as(me, "personal", "c.pdf", _pdf(1), PDF, DESCRIPTION)
    async with transaction_scope():
        await arm(me.principal)
        await Job.raw(
            "UPDATE attachments SET text_status = 'waiting' WHERE id = {}", stuck.id
        )
    await _queue(me.principal, BACKFILL_FILES)
    assert await drain("test") == 4
    for file in (never, stuck, queued):
        assert await _status(me.principal, file.id) == ("read", 1, None)
    jobs = await _jobs(me.principal, READ_FILE)
    subjects = sorted(str(job["subject_ids"][0]) for job in jobs)
    assert subjects == sorted(str(file.id) for file in (never, stuck, queued))


async def test_a_file_left_waiting_is_healed_by_the_next_index_load(
    household, reading, monkeypatch, seed
):
    """A file whose job failed for good, or ended with the switch off, is read."""
    me = caller_for(household["wouter"])
    failed = await store_as(me, "personal", "a.pdf", _pdf(1), PDF, DESCRIPTION)
    ended = await store_as(me, "personal", "b.pdf", _pdf(1), PDF, DESCRIPTION)
    async with transaction_scope():
        await build_index(me.principal)
    assert await _jobs(me.principal, BACKFILL_FILES) == []

    await seed.execute(
        "UPDATE jobs SET status = 'failed', error = 'SandboxUnavailable' "
        "WHERE $1 = ANY(subject_ids)",
        failed.id,
    )
    monkeypatch.setattr(reading, "enabled", False)
    assert await drain("test") == 1
    monkeypatch.setattr(reading, "enabled", True)
    for file in (failed, ended):
        assert await _status(me.principal, file.id) == ("waiting", None, None)

    for _ in range(2):
        async with transaction_scope():
            await build_index(me.principal)
    assert len(await _jobs(me.principal, BACKFILL_FILES)) == 1
    assert await drain("test") == 3
    for file in (failed, ended):
        assert await _status(me.principal, file.id) == ("read", 1, None)
    async with transaction_scope():
        assert await filetext.request_backfill(me.principal) is False


async def test_a_file_out_of_processes_is_tried_again_a_day_later(
    household, reading, monkeypatch, seed
):
    """A worker fault is not healed on every index load, or a hostile file
    that fills its cap would be read again on each: its failed attempts
    roll back and cost nothing. Any other failure still heals at once."""
    me = caller_for(household["wouter"])
    held = await store_as(me, "personal", "a.pdf", _pdf(1), PDF, DESCRIPTION)
    await seed.execute(
        "UPDATE jobs SET status = 'failed', error = 'OutOfProcesses', "
        "finished_at = LOCALTIMESTAMP WHERE $1 = ANY(subject_ids)",
        held.id,
    )
    async with transaction_scope():
        await build_index(me.principal)
    assert await _jobs(me.principal, BACKFILL_FILES) == []
    async with transaction_scope():
        assert await filetext.request_backfill(me.principal) is False

    other = await store_as(me, "personal", "b.pdf", _pdf(1), PDF, DESCRIPTION)
    await seed.execute(
        "UPDATE jobs SET status = 'failed', error = 'SandboxUnavailable', "
        "finished_at = LOCALTIMESTAMP WHERE $1 = ANY(subject_ids)",
        other.id,
    )
    async with transaction_scope():
        await build_index(me.principal)
    assert await drain("test") == 2
    assert await _status(me.principal, other.id) == ("read", 1, None)
    assert await _status(me.principal, held.id) == ("waiting", None, None)
    held_jobs = [
        job["status"]
        for job in await _jobs(me.principal, READ_FILE)
        if job["subject_ids"] == [held.id]
    ]
    assert held_jobs == ["failed"]

    await seed.execute(
        "UPDATE jobs SET finished_at = LOCALTIMESTAMP - interval '25 hours' "
        "WHERE $1 = ANY(subject_ids)",
        held.id,
    )
    async with transaction_scope():
        await build_index(me.principal)
    assert await drain("test") == 2
    assert await _status(me.principal, held.id) == ("read", 1, None)


@pytest.mark.parametrize("reverse_insert", [False, True])
@pytest.mark.parametrize("highest_fault", [False, True])
@pytest.mark.parametrize("request_only", [False, True])
async def test_tied_reading_jobs_choose_the_highest_id(
    household: dict,
    seed: asyncpg.Connection,
    reverse_insert: bool,
    highest_fault: bool,
    request_only: bool,
) -> None:
    """Both backfill paths choose the same job when creation times tie.

    Insert in both orders and force equal timestamps, so heap order cannot
    stand in for the id tiebreaker. Repeat after removing only queued jobs
    to check the same completed history each time.

    :param household: two people with personal and shared spaces
    :param seed: the connection used to write completed job history
    :param reverse_insert: whether the larger id is inserted first
    :param highest_fault: whether the larger id should hold the file back
    :param request_only: whether to ask for backfill or run it directly
    """
    me = caller_for(household["wouter"])
    file = await store_as(me, "personal", "tied.pdf", _pdf(1), PDF, DESCRIPTION)
    await seed.execute("DELETE FROM jobs WHERE person_id = $1", me.principal.person_id)
    ids = [UUID(int=1), UUID(int=2)]
    for job_id in reversed(ids) if reverse_insert else ids:
        fault = highest_fault if job_id == ids[1] else not highest_fault
        await seed.execute(
            "INSERT INTO jobs (id, person_id, kind, subject_ids, status, error, "
            "created_at, finished_at) VALUES ($1, $2, $3, $4, 'failed', $5, "
            "TIMESTAMP '2026-01-01 12:00:00', LOCALTIMESTAMP)",
            job_id,
            me.principal.person_id,
            READ_FILE,
            [file.id],
            "OutOfProcesses" if fault else "SandboxUnavailable",
        )
    for _ in range(3):
        async with transaction_scope():
            result = await (
                filetext.request_backfill(me.principal)
                if request_only
                else filetext.backfill(me.principal)
            )
        assert result == (0 if highest_fault else 1)
        await seed.execute(
            "DELETE FROM jobs WHERE person_id = $1 AND status = 'queued'",
            me.principal.person_id,
        )


async def test_a_file_whose_job_is_running_does_not_ask_for_the_backfill(
    household, seed
):
    me = caller_for(household["wouter"])
    await store_as(me, "personal", "a.pdf", _pdf(1), PDF, DESCRIPTION)
    await seed.execute("UPDATE jobs SET status = 'running'")
    async with transaction_scope():
        assert await filetext.request_backfill(me.principal) is False


async def test_the_backfill_queues_embedding_for_files_without_a_vector(
    household, reading, monkeypatch, embedder
):
    me = caller_for(household["wouter"])
    file = await store_as(me, "personal", "polis.pdf", _pdf(1), PDF, DESCRIPTION)
    assert await drain("test") == 1
    async with transaction_scope():
        await arm(me.principal)
        await Job.raw("DELETE FROM jobs WHERE kind = {}", EMBED_FILE)
    async with transaction_scope():
        assert await filetext.backfill(me.principal) == 1
    embeds = await _jobs(me.principal, EMBED_FILE)
    assert [job["subject_ids"] for job in embeds] == [[file.id]]


async def test_the_worker_does_nothing_with_the_switch_off(
    household, reading, monkeypatch
):
    me = caller_for(household["wouter"])
    file = await store_as(me, "personal", "polis.pdf", _pdf(1), PDF, DESCRIPTION)
    await _queue(me.principal, BACKFILL_FILES)
    monkeypatch.setattr(reading, "enabled", False)
    assert await drain("test") == 2
    assert await _pages(me.principal, file.id) == []
    assert await _status(me.principal, file.id) == ("waiting", None, None)
    assert [job["status"] for job in await _jobs(me.principal, READ_FILE)] == ["done"]
    async with transaction_scope():
        assert await filetext.request_read(me.principal, file.id) is False
        assert await filetext.request_backfill(me.principal) is False
        assert await filetext.backfill(me.principal) == 0
        assert await filetext.read_file(me.principal, file.id) is None
    assert len(await _jobs(me.principal, READ_FILE)) == 1


async def test_stored_pages_are_given_in_order_to_who_may_see_them(household):
    me = caller_for(household["wouter"])
    partner = caller_for(household["partner"])
    file = await store_as(me, "personal", "polis.pdf", _pdf(2), PDF, DESCRIPTION)
    assert await drain("test") == 1
    async with transaction_scope():
        await arm(me.principal)
        stored = await filetext.stored_pages([file.id])
    assert [(page.number, page.reader) for page in stored[file.id]] == [
        (1, "pdf-text"),
        (2, "pdf-text"),
    ]
    assert "pagina 2" in stored[file.id][1].text
    async with transaction_scope():
        await arm(partner.principal)
        assert await filetext.stored_pages([file.id]) == {file.id: []}


async def test_the_log_says_counts_and_nothing_private(household, caplog):
    me = caller_for(household["wouter"])
    secret = "Polisnummer"
    await store_as(me, "personal", "geheim-polis.pdf", _pdf(2), PDF, "Zorgpolis")
    with caplog.at_level(logging.INFO, logger="reef.worker"):
        assert await drain("test") == 1
    lines = [record.getMessage() for record in caplog.records]
    assert any(
        re.fullmatch(
            r"read_file: 2 pages \(layer 2, ocr 0, format 0\) read in \d+\.\d s", line
        )
        for line in lines
    )
    for line in lines:
        for private in (
            secret,
            "geheim",
            "Zorgpolis",
            "personal",
            str(household["wouter"].id),
            household["wouter"].email,
        ):
            assert private not in line


def test_no_job_kind_holds_a_scrubbed_word():
    """Logfire redacts a value with a scrubbed word, job kinds included."""
    for kind in (READ_FILE, BACKFILL_FILES):
        assert kind in HANDLERS
        for word in ("text", "filename", "description"):
            assert word not in kind
