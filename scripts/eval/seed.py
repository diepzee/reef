"""Load the benchmark into the local database, as the eval person's pages.

Run as: ``python scripts/eval/seed.py [--questions N] [--write-slice PATH]``

With ``--private`` it loads your own export instead, as a second local
person. See "The private set" in the README.

Never in production: see :func:`evalkit.refuse_unless_local`.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import evalkit

from reef.config import get_settings
from reef.db import DB

DEFAULT_QUESTIONS = 100

#: The slice kept in the repository: a few questions of each type, each with
#: its evidence and a couple of sessions without, so it stays small.
SLICE_PER_KIND = 4
SLICE_DISTRACTORS = 2


def superuser_dsn(app_dsn: str) -> str:
    """Return the connection string that creates the eval person.

    Defaults to the superuser the local ``docker-compose.yml`` creates, on
    the same database as the app. Override with ``REEF_SEED_DATABASE_URL``.

    :param app_dsn: the app's own connection string
    :returns: a connection string for the superuser
    """
    override = os.environ.get("REEF_SEED_DATABASE_URL")
    if override:
        return override
    parts = urlsplit(app_dsn)
    host = parts.hostname or "localhost"
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit(parts._replace(netloc=f"postgres:postgres@{host}{port}"))


def _progress(done: int, total: int) -> None:
    """Print how far the seeding is.

    :param done: pages written so far
    :param total: pages to write
    """
    print(f"\r  {done}/{total} pages", end="", flush=True)


async def seed(count: int | None) -> None:
    """Create the eval person and load the chosen questions' sessions.

    :param count: how many questions to load, or None for all
    """
    app_dsn = get_settings().dsn
    seed_dsn = superuser_dsn(app_dsn)
    evalkit.refuse_unless_local(app_dsn, seed_dsn)
    chosen = evalkit.choose(evalkit.load_benchmark(), count)
    eval_set = evalkit.convert(chosen)
    print(f"{len(eval_set.questions)} questions, {len(eval_set.pages)} pages")
    await DB.start_connection_pool()
    try:
        principal = await evalkit.ensure_eval_person(seed_dsn)
        written = await evalkit.seed_pages(
            principal, eval_set.pages, progress=_progress
        )
    finally:
        await DB.close_connection_pool()
    print(
        f"\nwrote {written} pages; {len(eval_set.pages) - written} were there already"
    )


async def seed_private(export_path: Path) -> None:
    """Load your own export as the private eval person's pages.

    :param export_path: the JSON export downloaded from the site
    :raises FileNotFoundError: if the export is not there
    """
    app_dsn = get_settings().dsn
    seed_dsn = superuser_dsn(app_dsn)
    evalkit.refuse_unless_local(app_dsn, seed_dsn)
    if not export_path.exists():
        raise FileNotFoundError(
            f"{export_path} is missing. Export your pages as JSON on the site "
            "and save the file there."
        )
    pages = evalkit.private_pages(json.loads(export_path.read_text()))
    print(f"{len(pages)} pages in the export")
    await DB.start_connection_pool()
    try:
        principal = await evalkit.ensure_eval_person(
            seed_dsn, evalkit.PRIVATE_EMAIL, "Private eval"
        )
        written = await evalkit.seed_pages(
            principal, pages, progress=_progress, tag="private"
        )
    finally:
        await DB.close_connection_pool()
    print(f"\nwrote {written} pages; {len(pages) - written} were there already")


def _evidence_size(record: dict) -> int:
    """Count the characters in a question's evidence sessions.

    :param record: one question as the benchmark file holds it
    :returns: the total length of its evidence
    """
    return sum(
        len(turn["content"])
        for session_id, session in zip(
            record["haystack_session_ids"], record["haystack_sessions"], strict=True
        )
        if session_id in record["answer_session_ids"]
        for turn in session
    )


def write_slice(path: Path) -> None:
    """Write the small slice that the test suite runs.

    Takes the same number of questions of each type, and of each type the
    ones with the least evidence text, so the file stays small. That makes
    the slice easier than the benchmark. It is there to catch a regression,
    not to measure quality.

    :param path: where to write it
    """
    by_kind: dict[str, list[dict]] = {}
    for record in evalkit.choose(evalkit.load_benchmark(), None):
        by_kind.setdefault(record["question_type"], []).append(record)
    chosen = [
        record
        for kind in sorted(by_kind)
        for record in sorted(
            by_kind[kind], key=lambda r: (_evidence_size(r), r["question_id"])
        )[:SLICE_PER_KIND]
    ]
    eval_set = evalkit.convert(chosen, distractors=SLICE_DISTRACTORS)
    path.write_text(eval_set.to_json() + "\n")
    print(f"{path}: {len(eval_set.questions)} questions, {len(eval_set.pages)} pages")


def main() -> None:
    """Parse the command line and run."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--questions",
        default=str(DEFAULT_QUESTIONS),
        help=f"how many questions to load, or 'all' (default {DEFAULT_QUESTIONS})",
    )
    parser.add_argument(
        "--write-slice", type=Path, help="write the test slice and stop"
    )
    parser.add_argument(
        "--private",
        nargs="?",
        type=Path,
        const=evalkit.PRIVATE_EXPORT,
        help=f"load your own JSON export instead (default {evalkit.PRIVATE_EXPORT})",
    )
    args = parser.parse_args()
    if args.write_slice:
        write_slice(args.write_slice)
        return
    count = None if args.questions == "all" else int(args.questions)
    try:
        asyncio.run(seed_private(args.private) if args.private else seed(count))
    except (evalkit.NotLocal, FileNotFoundError, ValueError) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
