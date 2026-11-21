"""The operator's file report: counts and timings, never a name or a text.

These tests manage their own transactions: the report opens its own, and has
to see committed rows. Files are kept in a :class:`conftest.MemoryStore`.
"""

import json
import sys
from pathlib import Path

import pytest
from conftest import MemoryStore, caller_for, store_as
from filefixtures import scan_image, text_pdf

from reef import filetext
from reef.access import Principal, arm
from reef.config import get_settings
from reef.db import transaction_scope
from reef.models import FilePage

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import file_report

FILENAME = "ZEBRAQUILT-polis.pdf"
DESCRIPTION = "ZEBRAQUILT car insurance"


@pytest.fixture(autouse=True)
def reading(monkeypatch):
    """Keep files in memory, and leave embedding off.

    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the reading settings
    """
    settings = get_settings()
    monkeypatch.setattr(settings.embedding, "url", "")
    monkeypatch.setattr(filetext, "S3ObjectStore", MemoryStore)
    MemoryStore.objects.clear()
    return settings.reading


async def test_the_report_counts_without_names(household):
    me = caller_for(household["wouter"])
    await store_as(
        me, "personal", FILENAME, text_pdf(["page one"]), "application/pdf", DESCRIPTION
    )
    await store_as(
        me,
        "personal",
        "ZEBRAQUILT.png",
        scan_image(["Polisnummer 4711"], "PNG"),
        "image/png",
        DESCRIPTION,
    )
    out = await file_report.run(["--email", household["wouter"].email])
    report = json.loads(out)
    assert report["files"] == {"pdf": 1, "image": 1}
    assert "ZEBRAQUILT" not in out
    assert household["wouter"].email not in out
    assert set(report) == {"files", "pages"}


async def test_the_report_times_pages_and_stores_nothing(household, seed):
    me = caller_for(household["wouter"])
    await store_as(
        me,
        "personal",
        "foto.png",
        scan_image(["Polisnummer 4711", "Verzekering van de woning"], "PNG"),
        "image/png",
        DESCRIPTION,
    )
    out = await file_report.run(
        ["--email", household["wouter"].email, "--time", "1"], store=MemoryStore()
    )
    report = json.loads(out)
    assert report["timed_pages"] == 1
    seconds = report["seconds_per_page"]
    assert 0 < seconds["median"] <= seconds["max"]
    assert 0 <= report["median_confidence"] <= 100
    assert "4711" not in out
    assert await seed.fetchval("SELECT count(*) FROM file_pages") == 0


async def test_timing_skips_a_file_it_cannot_read_and_counts_it(household):
    """A broken file is counted and passed over; the next file is still timed."""
    me = caller_for(household["wouter"])
    await store_as(
        me,
        "personal",
        "foto.png",
        scan_image(["Polisnummer 4711"], "PNG"),
        "image/png",
        DESCRIPTION,
    )
    # Stored last, so it is the first the report tries.
    await store_as(
        me, "personal", FILENAME, b"%PDF-1.7 garbage", "application/pdf", DESCRIPTION
    )
    out = await file_report.run(
        ["--email", household["wouter"].email, "--time", "1"], store=MemoryStore()
    )
    report = json.loads(out)
    assert report["unreadable"] == 1
    assert report["timed_pages"] == 1
    assert "ZEBRAQUILT" not in out


async def test_queueing_adds_the_jobs_it_made(household, monkeypatch):
    me = caller_for(household["wouter"])
    monkeypatch.setattr(get_settings().reading, "enabled", False)
    await store_as(me, "personal", "a.pdf", text_pdf(["x"]), "application/pdf", "A")
    monkeypatch.setattr(get_settings().reading, "enabled", True)
    out = await file_report.run(["--email", household["wouter"].email, "--queue"])
    assert json.loads(out)["queued"] == 1
    async with transaction_scope():
        await arm(Principal(person_id=household["wouter"].id, email="x"))
        assert await FilePage.count() == 0


async def test_an_unknown_address_is_refused_without_repeating_it():
    with pytest.raises(SystemExit) as refused:
        await file_report.run(["--email", "nobody-ZEBRAQUILT@example.test"])
    assert "ZEBRAQUILT" not in str(refused.value)
