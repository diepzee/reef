"""Engram embeddings: only what changed, only what the person may write, capped."""

import asyncpg
import pytest

from reef import embedding
from reef.access import Principal, arm
from reef.config import get_settings
from reef.context import build_index
from reef.db import transaction_scope
from reef.engrams import delete_engram, save_engram
from reef.jobs import BACKFILL_EMBEDDINGS, EMBED_ENGRAM
from reef.models import EngramEmbedding, Job
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
    monkeypatch.setattr(settings.embedding, "url", "http://embedder.internal")
    monkeypatch.setattr(settings.embedding, "model", fake.model_id)
    monkeypatch.setattr(settings.embedding, "delay_seconds", 0.0)
    monkeypatch.setattr(embedding, "get_embedder", lambda: fake)
    return fake


async def _stored(principal) -> list[tuple[str | None, str, int]]:
    """Return the vectors the principal can see: anchor, model, length."""
    await arm(principal)
    rows = await EngramEmbedding.raw(
        "SELECT section_anchor, model_id, dims FROM engram_embeddings "
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
    await save_engram(me, "personal", "insurance.md", BODY, message="x")
    await build_index(me)
    assert await _jobs(me, EMBED_ENGRAM) == 0
    assert await _jobs(me, BACKFILL_EMBEDDINGS) == 0
    assert await _stored(me) == []


# --- what is embedded ----------------------------------------------------------


async def test_a_saved_engram_is_embedded_by_the_worker(household, service):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(
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
        await save_engram(me, "personal", "insurance.md", BODY + str(n), message="x")
    assert await _jobs(me, EMBED_ENGRAM) == 1


async def test_unchanged_text_is_never_embedded_again(household, service):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        engram = await save_engram(me, "personal", "insurance.md", BODY, message="x")
    await drain("test")
    sent = len(service.sent)
    async with transaction_scope():
        assert await embedding.embed_engram(me, engram.id, service) == 0
    assert len(service.sent) == sent


async def test_an_edit_embeds_only_the_pieces_that_changed(household, service):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(me, "personal", "insurance.md", BODY, message="x")
    await drain("test")
    service.sent.clear()
    async with transaction_scope():
        await save_engram(me, "personal", "insurance.md", BODY + " more", message="x")
    await drain("test")
    # The whole-engram piece and the section that changed. Not "car".
    assert len(service.sent) == 2
    async with transaction_scope():
        assert len(await _stored(me)) == 3


async def test_a_section_that_is_gone_loses_its_vector(household, service):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(me, "personal", "insurance.md", BODY, message="x")
    await drain("test")
    async with transaction_scope():
        await save_engram(me, "personal", "insurance.md", "Short now.", message="x")
    await drain("test")
    async with transaction_scope():
        assert await _stored(me) == [(None, "fake-1", 3)]


async def test_another_models_vectors_are_replaced(household, service):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        engram = await save_engram(me, "personal", "note.md", "A note.", message="x")
    await drain("test")
    newer = FakeEmbedder(dims=2)
    newer.model_id = "fake-2"
    async with transaction_scope():
        assert await embedding.embed_engram(me, engram.id, newer) == 1
        assert await _stored(me) == [(None, "fake-2", 2)]


async def test_deleting_an_engram_deletes_its_vectors(household, service, seed):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(me, "personal", "insurance.md", BODY, message="x")
    await drain("test")
    async with transaction_scope():
        await delete_engram(me, "personal", "insurance.md")
    assert await seed.fetchval("SELECT count(*) FROM engram_embeddings") == 0


async def test_a_bad_answer_from_the_service_stores_nothing(household, service, seed):
    me = principal_for(household["wouter"])

    async def broken(texts):
        return [[float("nan"), 1.0, 0.5] for _ in texts]

    service.embed = broken
    async with transaction_scope():
        engram = await save_engram(me, "personal", "note.md", "A note.", message="x")
    assert await drain("test") == 1
    assert await seed.fetchval("SELECT count(*) FROM engram_embeddings") == 0
    async with transaction_scope():
        # The text was sent, so it is charged, and the engram is tried later.
        assert await embedding.spent_today(me) == 1
        assert await _jobs(me, EMBED_ENGRAM, "queued") == 1
        assert engram.id is not None


@pytest.mark.parametrize(
    "answer",
    [
        [[True, False, True]],
        [[]],
        [["1", "2", "3"]],
        [[1.0, 2.0], [1.0]],
        [[0.0] * 5000],
        "nonsense",
        [[1.0, float("inf")]],
    ],
)
def test_an_answer_that_is_not_sound_vectors_is_refused(answer):
    count = len(answer) if isinstance(answer, list) else 1
    with pytest.raises(embedding.BadEmbedding):
        embedding._checked(answer, count)


def test_a_response_must_hold_one_vector_for_each_input():
    good = {
        "data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]
    }
    assert embedding._rows(good, 2) == [[1.0], [2.0]]
    for bad in ({"data": [{"index": 0, "embedding": [1.0]}] * 2}, {"oops": 1}, []):
        with pytest.raises(embedding.BadEmbedding):
            embedding._rows(bad, 2)


async def test_a_failing_service_cannot_be_sent_text_without_limit(
    household, service, monkeypatch
):
    """Each failed try is charged, so the cap ends the retries."""
    import httpx

    me = principal_for(household["wouter"])
    monkeypatch.setattr(get_settings().embedding, "daily_cap", 2)
    calls = 0

    async def refusing(texts):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("down")

    service.embed = refusing
    async with transaction_scope():
        engram = await save_engram(me, "personal", "note.md", "A note.", message="x")
    await drain("test")
    for _ in range(4):
        async with transaction_scope():
            await arm(me)
            await Job.raw("UPDATE jobs SET run_after = LOCALTIMESTAMP")
        await drain("test")
    assert calls == 2
    async with transaction_scope():
        assert await embedding.spent_today(me) == 2
        assert engram.id is not None


async def test_one_refused_engram_does_not_undo_the_others(household, service):
    me = principal_for(household["wouter"])
    settings = get_settings()
    url, settings.embedding.url = settings.embedding.url, ""
    try:
        async with transaction_scope():
            await save_engram(me, "personal", "good.md", "Fine.", message="x")
            await save_engram(me, "personal", "bad.md", "REFUSE this.", message="x")
    finally:
        settings.embedding.url = url
    sound = service.embed

    async def picky(texts):
        if any("REFUSE" in text for text in texts):
            raise embedding.BadEmbedding("no")
        return await sound(texts)

    service.embed = picky
    async with transaction_scope():
        await build_index(me)
    await drain("test")
    async with transaction_scope():
        assert await _stored(me) == [(None, "fake-1", 3)]


async def test_an_engram_larger_than_the_whole_cap_is_left_alone(
    household, service, monkeypatch
):
    me = principal_for(household["wouter"])
    monkeypatch.setattr(get_settings().embedding, "daily_cap", 2)
    async with transaction_scope():
        await save_engram(me, "personal", "insurance.md", BODY, message="x")
    await drain("test")
    assert service.sent == []
    async with transaction_scope():
        # Not queued again: it could never fit.
        assert await _jobs(me, EMBED_ENGRAM, "queued") == 0


async def test_an_engram_of_only_white_space_asks_for_nothing(tx, household, service):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "blank.md", "\n\t\n", message="x")
    assert await embedding.request_backfill(me) is False


# --- whose it is ---------------------------------------------------------------


async def test_a_vector_is_visible_exactly_when_its_engram_is(household, service):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    async with transaction_scope():
        await save_engram(me, "personal", "diary.md", "Private.", message="x")
        await save_engram(me, "household", "boiler.md", "Shared.", message="x")
    await drain("test")
    async with transaction_scope():
        assert len(await _stored(me)) == 2
    async with transaction_scope():
        assert len(await _stored(partner)) == 1


async def test_an_engram_the_person_cannot_read_is_not_embedded(household, service):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    async with transaction_scope():
        engram = await save_engram(
            partner, "personal", "diary.md", "Hers.", message="x"
        )
    service.sent.clear()
    async with transaction_scope():
        assert await embedding.embed_engram(me, engram.id, service) == 0
    assert service.sent == []


async def test_a_vector_cannot_be_planted_on_somebody_elses_engram(household, service):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    async with transaction_scope():
        engram = await save_engram(
            partner, "personal", "diary.md", "Hers.", message="x"
        )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with transaction_scope():
            await arm(me)
            await EngramEmbedding.raw(
                "INSERT INTO engram_embeddings "
                "(id, engram_id, model_id, dims, content_hash, embedding) "
                "VALUES (gen_random_uuid(), {}, 'fake-1', 3, 'x', '[1,2,3]'::vector)",
                engram.id,
            )


async def test_a_vector_must_have_the_length_it_says(household, seed):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(me, "personal", "note.md", "A note.", message="x")
    with pytest.raises(asyncpg.CheckViolationError):
        await seed.execute(
            "INSERT INTO engram_embeddings "
            "(id, engram_id, model_id, dims, content_hash, embedding) "
            "SELECT gen_random_uuid(), id, 'fake-1', 4, 'x', '[1,2,3]'::vector "
            "FROM engrams LIMIT 1"
        )


# --- what it costs -------------------------------------------------------------


async def test_the_days_spend_is_counted_per_person(household, service):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    async with transaction_scope():
        await save_engram(me, "personal", "insurance.md", BODY, message="x")
    await drain("test")
    async with transaction_scope():
        assert await embedding.spent_today(me) == 3
    async with transaction_scope():
        assert await embedding.spent_today(partner) == 0


async def test_an_engram_over_the_cap_waits_whole_and_keeps_its_old_vectors(
    household, service, monkeypatch
):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(me, "personal", "insurance.md", BODY, message="x")
    await drain("test")
    monkeypatch.setattr(get_settings().embedding, "daily_cap", 4)
    service.sent.clear()
    async with transaction_scope():
        await save_engram(me, "personal", "insurance.md", BODY + " more", message="x")
    await drain("test")
    assert service.sent == []
    async with transaction_scope():
        assert len(await _stored(me)) == 3
        assert await embedding.spent_today(me) == 3
        # Queued again for later, not lost.
        assert await _jobs(me, EMBED_ENGRAM, "queued") == 1


# --- old engrams -----------------------------------------------------------------


async def test_loading_the_index_backfills_engrams_without_vectors(
    household, service, monkeypatch
):
    me = principal_for(household["wouter"])
    settings = get_settings()
    monkeypatch.setattr(settings.embedding, "url", "")
    async with transaction_scope():
        await save_engram(me, "personal", "old.md", "Written before.", message="x")
    monkeypatch.setattr(settings.embedding, "url", "http://embedder.internal")
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
