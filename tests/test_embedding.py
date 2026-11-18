"""Page embeddings: only what changed, only what the person may write, capped."""

import asyncpg
import pytest

from reef import embedding
from reef.access import Principal, arm
from reef.config import get_settings
from reef.context import build_index
from reef.db import transaction_scope
from reef.jobs import BACKFILL_EMBEDDINGS, EMBED_PAGE
from reef.models import Job, PageEmbedding
from reef.pages import delete_page, save_page
from reef.worker import drain

LONG = "word " * 100
BODY = "\n\n".join(["## Car", LONG * 3, "## House", LONG * 3])


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


class FakeEmbedder:
    """Stands in for the embedding service and remembers what it was sent."""

    model_id = "fake-1"

    def __init__(self, dims: int = 3) -> None:
        self.dims = dims
        self.sent: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.sent.extend(texts)
        return [[float(len(text) % 7), 1.0, 0.5][: self.dims] for text in texts]


@pytest.fixture
def service(monkeypatch):
    """Configure an embedding service, and answer for it."""
    fake = FakeEmbedder()
    settings = get_settings()
    monkeypatch.setattr(settings, "embedding_url", "http://embedder.internal")
    monkeypatch.setattr(settings, "embedding_model", fake.model_id)
    monkeypatch.setattr(settings, "embedding_delay_seconds", 0.0)
    monkeypatch.setattr(embedding, "get_embedder", lambda: fake)
    return fake


async def _stored(principal) -> list[tuple[str | None, str, int]]:
    """Return the vectors the principal can see: anchor, model, length."""
    await arm(principal)
    rows = await PageEmbedding.raw(
        "SELECT section_anchor, model_id, dims FROM page_embeddings "
        "ORDER BY section_anchor NULLS FIRST"
    )
    return [(row["section_anchor"], row["model_id"], row["dims"]) for row in rows]


async def _jobs(principal, kind: str, status: str | None = None) -> int:
    """Count the principal's jobs of one kind, optionally in one state."""
    await arm(principal)
    query = Job.count().where(Job.kind == kind)
    return await (query.where(Job.status == status) if status else query)


# --- no service, no embedding --------------------------------------------------


async def test_without_a_service_nothing_is_queued_or_stored(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "insurance.md", BODY, message="x")
    await build_index(me)
    assert await _jobs(me, EMBED_PAGE) == 0
    assert await _jobs(me, BACKFILL_EMBEDDINGS) == 0
    assert await _stored(me) == []


# --- what is embedded ----------------------------------------------------------


async def test_a_saved_page_is_embedded_by_the_worker(household, service):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_page(
            me, "personal", "insurance.md", BODY, message="x", title="Insurance"
        )
    assert await drain("test") == 1
    async with transaction_scope():
        assert await _stored(me) == [
            (None, "fake-1", 3),
            ("car", "fake-1", 3),
            ("house", "fake-1", 3),
        ]
    # The context goes in front of the text that is sent.
    assert any(text.startswith("Insurance\n") for text in service.sent)


async def test_saving_five_times_queues_one_job(tx, household, service):
    me = principal_for(household["wouter"])
    for n in range(5):
        await save_page(me, "personal", "insurance.md", BODY + str(n), message="x")
    assert await _jobs(me, EMBED_PAGE) == 1


async def test_unchanged_text_is_never_embedded_again(household, service):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        page = await save_page(me, "personal", "insurance.md", BODY, message="x")
    await drain("test")
    sent = len(service.sent)
    async with transaction_scope():
        assert await embedding.embed_page(me, page.id, service) == 0
    assert len(service.sent) == sent


async def test_an_edit_embeds_only_the_pieces_that_changed(household, service):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_page(me, "personal", "insurance.md", BODY, message="x")
    await drain("test")
    service.sent.clear()
    async with transaction_scope():
        await save_page(me, "personal", "insurance.md", BODY + " more", message="x")
    await drain("test")
    # The whole-page piece and the section that changed. Not "car".
    assert len(service.sent) == 2
    async with transaction_scope():
        assert len(await _stored(me)) == 3


async def test_a_section_that_is_gone_loses_its_vector(household, service):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_page(me, "personal", "insurance.md", BODY, message="x")
    await drain("test")
    async with transaction_scope():
        await save_page(me, "personal", "insurance.md", "Short now.", message="x")
    await drain("test")
    async with transaction_scope():
        assert await _stored(me) == [(None, "fake-1", 3)]


async def test_another_models_vectors_are_replaced(household, service):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        page = await save_page(me, "personal", "note.md", "A note.", message="x")
    await drain("test")
    newer = FakeEmbedder(dims=2)
    newer.model_id = "fake-2"
    async with transaction_scope():
        assert await embedding.embed_page(me, page.id, newer) == 1
        assert await _stored(me) == [(None, "fake-2", 2)]


async def test_deleting_a_page_deletes_its_vectors(household, service, seed):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_page(me, "personal", "insurance.md", BODY, message="x")
    await drain("test")
    async with transaction_scope():
        await delete_page(me, "personal", "insurance.md")
    assert await seed.fetchval("SELECT count(*) FROM page_embeddings") == 0


async def test_a_bad_answer_from_the_service_stores_nothing(household, service, seed):
    me = principal_for(household["wouter"])

    async def broken(texts):
        return [[float("nan"), 1.0, 0.5] for _ in texts]

    service.embed = broken
    async with transaction_scope():
        page = await save_page(me, "personal", "note.md", "A note.", message="x")
    with pytest.raises(embedding.BadEmbedding):
        async with transaction_scope():
            await embedding.embed_page(me, page.id, service)
    assert await seed.fetchval("SELECT count(*) FROM page_embeddings") == 0
    assert await seed.fetchval("SELECT count(*) FROM embedding_spend") == 0


# --- whose it is ---------------------------------------------------------------


async def test_a_vector_is_visible_exactly_when_its_page_is(household, service):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    async with transaction_scope():
        await save_page(me, "personal", "diary.md", "Private.", message="x")
        await save_page(me, "household", "boiler.md", "Shared.", message="x")
    await drain("test")
    async with transaction_scope():
        assert len(await _stored(me)) == 2
    async with transaction_scope():
        assert len(await _stored(partner)) == 1


async def test_a_page_the_person_cannot_read_is_not_embedded(household, service):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    async with transaction_scope():
        page = await save_page(partner, "personal", "diary.md", "Hers.", message="x")
    service.sent.clear()
    async with transaction_scope():
        assert await embedding.embed_page(me, page.id, service) == 0
    assert service.sent == []


async def test_a_vector_cannot_be_planted_on_somebody_elses_page(household, service):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    async with transaction_scope():
        page = await save_page(partner, "personal", "diary.md", "Hers.", message="x")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with transaction_scope():
            await arm(me)
            await PageEmbedding.raw(
                "INSERT INTO page_embeddings "
                "(id, page_id, model_id, dims, content_hash, embedding) "
                "VALUES (gen_random_uuid(), {}, 'fake-1', 3, 'x', '[1,2,3]'::vector)",
                page.id,
            )


async def test_a_vector_must_have_the_length_it_says(household, seed):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_page(me, "personal", "note.md", "A note.", message="x")
    with pytest.raises(asyncpg.CheckViolationError):
        await seed.execute(
            "INSERT INTO page_embeddings "
            "(id, page_id, model_id, dims, content_hash, embedding) "
            "SELECT gen_random_uuid(), id, 'fake-1', 4, 'x', '[1,2,3]'::vector "
            "FROM pages LIMIT 1"
        )


# --- what it costs -------------------------------------------------------------


async def test_the_days_spend_is_counted_per_person(household, service):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    async with transaction_scope():
        await save_page(me, "personal", "insurance.md", BODY, message="x")
    await drain("test")
    async with transaction_scope():
        assert await embedding.spent_today(me) == 3
    async with transaction_scope():
        assert await embedding.spent_today(partner) == 0


async def test_a_page_over_the_cap_waits_whole_and_keeps_its_old_vectors(
    household, service, monkeypatch
):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_page(me, "personal", "insurance.md", BODY, message="x")
    await drain("test")
    monkeypatch.setattr(get_settings(), "embedding_daily_cap", 4)
    service.sent.clear()
    async with transaction_scope():
        await save_page(me, "personal", "insurance.md", BODY + " more", message="x")
    await drain("test")
    assert service.sent == []
    async with transaction_scope():
        assert len(await _stored(me)) == 3
        assert await embedding.spent_today(me) == 3
        # Queued again for later, not lost.
        assert await _jobs(me, EMBED_PAGE, "queued") == 1


# --- old pages -----------------------------------------------------------------


async def test_loading_the_index_backfills_pages_without_vectors(
    household, service, monkeypatch
):
    me = principal_for(household["wouter"])
    settings = get_settings()
    monkeypatch.setattr(settings, "embedding_url", "")
    async with transaction_scope():
        await save_page(me, "personal", "old.md", "Written before.", message="x")
    monkeypatch.setattr(settings, "embedding_url", "http://embedder.internal")
    async with transaction_scope():
        await build_index(me)
        assert await _jobs(me, BACKFILL_EMBEDDINGS) == 1
    await drain("test")
    async with transaction_scope():
        assert (None, "fake-1", 3) in await _stored(me)
        await build_index(me)
        # Everything is current now, so it does not ask again.
        assert await _jobs(me, BACKFILL_EMBEDDINGS, "queued") == 0


async def test_the_passage_form_follows_the_model():
    assert embedding.passage("embeddinggemma:300m", "x") == "title: none | text: x"
    assert embedding.passage("qwen3-embedding:0.6b", "x") == "x"
    assert embedding.passage("something-else", "x") == "x"
    assert embedding.question("embeddinggemma:300m", "x").endswith("query: x")
