"""Count the files Reef holds for one person, and time reading their scans.

Prints one JSON object of numbers. It never prints a filename, key,
description, text or address. Run it as that person, in ``reef-worker``,
which has the read-only storage token::

    railway ssh -s reef-worker -- uv run --frozen --no-dev \\
      python scripts/file_report.py --email PERSON [--time N] [--queue]

The report counts the person's ready files and the pages read so far, per
kind. A file's kind comes from its stored type and name, without fetching it.

``--time N`` reads up to N scan pages through the real sandbox and Tesseract,
stores nothing, and adds the seconds one page took (median and longest), the
median word confidence, and how many files it could not read and skipped.
``--queue`` queues reading for every file that needs it, as the backfill
does, and adds how many jobs it queued; that needs ``REEF_FILE_TEXT`` set.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import PurePosixPath

from reef import filetext
from reef.access import Principal, arm
from reef.attachments import ObjectStore, S3ObjectStore
from reef.config import get_settings
from reef.db import DB, transaction_scope
from reef.identity import person_by_email
from reef.models import Attachment
from reef.reading import FileKind, ReadFailed, ScanPage, local
from reef.reading.kinds import likely_kind

PROBE_LIMIT_PER_PAGE = 20
"""How many pages are opened, per timed scan page, before the search for
scans gives up. A person's pages may all have a text layer."""

_OFFICE = {".docx": FileKind.DOCX, ".xlsx": FileKind.XLSX, ".pptx": FileKind.PPTX}


def kind_of(mime: str, filename: str) -> FileKind:
    """Tell a file's kind from its stored type and name, without its bytes.

    :param mime: the type stored with the file
    :param filename: the stored filename
    :returns: the kind; ``OTHER`` for what Reef does not read
    """
    suffix = PurePosixPath(filename).suffix.lower()
    if mime == "application/pdf" or suffix == ".pdf":
        return FileKind.PDF
    if mime.startswith("image/"):
        return FileKind.IMAGE
    return _OFFICE.get(suffix) or likely_kind(b"", filename)


async def count(principal: Principal) -> dict[str, dict[str, int]]:
    """Count the person's ready files, and their pages read, per kind.

    :param principal: the person
    :returns: ``files`` and ``pages``, each a count per kind name
    """
    await arm(principal)
    rows = await Attachment.raw(
        "SELECT mime, filename, COALESCE(pages, 0) AS pages FROM attachments "
        "WHERE status = 'ready'"
    )
    files: dict[str, int] = {}
    pages: dict[str, int] = {}
    for row in rows:
        kind = kind_of(row["mime"], row["filename"]).value
        files[kind] = files.get(kind, 0) + 1
        if row["pages"]:
            pages[kind] = pages.get(kind, 0) + row["pages"]
    return {"files": files, "pages": pages}


async def time_scans(
    principal: Principal, limit: int, store: ObjectStore
) -> dict[str, object]:
    """Read up to ``limit`` scan pages and time each one. Nothing is stored.

    A file that cannot be read is skipped and counted, and the next one is
    tried: one broken file must not end the report.

    :param principal: the person
    :param limit: how many scan pages to read
    :param store: where the files' bytes are
    :returns: ``timed_pages``; ``unreadable``, the files skipped because a
        read failed; and, if any page was read, ``seconds_per_page`` (median
        and max) and ``median_confidence``
    """
    await arm(principal)
    rows = await Attachment.raw(
        "SELECT object_key, mime, filename FROM attachments "
        "WHERE status = 'ready' ORDER BY created_at DESC"
    )
    settings = get_settings().reading
    reader = local.TesseractReader()
    seconds: list[float] = []
    confidences: list[float] = []
    probes = limit * PROBE_LIMIT_PER_PAGE
    unreadable = 0
    for row in rows:
        kind = kind_of(row["mime"], row["filename"])
        if kind not in (FileKind.PDF, FileKind.IMAGE):
            continue
        data = await store.get(row["object_key"])
        try:
            total = min(await local.survey(data, kind), settings.pages_per_file)
            for number in range(1, total + 1):
                if len(seconds) >= limit or probes <= 0:
                    break
                probes -= 1
                started = time.monotonic()
                opened = await local.open_page(data, kind, number, dpi=settings.dpi)
                if not isinstance(opened, ScanPage):
                    continue
                reading = await reader.read(opened)
                seconds.append(time.monotonic() - started)
                if reading.confidence is not None:
                    confidences.append(reading.confidence * 100)
        except ReadFailed:
            unreadable += 1
        if len(seconds) >= limit or probes <= 0:
            break
    result: dict[str, object] = {"timed_pages": len(seconds), "unreadable": unreadable}
    if seconds:
        result["seconds_per_page"] = {
            "median": round(statistics.median(seconds), 2),
            "max": round(max(seconds), 2),
        }
    if confidences:
        result["median_confidence"] = round(statistics.median(confidences), 1)
    return result


async def run(argv: list[str], store: ObjectStore | None = None) -> str:
    """Make the report the command line asks for.

    :param argv: the command-line arguments
    :param store: where files' bytes are; the object store by default
    :returns: the report, as one line of JSON
    :raises SystemExit: for a bad argument or an address no person has
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True, help="the person to report on")
    parser.add_argument("--time", type=int, default=0, metavar="N")
    parser.add_argument("--queue", action="store_true")
    args = parser.parse_args(argv)
    if args.time < 0:
        parser.error("--time must not be negative")
    identity = await person_by_email(args.email)
    if identity is None:
        sys.exit("no person has that address")
    principal = Principal(person_id=identity.person_id, email=identity.email)
    async with transaction_scope():
        report: dict[str, object] = await count(principal)
    if args.time:
        async with transaction_scope():
            report |= await time_scans(principal, args.time, store or S3ObjectStore())
    if args.queue:
        async with transaction_scope():
            report["queued"] = await filetext.backfill(principal)
    return json.dumps(report)


async def _main() -> None:
    """Open the database, print the report, and close it."""
    await DB.start_connection_pool()
    try:
        print(await run(sys.argv[1:]))
    finally:
        await DB.close_connection_pool()


if __name__ == "__main__":
    asyncio.run(_main())
