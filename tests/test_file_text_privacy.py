"""Nothing a file holds leaves Reef: not in a log, a span or a request.

A file's text, name and description are private. These tests store a file of
every kind with one plain marker in all three, run the whole path (store,
read, embed, recall, open) and look for the marker where it must never be:
every log record, and every span Logfire would export. They also pin which
services the worker may talk to.

The marker is a plain word on purpose: Logfire scrubs values that say
"secret" for that alone, and a probe it scrubs on those grounds proves
nothing about our own patterns.
"""

import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from conftest import MemoryStore, SpanCapture, caller_for, store_as
from filefixtures import (
    docx_bytes,
    html_page,
    pptx_bytes,
    scan_image,
    scan_pdf,
    text_pdf,
    xlsx_bytes,
)
from test_embedding import FakeEmbedder

from reef import embedding, filetext, telemetry, worker
from reef.config import get_settings
from reef.db import transaction_scope
from reef.embedding import HttpEmbedder
from reef.jobs import BACKFILL_FILES, EMBED_FILE, READ_FILE
from reef.methods import find
from reef.worker import drain

MARKER = "ZEBRAQUILT"
OFFICE = "application/vnd.openxmlformats-officedocument"

#: Each kind of file: its bytes, the type claimed for it and its extension.
KINDS: dict[str, Callable[[], tuple[bytes, str, str]]] = {
    "text-pdf": lambda: (text_pdf([f"{MARKER} on the page"]), "application/pdf", "pdf"),
    "scan-pdf": lambda: (scan_pdf([MARKER]), "application/pdf", "pdf"),
    "jpeg": lambda: (scan_image([MARKER], "JPEG"), "image/jpeg", "jpg"),
    "markdown": lambda: (f"# {MARKER}\n\nWords.".encode(), "text/markdown", "md"),
    "html": lambda: (html_page(f"<p>{MARKER} words</p>"), "text/html", "html"),
    "docx": lambda: (
        docx_bytes([f"{MARKER} words"]),
        f"{OFFICE}.wordprocessingml.document",
        "docx",
    ),
    "xlsx": lambda: (
        xlsx_bytes({"Sheet": [[MARKER, "words"]]}),
        f"{OFFICE}.spreadsheetml.sheet",
        "xlsx",
    ),
    "pptx": lambda: (
        pptx_bytes([[f"{MARKER} words"]]),
        f"{OFFICE}.presentationml.presentation",
        "pptx",
    ),
}


@pytest.fixture(autouse=True)
def reading(monkeypatch) -> FakeEmbedder:
    """Switch reading on, keep files in memory and embed with a stand-in.

    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the stand-in embedding service
    """
    fake = FakeEmbedder()
    settings = get_settings()
    monkeypatch.setattr(settings.reading, "enabled", True)
    monkeypatch.setattr(settings.embedding, "url", "http://embedder.internal")
    monkeypatch.setattr(settings.embedding, "model", fake.model_id)
    monkeypatch.setattr(settings.embedding, "delay_seconds", 0.0)
    monkeypatch.setattr(embedding, "get_embedder", lambda: fake)
    monkeypatch.setattr(filetext, "S3ObjectStore", MemoryStore)
    monkeypatch.setattr(find, "S3ObjectStore", MemoryStore)
    MemoryStore.objects.clear()
    return fake


@pytest.fixture
def live(captured: SpanCapture, monkeypatch) -> SpanCapture:
    """Make telemetry believe it is configured, exporting to memory.

    :param captured: the in-memory exporter
    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the exporter holding whatever was exported
    """
    monkeypatch.setattr(telemetry, "_configured", True)
    return captured


@pytest.fixture
def forwarded():
    """Forward the worker's log to Logfire, as the worker process does.

    :returns: nothing; the forwarding is undone afterwards
    """
    level = worker.log.level
    worker.log.setLevel(logging.INFO)
    telemetry.forward_logs(worker.log)
    yield
    worker.log.handlers.clear()
    worker.log.setLevel(level)


async def _drain_all() -> int:
    """Run jobs until none is ready, since each may queue the next.

    :returns: how many jobs ran
    """
    total = 0
    while ran := await drain("test"):
        total += ran
    return total


def _everything(sink: SpanCapture) -> str:
    """Flatten the names, attributes and events of every span into one string.

    :param sink: the capturing exporter
    :returns: all of it, as text
    """
    return "".join(
        f"{span.name}{dict(span.attributes or {})}"
        + "".join(
            f"{event.name}{dict(event.attributes or {})}" for event in span.events
        )
        for span in sink.spans
    )


@pytest.mark.parametrize("kind", list(KINDS))
async def test_a_marker_word_reaches_no_log_or_span(
    household, live, forwarded, caplog, kind
):
    data, mime, extension = KINDS[kind]()
    me = caller_for(household["wouter"])
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="reef")

    file = await store_as(
        me, "personal", f"{MARKER}.{extension}", data, mime, f"{MARKER} policy"
    )
    assert await _drain_all() >= 2  # read, then embed
    [hit, *_] = await find.recall(me, "", MARKER)
    [opened] = await find.open(me, "", [hit["ref"]])

    # The probe is live: the file was read, and the tools do carry the marker.
    assert opened["text_status"] == "read"
    assert MARKER in opened["filename"]
    if kind != "scan-pdf" and kind != "jpeg":
        assert MARKER in opened["text"], file.id
    assert live.spans, "no span was exported"
    assert MARKER not in caplog.text
    assert MARKER not in _everything(live)


def test_file_job_spans_keep_their_kind(live, forwarded, caplog):
    from uuid import uuid4

    caplog.set_level(logging.INFO)
    for kind in (READ_FILE, EMBED_FILE, BACKFILL_FILES):
        with telemetry.job_span(uuid4(), kind, 1) as result:
            result["outcome"] = "done"
    kinds = [dict(span.attributes)["kind"] for span in live.spans]
    assert kinds == [READ_FILE, EMBED_FILE, BACKFILL_FILES]
    assert "Scrubbed" not in _everything(live)

    worker.log.info("job %s (%s) done", uuid4(), READ_FILE)
    worker.log.info("read_file: %d pages (layer %d) %s in %.1f s", 1, 1, "read", 0.1)
    logged = _everything(live)
    assert "read_file: 1 pages (layer 1) read in 0.1 s" in logged
    assert f"({READ_FILE}) done" in logged
    assert "Scrubbed" not in logged


async def test_a_job_that_reads_a_file_keeps_its_kind_in_the_span(
    household, live, forwarded
):
    data, mime, extension = KINDS["markdown"]()
    me = caller_for(household["wouter"])
    await store_as(me, "personal", f"{MARKER}.{extension}", data, mime, "A policy")
    await _drain_all()
    kinds = {dict(span.attributes).get("kind") for span in live.spans}
    assert {READ_FILE, EMBED_FILE} <= kinds
    assert "read_file: 1 pages" in _everything(live)


@pytest.fixture
def requests(monkeypatch) -> list[httpx.URL]:
    """Record every request an httpx client sends, and answer a vector for each.

    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the addresses asked, in order
    """
    sent: list[httpx.URL] = []

    async def send(self: Any, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        """Note the request, and answer as an embedding service would.

        :param request: what the client wants to send
        :returns: one vector per input
        """
        import json

        sent.append(request.url)
        inputs = json.loads(request.content)["input"]
        rows = [{"index": i, "embedding": [1.0, 0.0, 0.0]} for i in range(len(inputs))]
        return httpx.Response(200, json={"data": rows}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return sent


async def test_reading_and_embedding_reach_only_their_own_services(
    household, requests, monkeypatch
):
    me = caller_for(household["wouter"])
    for name, make in KINDS.items():
        data, mime, extension = make()
        await store_as(me, "personal", f"{name}.{extension}", data, mime, "A policy")

    # Reading every kind sends nothing, with embedding left to wait.
    monkeypatch.setattr(embedding, "get_embedder", lambda: None)
    assert await drain("test") >= len(KINDS)
    assert requests == []

    # Embedding with the real client sends only to the embedding service.
    url = "http://embedder.internal"
    monkeypatch.setattr(embedding, "get_embedder", lambda: HttpEmbedder(url, "fake-1"))
    async with transaction_scope():
        await filetext.backfill(me.principal)
    assert await _drain_all() > 0
    assert requests
    assert {(u.scheme, u.host) for u in requests} == {("http", "embedder.internal")}
