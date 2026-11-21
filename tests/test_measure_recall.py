"""The recall measurement: numbers only, and no trace in the search log.

These tests manage their own transactions: the script opens its own, and has
to see committed rows.
"""

import json
import sys
from pathlib import Path

import pytest
from test_search_files import _file

from reef.access import Principal
from reef.config import get_settings
from reef.db import transaction_scope
from reef.engrams import save_engram

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import measure_recall

MARKER = "ZEBRAQUILT"


async def test_measuring_prints_numbers_only_and_logs_no_search(
    household, seed, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings.search, "log", True)
    monkeypatch.setattr(settings.embedding, "url", "")
    person = household["wouter"]
    me = Principal(person_id=person.id, email=person.email)
    async with transaction_scope():
        await save_engram(
            me, "personal", "boiler.md", "The Vaillant boiler.", message="x"
        )
        await _file(
            me,
            household["w_personal"],
            "polis.pdf",
            "A scan.",
            ["Dear neighbours", "The xylophone recital starts at eight."],
        )
    questions = [
        {
            "id": "q1",
            "text": f"Vaillant {MARKER}",
            "expected": ["personal/boiler.md"],
        },
        {
            "id": "q2",
            "text": f"xylophone {MARKER}",
            "expected": [f"personal/file/{_key()}"],
            "answer": "starts at eight",
        },
        {"id": "q3", "text": f"nothing {MARKER}", "expected": ["personal/none.md"]},
    ]
    out = await measure_recall.run(
        ["--email", person.email, "--file-text", "on"], json.dumps(questions)
    )
    report = json.loads(out)
    assert report["engrams"] == {"asked": 2, "found_at_5": 1}
    assert report["files"] == {"asked": 1, "found_at_5": 1, "answer_in_passage": 1}
    assert report["median_ms"] >= 0
    for private in (MARKER, "xylophone", "polis", "boiler", "starts at eight"):
        assert private not in out
    assert await seed.fetchval("SELECT count(*) FROM search_log") == 0


async def test_with_file_text_off_a_page_word_finds_no_file(household, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings.embedding, "url", "")
    person = household["wouter"]
    me = Principal(person_id=person.id, email=person.email)
    async with transaction_scope():
        await _file(me, household["w_personal"], "polis.pdf", "A scan.", ["xylophone"])
    questions = [
        {"id": "q", "text": "xylophone", "expected": [f"personal/file/{_key()}"]}
    ]
    out = await measure_recall.run(
        ["--email", person.email, "--file-text", "off"], json.dumps(questions)
    )
    assert json.loads(out)["files"]["found_at_5"] == 0


def _key() -> str:
    """Return the object key `_file` gives its file.

    :returns: the key
    """
    return "files/polis.pdf"


def test_a_question_without_an_expected_ref_is_refused():
    with pytest.raises(SystemExit):
        measure_recall.parse_questions('[{"id": "q", "text": "t", "expected": []}]')
