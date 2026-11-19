"""Hybrid search: pages found by meaning, merged with pages found by words."""

from uuid import uuid4

import httpx
import pytest

from reef import embedding, search
from reef.access import Principal, arm
from reef.breaker import Breaker
from reef.config import get_settings
from reef.models import Attachment, AttachmentStatus, SearchLog
from reef.pages import save_page
from reef.search import fuse, search_pages

FUSION_DEPTH = get_settings().search.fusion_depth

#: The fake model knows three subjects. A text about none of them points
#: in a fourth direction.
SUBJECTS = {
    "car": [1.0, 0.0, 0.0, 0.0],
    "vehicle": [1.0, 0.0, 0.0, 0.0],
    "garage": [0.9, 0.1, 0.0, 0.0],
    "house": [0.0, 1.0, 0.0, 0.0],
    "dwelling": [0.0, 1.0, 0.0, 0.0],
    "holiday": [0.0, 0.0, 1.0, 0.0],
}


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


class SubjectEmbedder:
    """Stands in for the embedding service: a text points at its subject."""

    model_id = "fake-subjects"

    def __init__(self) -> None:
        self.calls = 0
        self.fail: Exception | None = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return [self._vector(text.lower()) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        for word, vector in SUBJECTS.items():
            if word in text:
                return vector
        return [0.0, 0.0, 0.0, 1.0]


@pytest.fixture
def service(monkeypatch):
    """Configure an embedding service, and answer for it."""
    fake = SubjectEmbedder()
    settings = get_settings()
    monkeypatch.setattr(settings.embedding, "url", "http://embedder.internal")
    monkeypatch.setattr(settings.embedding, "model", fake.model_id)
    monkeypatch.setattr(embedding, "get_embedder", lambda: fake)
    monkeypatch.setattr(
        embedding,
        "breaker",
        Breaker(
            get_settings().embedding.rest_after_failures,
            get_settings().embedding.rest_seconds,
        ),
    )
    return fake


async def _page(principal, service, cove: str, path: str, body: str) -> None:
    """Save a page and give it its vectors at once."""
    page = await save_page(principal, cove, path, body, message="x")
    await embedding.embed_page(principal, page.id, service)


def _paths(results: list[dict]) -> list[str]:
    return [r["path"] for r in results if r["kind"] == "page"]


# --- found by meaning ----------------------------------------------------------


async def test_a_page_that_shares_no_word_with_the_query_is_found(
    tx, household, service
):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    await _page(me, service, "personal", "home.md", "The dwelling needs paint.")
    results = await search_pages(me, "vehicle")
    assert _paths(results)[0] == "auto.md"
    # A hit without a matching word still carries a snippet of the page.
    assert "garage" in results[0]["snippet"]


async def test_a_page_in_both_rankings_comes_first(tx, household, service):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "words.md", "Notes on the boiler.")
    await _page(me, service, "personal", "meaning.md", "The garage is booked.")
    await _page(me, service, "personal", "both.md", "The boiler in the garage.")
    assert _paths(await search_pages(me, "boiler vehicle"))[0] == "both.md"


async def test_without_vectors_search_is_keyword_search(tx, household, service):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "auto.md", "The garage visit.", message="x")
    assert await search_pages(me, "vehicle") == []
    assert _paths(await search_pages(me, "garage")) == ["auto.md"]


async def test_keyword_search_can_be_asked_for_alone(tx, household, service):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    assert await search_pages(me, "vehicle", semantic=False) == []
    assert service.calls == 1  # the page, not the question


async def test_the_limit_holds(tx, household, service):
    me = principal_for(household["wouter"])
    for n in range(4):
        await _page(me, service, "personal", f"car-{n}.md", f"The car, part {n}.")
    assert len(await search_pages(me, "vehicle", limit=2)) == 2


async def test_a_file_is_still_found_by_its_words(tx, household, service):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    await arm(me)
    await Attachment(
        cove_id=household["w_personal"].id,
        object_key="files/policy",
        filename="policy.pdf",
        mime="application/pdf",
        byte_size=1,
        description="The vehicle insurance policy.",
        status=AttachmentStatus.READY.value,
    ).save()
    results = await search_pages(me, "vehicle")
    assert {r["kind"] for r in results} == {"page", "file"}


# --- exact queries stay exact --------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        '"vehicle tax"',
        "vehicle -garage",
        "vehicle - garage",
        "- garage vehicle",
        "vehicle(-garage)",
        "(-garage) vehicle",
    ],
)
async def test_a_phrase_or_an_exclusion_is_not_widened(tx, household, service, query):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    before = service.calls
    assert await search_pages(me, query) == []
    assert service.calls == before


async def test_a_hyphenated_word_is_not_an_exclusion(tx, household, service):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    assert _paths(await search_pages(me, "vehicle check-up")) == ["auto.md"]


async def test_a_query_of_stop_words_cannot_error(tx, household, service):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    assert _paths(await search_pages(me, "the and of")) == ["auto.md"]


# --- whose pages ---------------------------------------------------------------


async def test_meaning_cannot_reach_another_persons_pages(tx, household, service):
    """The vector query reads the whole table; RLS must be what scopes it."""
    partner = principal_for(household["partner"])
    await _page(partner, service, "personal", "diary.md", "The garage called.")
    assert _paths(await search_pages(partner, "vehicle")) == ["diary.md"]
    me = principal_for(household["wouter"])
    assert await search_pages(me, "vehicle") == []


async def test_a_shared_page_is_found_by_both_members(tx, household, service):
    partner = principal_for(household["partner"])
    await _page(partner, service, "household", "auto.md", "The garage called.")
    me = principal_for(household["wouter"])
    results = await search_pages(me, "vehicle")
    assert [(r["cove"], r["path"]) for r in results] == [("household", "auto.md")]


async def test_scoping_to_one_cove_holds_for_meaning_too(tx, household, service):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "mine.md", "The garage visit is in May.")
    await _page(me, service, "household", "ours.md", "The garage is paid.")
    assert _paths(await search_pages(me, "vehicle", cove="household")) == ["ours.md"]


# --- the service is optional ---------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("down"), TimeoutError(), embedding.BadEmbedding("junk")],
)
async def test_a_failing_service_leaves_keyword_search(tx, household, service, failure):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    service.fail = failure
    assert _paths(await search_pages(me, "garage")) == ["auto.md"]
    assert await search_pages(me, "vehicle") == []


async def test_a_service_that_keeps_failing_is_left_alone(tx, household, service):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    service.fail = httpx.ConnectError("down")
    for _ in range(get_settings().embedding.rest_after_failures):
        await search_pages(me, "vehicle")
    asked = service.calls
    service.fail = None
    assert await search_pages(me, "vehicle") == []
    assert service.calls == asked


async def test_one_failure_does_not_switch_meaning_off(tx, household, service):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    service.fail = httpx.ConnectError("down")
    await search_pages(me, "vehicle")
    service.fail = None
    assert _paths(await search_pages(me, "vehicle")) == ["auto.md"]


async def test_a_refused_question_never_switches_meaning_off(tx, household, service):
    """One person's odd query must not change everybody's search."""
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    request = httpx.Request("POST", "http://embedder.internal/v1/embeddings")
    service.fail = httpx.HTTPStatusError(
        "refused", request=request, response=httpx.Response(400, request=request)
    )
    for _ in range(get_settings().embedding.rest_after_failures + 2):
        assert await search_pages(me, "vehicle") == []
    service.fail = None
    assert _paths(await search_pages(me, "vehicle")) == ["auto.md"]


async def test_after_the_rest_one_search_tries_again(
    tx, household, service, monkeypatch
):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    service.fail = httpx.ConnectError("down")
    for _ in range(get_settings().embedding.rest_after_failures):
        await search_pages(me, "vehicle")
    embedding.breaker.resting_until = 0.0  # the rest is over
    asked = service.calls
    await search_pages(me, "vehicle")
    assert service.calls == asked + 1
    # It failed again, so the others are spared at once.
    await search_pages(me, "vehicle")
    assert service.calls == asked + 1
    embedding.breaker.resting_until = 0.0
    service.fail = None
    assert _paths(await search_pages(me, "vehicle")) == ["auto.md"]
    assert embedding.breaker.failures == 0


@pytest.mark.parametrize(
    "answer",
    [
        ValueError("not json"),
        [[10**400, 1.0, 0.0, 0.0]],
        [[1e300, 1.0, 0.0, 0.0]],
        [[float("nan"), 1.0, 0.0, 0.0]],
        [[0.0, 0.0, 0.0, 0.0]],
        [],
        "junk",
    ],
)
async def test_no_answer_of_the_service_can_fail_a_search(
    tx, household, service, monkeypatch, answer
):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")

    async def odd(texts: list[str]):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(service, "embed", odd)
    assert _paths(await search_pages(me, "garage")) == ["auto.md"]
    assert await search_pages(me, "vehicle") == []


async def test_a_failing_vector_statement_leaves_keyword_search(
    tx, household, service, monkeypatch
):
    """A failed statement must not take the transaction down with it."""
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    monkeypatch.setattr(search, "_SEMANTIC_SQL", "SELECT no_such_column%s {} {} {} {}")
    assert _paths(await search_pages(me, "garage")) == ["auto.md"]


async def test_a_failure_logs_no_query(tx, household, service, caplog):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    service.fail = httpx.ConnectError("xylophone-secret")
    with caplog.at_level("WARNING"):
        await search_pages(me, "xylophone vehicle")
    assert "ConnectError" in caplog.text
    assert "xylophone" not in caplog.text


async def test_a_slow_service_is_not_waited_for(tx, household, service, monkeypatch):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    monkeypatch.setattr(get_settings().embedding, "seconds", 0.01)

    async def slow(texts: list[str]) -> list[list[float]]:
        import asyncio

        await asyncio.sleep(1)
        return [[1.0, 0.0, 0.0, 0.0]]

    monkeypatch.setattr(service, "embed", slow)
    assert _paths(await search_pages(me, "garage")) == ["auto.md"]


async def test_vectors_of_another_model_are_not_compared(
    tx, household, service, monkeypatch
):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    monkeypatch.setattr(service, "model_id", "another-model")
    assert await search_pages(me, "vehicle") == []


async def test_vectors_of_another_length_cannot_error(
    tx, household, service, monkeypatch
):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")

    async def shorter(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0]]

    monkeypatch.setattr(service, "embed", shorter)
    assert await search_pages(me, "vehicle") == []


# --- keyword hits keep their place ---------------------------------------------


async def _many_cars(me, service, count: int = 12) -> None:
    for n in range(count):
        await _page(me, service, "personal", f"car-{n}.md", f"The car, part {n}.")


async def test_a_file_is_not_pushed_out_by_pages_close_in_meaning(
    tx, household, service
):
    me = principal_for(household["wouter"])
    await _many_cars(me, service)
    await arm(me)
    await Attachment(
        cove_id=household["w_personal"].id,
        object_key="files/policy",
        filename="policy.pdf",
        mime="application/pdf",
        byte_size=1,
        description="The vehicle insurance policy.",
        status=AttachmentStatus.READY.value,
    ).save()
    results = await search_pages(me, "vehicle")
    assert results[0]["kind"] == "file"


async def test_a_page_without_vectors_yet_is_not_pushed_out(tx, household, service):
    me = principal_for(household["wouter"])
    await _many_cars(me, service)
    await save_page(me, "personal", "new.md", "Vehicle tax is due.", message="x")
    assert _paths(await search_pages(me, "vehicle"))[0] == "new.md"


async def test_the_page_with_the_exact_words_is_not_pushed_out(tx, household, service):
    """An identifier is found by its letters, whatever the model makes of it."""
    me = principal_for(household["wouter"])
    await _many_cars(me, service)
    await _page(me, service, "personal", "ticket.md", "RUG-35 is about the house.")
    assert _paths(await search_pages(me, "vehicle RUG-35"))[0] == "ticket.md"


async def test_a_file_that_never_landed_stays_out(tx, household, service):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    await arm(me)
    await Attachment(
        cove_id=household["w_personal"].id,
        object_key="files/pending",
        filename="policy.pdf",
        mime="application/pdf",
        byte_size=1,
        description="The vehicle insurance policy.",
        status=AttachmentStatus.PENDING.value,
    ).save()
    assert {r["kind"] for r in await search_pages(me, "vehicle")} == {"page"}


async def test_a_limit_above_the_fusion_depth_holds(tx, household, service):
    me = principal_for(household["wouter"])
    await _many_cars(me, service, 3)
    assert len(await search_pages(me, "vehicle", limit=FUSION_DEPTH + 10)) == 3


async def test_the_search_log_gets_the_merged_pages(
    tx, household, service, monkeypatch
):
    monkeypatch.setattr(get_settings().search, "log", True)
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    await search_pages(me, "vehicle", log=True)
    await arm(me)
    rows = await SearchLog.raw("SELECT page_ids FROM search_log")
    assert len(rows) == 1 and len(rows[0]["page_ids"]) == 1


# --- the fusion itself ---------------------------------------------------------


def test_fusion_puts_a_row_in_both_rankings_first():
    a, b, c = uuid4(), uuid4(), uuid4()
    merged = fuse([("page", a, False), ("page", b, False)], [c, b])
    assert merged[0] == ("page", b)
    assert set(merged) == {("page", a), ("page", b), ("page", c)}


def test_fusion_keeps_a_sure_keyword_hit_above_fifty_close_pages():
    file, pages = uuid4(), [uuid4() for _ in range(FUSION_DEPTH)]
    assert fuse([("file", file, True)], pages)[0] == ("file", file)


def test_fusion_lets_meaning_outrank_an_unsure_keyword_hit():
    loose, close = uuid4(), uuid4()
    assert fuse([("page", loose, False)], [close])[0] == ("page", close)


def test_fusion_judges_a_sure_hit_by_its_real_place_when_it_has_one():
    first, second = uuid4(), uuid4()
    merged = fuse([("page", first, True), ("page", second, True)], [second, first])
    # Closest in meaning wins: a place by words counts for less.
    assert merged == [("page", second), ("page", first)]


def test_fusion_reads_no_deeper_than_it_is_given():
    pages = [uuid4() for _ in range(FUSION_DEPTH)]
    assert len(fuse([], pages)) == FUSION_DEPTH
