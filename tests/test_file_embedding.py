"""File embeddings: every file has a vector, changed chunks only, under the cap."""

from datetime import timedelta
from uuid import UUID

import pytest
from conftest import Store
from test_embedding import FakeEmbedder, principal_for

from reef import embedding
from reef.access import Principal, arm
from reef.attachments import add_attachment
from reef.config import get_settings
from reef.db import transaction_scope
from reef.jobs import EMBED_FILE
from reef.models import FileEmbedding, Job
from reef.worker import drain

PAGE = "word " * 120


@pytest.fixture
def service(monkeypatch):
    """Configure an embedding service and the reading switch, and answer for it."""
    fake = FakeEmbedder()
    settings = get_settings()
    monkeypatch.setattr(settings.embedding, "url", "http://embedder.internal")
    monkeypatch.setattr(settings.embedding, "model", fake.model_id)
    monkeypatch.setattr(settings.embedding, "delay_seconds", 0.0)
    monkeypatch.setattr(settings.reading, "enabled", True)
    monkeypatch.setattr(embedding, "get_embedder", lambda: fake)
    return fake


async def _file(principal: Principal, space: str = "personal") -> UUID:
    """Store a ready, described file, as it was stored before reading began.

    Stored with the reading switch off, so storing it queues nothing: these
    tests ask for embedding themselves.

    :param principal: who stores it
    :param space: the person's name for the space
    :returns: the file's id
    """
    reading = get_settings().reading
    enabled, reading.enabled = reading.enabled, False
    try:
        file = await add_attachment(
            principal,
            space,
            b"%PDF",
            "application/pdf",
            filename="policy.pdf",
            description="Car insurance",
            store=Store(),
        )
    finally:
        reading.enabled = enabled
    return file.id


async def _pages(principal: Principal, file: UUID, texts: list[str]) -> None:
    """Store pages for a file, numbered from 1, and mark it read.

    :param principal: who stores them
    :param file: the file's id
    :param texts: the text of each page
    """
    async with transaction_scope():
        await arm(principal)
        for number, text in enumerate(texts, 1):
            await Job.raw(
                "INSERT INTO file_pages (id, attachment_id, number, text, reader) "
                "VALUES (gen_random_uuid(), {}, {}, {}, 'tesseract')",
                file,
                number,
                text,
            )
        await Job.raw("UPDATE attachments SET text_status = 'read' WHERE id = {}", file)


async def _anchors(principal: Principal) -> list[str | None]:
    """Return the chunk anchors of the vectors the principal can see.

    :param principal: who looks
    :returns: the anchors, the whole-file chunk first
    """
    await arm(principal)
    rows = await FileEmbedding.raw(
        "SELECT chunk_anchor FROM file_embeddings ORDER BY chunk_anchor NULLS FIRST"
    )
    return [row["chunk_anchor"] for row in rows]


async def test_every_file_gets_a_vector_for_its_description(household, service):
    me = principal_for(household["wouter"])
    file = await _file(me)
    async with transaction_scope():
        await arm(me)
        await Job.raw("UPDATE attachments SET text_status = 'not_read'")
        assert await embedding.embed_file(me, file, service) == 1
        assert await _anchors(me) == [None]
    assert service.sent[0].endswith("policy.pdf\nCar insurance\n\n")


async def test_a_read_file_gets_a_vector_per_chunk(household, service):
    me = principal_for(household["wouter"])
    file = await _file(me)
    await _pages(me, file, [PAGE, PAGE])
    async with transaction_scope():
        assert await embedding.embed_file(me, file, service) == 3
        assert await _anchors(me) == [None, "p1", "p2"]


async def test_unchanged_chunks_are_not_embedded_again(household, service):
    me = principal_for(household["wouter"])
    file = await _file(me)
    await _pages(me, file, [PAGE, PAGE])
    async with transaction_scope():
        await embedding.embed_file(me, file, service)
        sent = len(service.sent)
        assert await embedding.embed_file(me, file, service) == 0
    assert len(service.sent) == sent


async def test_file_chunks_count_against_the_daily_cap(household, service):
    me = principal_for(household["wouter"])
    file = await _file(me)
    await _pages(me, file, [PAGE, PAGE])
    async with transaction_scope():
        await embedding.embed_file(me, file, service)
        assert await embedding.spent_today(me) == 3


async def test_a_file_over_the_cap_waits(household, service, monkeypatch, seed):
    me = principal_for(household["wouter"])
    file = await _file(me)
    await _pages(me, file, [PAGE, PAGE])
    monkeypatch.setattr(get_settings().embedding, "daily_cap", 4)
    async with transaction_scope():
        await embedding.embed_file(me, file, service)
    async with transaction_scope():
        await embedding.request_file(me, file, delay=timedelta(0))
        await arm(me)
        await Job.raw("UPDATE attachments SET description = 'Other words'")
    assert await drain("test") == 1
    async with transaction_scope():
        await arm(me)
        waiting = await Job.raw(
            "SELECT run_after > LOCALTIMESTAMP + interval '1 hour' AS later "
            "FROM jobs WHERE kind = {} AND status = 'queued'",
            EMBED_FILE,
        )
    assert [row["later"] for row in waiting] == [True]


async def test_a_viewer_cannot_embed_a_file(household, graph, service):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    file = await _file(me, "household")
    await graph.set_role(household["partner"], household["shared"], "viewer")
    async with transaction_scope():
        assert await embedding.embed_file(partner, file, service) == 0
        assert await _anchors(partner) == []
    assert service.sent == []


async def test_nothing_is_asked_for_with_the_switch_off(
    household, service, monkeypatch
):
    me = principal_for(household["wouter"])
    file = await _file(me)
    monkeypatch.setattr(get_settings().reading, "enabled", False)
    async with transaction_scope():
        assert await embedding.request_file(me, file) is False
        await arm(me)
        assert await Job.count().where(Job.kind == EMBED_FILE) == 0


async def test_one_job_waits_for_a_file(household, service):
    me = principal_for(household["wouter"])
    file = await _file(me)
    async with transaction_scope():
        assert await embedding.request_file(me, file) is True
        assert await embedding.request_file(me, file) is False


async def test_the_worker_embeds_a_file_and_ignores_it_with_the_switch_off(
    household, service, monkeypatch
):
    me = principal_for(household["wouter"])
    file = await _file(me)
    async with transaction_scope():
        await embedding.request_file(me, file)
    monkeypatch.setattr(get_settings().reading, "enabled", False)
    assert await drain("test") == 1
    async with transaction_scope():
        assert await _anchors(me) == []
    async with transaction_scope():
        await embedding.request_file(me, file)  # off: no job
    monkeypatch.setattr(get_settings().reading, "enabled", True)
    async with transaction_scope():
        await embedding.request_file(me, file)
    assert await drain("test") == 1
    async with transaction_scope():
        assert await _anchors(me) == [None]


async def test_files_lacking_lists_final_files_without_a_whole_file_vector(
    household, service
):
    me = principal_for(household["wouter"])
    waiting, done = await _file(me), await _file(me)
    async with transaction_scope():
        await arm(me)
        await Job.raw(
            "UPDATE attachments SET text_status = 'waiting' WHERE id = {}", waiting
        )
        await Job.raw(
            "UPDATE attachments SET text_status = 'unreadable' WHERE id = {}", done
        )
        assert await embedding.files_lacking(me) == [done]
        await embedding.embed_file(me, done, service)
        assert await embedding.files_lacking(me) == []
