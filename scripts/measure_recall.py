"""Measure how often search finds the engrams and files people ask for.

Reads questions as JSON on stdin::

    [{"id": "q1", "text": "...", "expected": ["<space>/<path>",
      "<space>/file/<key>"], "answer": "..."}]

``answer`` is optional. A question is a file question when any expected
reference holds ``/file/``. Each question is searched with the first five
results, passages on, and nothing written to the search log. Prints numbers
only, never a question, reference or passage::

    {"engrams": {"asked", "found_at_5"},
     "files": {"asked", "found_at_5", "answer_in_passage"},
     "median_ms": ..., "sets": {"name": {"asked", "found_at_5", "median_ms"}}}

A question may name its ``set``. A ``{"session": [question, ...]}`` item
asks its questions in order and passes earlier results to the next search.
History ends with that item. No MCP session or search log is needed.

``answer_in_passage`` counts file questions with an answer where an expected
file came back in the first five and its passage holds the answer, compared
in lower case and without white space, and without the ``**`` that marks matches.

``--file-text on|off`` switches reading on or off for this process only, so
the two runs compare. Run it as that person, in ``reef-app``::

    railway ssh -s reef-app -- uv run --frozen --no-dev \\
      python scripts/measure_recall.py --email PERSON --file-text on \\
      < questions.json

Your own shell reads ``questions.json`` and sends it on stdin, so the
questions are never written to the container's disk.
"""

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from uuid import UUID

from reef.access import Principal, alias_map
from reef.config import get_settings
from reef.db import DB, transaction_scope
from reef.identity import person_by_email
from reef.models import Engram
from reef.search import search_engrams

K = 5
"""How many leading results count as found."""


def parse_questions(text: str) -> list[dict]:
    """Read and check the questions.

    :param text: the JSON
    :returns: the questions, each with ``text`` and a non-empty ``expected``
    :raises SystemExit: when the input is not as described; the message names
        no question
    """
    try:
        questions = json.loads(text)
    except ValueError:
        sys.exit("the questions are not valid JSON")
    if not isinstance(questions, list):
        sys.exit("the questions must be a list")
    for item in questions:
        if not isinstance(item, dict):
            sys.exit("every item must be a question or a session")
        group = item.get("session", [item])
        if not isinstance(group, list) or not group:
            sys.exit("a session must hold at least one question")
        for question in group:
            if (
                not isinstance(question, dict)
                or not isinstance(question.get("text"), str)
                or not isinstance(question.get("expected"), list)
                or not question["expected"]
                or not all(isinstance(ref, str) for ref in question["expected"])
            ):
                sys.exit(
                    "every question needs text and at least one expected reference"
                )
            if "set" in question and (
                not isinstance(question["set"], str) or not question["set"].strip()
            ):
                sys.exit("a set needs a non-empty name")
    return questions


def ref_of(hit: dict) -> str:
    """Write a hit as the reference a question expects.

    :param hit: a search result
    :returns: ``<space>/<path>`` for an engram, ``<space>/file/<key>`` for a file
    """
    if hit["kind"] == "file":
        return f"{hit['cove']}/file/{hit['key']}"
    return f"{hit['cove']}/{hit['path']}"


def squash(text: str) -> str:
    """Lower-case a text and drop its white space.

    :param text: any text
    :returns: the text, compared this way
    """
    return "".join(text.lower().split())


async def measure(principal: Principal, questions: list[dict]) -> dict:
    """Search every question and count what was found.

    :param principal: the person who searches
    :param questions: checked questions
    :returns: the numbers, as the module describes
    """
    engrams = {"asked": 0, "found_at_5": 0}
    files = {"asked": 0, "found_at_5": 0, "answer_in_passage": 0}
    milliseconds: list[float] = []
    sets: dict[str, dict] = {}
    times: dict[str, list[float]] = {}
    for item in questions:
        group = item.get("session", [item])
        earlier: list[list[UUID]] = []
        for index, question in enumerate(group):
            expected = set(question["expected"])
            for_files = any("/file/" in ref for ref in expected)
            started = time.monotonic()
            async with transaction_scope():
                hits = await search_engrams(
                    principal,
                    question["text"],
                    limit=K,
                    log=False,
                    passages=True,
                    earlier=list(earlier),
                )
            elapsed = (time.monotonic() - started) * 1000
            milliseconds.append(elapsed)
            found = [hit for hit in hits[:K] if ref_of(hit) in expected]
            tally = files if for_files else engrams
            tally["asked"] += 1
            tally["found_at_5"] += bool(found)
            if name := question.get("set"):
                counts = sets.setdefault(name, {"asked": 0, "found_at_5": 0})
                counts["asked"] += 1
                counts["found_at_5"] += bool(found)
                times.setdefault(name, []).append(elapsed)
            answer = question.get("answer")
            if for_files and answer:
                wanted = squash(answer)
                files["answer_in_passage"] += any(
                    wanted in squash((hit.get("passage") or "").replace("**", ""))
                    for hit in found
                )
            if index + 1 < len(group):
                # Resolve only for the next turn, outside the search timer.
                earlier.insert(0, await _result_ids(principal, hits[:K]))
    for name, tally in sets.items():
        tally["median_ms"] = round(statistics.median(times[name]), 1)
    return {
        "engrams": engrams,
        "files": files,
        "median_ms": round(statistics.median(milliseconds), 1) if milliseconds else 0,
        "sets": sets,
    }


async def _result_ids(principal: Principal, hits: list[dict]) -> list[UUID]:
    """Resolve returned engram references as the person, preserving their order.

    Search payloads keep their existing shape. Only this session runner
    needs ids, so it reads them separately after timing the search.

    :param principal: the person who searched
    :param hits: that search's returned hits
    :returns: readable engram ids only, best first
    """
    wanted = [(hit["cove"], hit["path"]) for hit in hits if hit["kind"] == "page"]
    if not wanted:
        return []
    async with transaction_scope():
        aliases = await alias_map(principal)
        spaces = [
            space_id
            for space_id, alias in aliases.items()
            if any(alias == space for space, _ in wanted)
        ]
        if not spaces:
            return []
        rows = await Engram.select(Engram.id, Engram.space_id, Engram.path).where(
            Engram.space_id.is_in(spaces),
            Engram.path.is_in([path for _, path in wanted]),
        )
    ids = {(aliases[row["space_id"]], row["path"]): row["id"] for row in rows}
    return [ids[ref] for ref in wanted if ref in ids]


def _lift_value(text: str) -> float:
    """Read a finite, non-negative lift from a flag.

    :param text: the flag value
    :returns: the lift
    :raises argparse.ArgumentTypeError: when the value is outside its bound
    """
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("a lift must be finite and at least zero")
    return value


async def run(argv: list[str], stdin: str) -> str:
    """Parse the arguments and the questions, and measure.

    :param argv: the command-line arguments
    :param stdin: the questions, as JSON
    :returns: the numbers, as one line of JSON
    :raises SystemExit: for a bad argument or an address no person has
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True, help="the person who searches")
    parser.add_argument("--file-text", required=True, choices=["on", "off"])
    parser.add_argument("--priming", choices=["on", "off"])
    parser.add_argument("--spreading", choices=["on", "off"])
    parser.add_argument("--priming-lift", type=_lift_value)
    parser.add_argument("--spreading-lift", type=_lift_value)
    args = parser.parse_args(argv)
    questions = parse_questions(stdin)
    identity = await person_by_email(args.email)
    if identity is None:
        sys.exit("no person has that address")
    principal = Principal(person_id=identity.person_id, email=identity.email)
    settings = get_settings()
    before = settings.reading.enabled
    overrides = {
        name: value == "on" if name in {"priming", "spreading"} else value
        for name in ("priming", "spreading", "priming_lift", "spreading_lift")
        if (value := getattr(args, name)) is not None
    }
    previous = {name: getattr(settings.search, name) for name in overrides}
    settings.reading.enabled = args.file_text == "on"
    for name, value in overrides.items():
        setattr(settings.search, name, value)
    try:
        return json.dumps(await measure(principal, questions))
    finally:
        settings.reading.enabled = before
        for name, value in previous.items():
            setattr(settings.search, name, value)


async def _main() -> None:
    """Open the database, print the numbers, and close it."""
    text = sys.stdin.read()
    await DB.start_connection_pool()
    try:
        print(await run(sys.argv[1:], text))
    finally:
        await DB.close_connection_pool()


if __name__ == "__main__":
    asyncio.run(_main())
