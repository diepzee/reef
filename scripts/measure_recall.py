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
     "median_ms": ...}

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
import statistics
import sys
import time

from reef.access import Principal
from reef.config import get_settings
from reef.db import DB, transaction_scope
from reef.identity import person_by_email
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
    for question in questions:
        if (
            not isinstance(question, dict)
            or not isinstance(question.get("text"), str)
            or not isinstance(question.get("expected"), list)
            or not question["expected"]
        ):
            sys.exit("every question needs text and at least one expected reference")
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
    for question in questions:
        expected = set(question["expected"])
        for_files = any("/file/" in ref for ref in expected)
        started = time.monotonic()
        async with transaction_scope():
            hits = await search_engrams(
                principal, question["text"], limit=K, log=False, passages=True
            )
        milliseconds.append((time.monotonic() - started) * 1000)
        found = [hit for hit in hits[:K] if ref_of(hit) in expected]
        tally = files if for_files else engrams
        tally["asked"] += 1
        tally["found_at_5"] += bool(found)
        answer = question.get("answer")
        if for_files and answer:
            wanted = squash(answer)
            files["answer_in_passage"] += any(
                wanted in squash((hit.get("passage") or "").replace("**", ""))
                for hit in found
            )
    return {
        "engrams": engrams,
        "files": files,
        "median_ms": round(statistics.median(milliseconds), 1) if milliseconds else 0,
    }


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
    args = parser.parse_args(argv)
    questions = parse_questions(stdin)
    identity = await person_by_email(args.email)
    if identity is None:
        sys.exit("no person has that address")
    principal = Principal(person_id=identity.person_id, email=identity.email)
    settings = get_settings().reading
    before = settings.enabled
    settings.enabled = args.file_text == "on"
    try:
        return json.dumps(await measure(principal, questions))
    finally:
        settings.enabled = before


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
