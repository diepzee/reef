"""Hybrid search: pages found by meaning, merged with pages found by words."""

from uuid import uuid4

import httpx
import pytest

from reef import embedding
from reef.access import Principal, arm
from reef.config import get_settings
from reef.models import Attachment, AttachmentStatus
from reef.pages import save_page
from reef.search import FUSION_DEPTH, fuse, search_pages

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
    monkeypatch.setattr(settings, "embedding_url", "http://embedder.internal")
    monkeypatch.setattr(settings, "embedding_model", fake.model_id)
    monkeypatch.setattr(embedding, "get_embedder", lambda: fake)
    monkeypatch.setattr(embedding, "_resting_until", 0.0)
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


@pytest.mark.parametrize("query", ['"vehicle tax"', "vehicle -garage"])
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


async def test_a_failed_service_is_left_alone_for_a_while(tx, household, service):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    service.fail = httpx.ConnectError("down")
    await search_pages(me, "vehicle")
    asked = service.calls
    service.fail = None
    assert await search_pages(me, "vehicle") == []
    assert service.calls == asked


async def test_a_slow_service_is_not_waited_for(tx, household, service, monkeypatch):
    me = principal_for(household["wouter"])
    await _page(me, service, "personal", "auto.md", "The garage visit is in May.")
    monkeypatch.setattr(embedding, "QUESTION_SECONDS", 0.01)

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


# --- the fusion itself ---------------------------------------------------------


def test_fusion_puts_a_row_in_both_rankings_first():
    a, b, c = uuid4(), uuid4(), uuid4()
    merged = fuse([("page", a), ("page", b)], [c, b])
    assert merged[0] == ("page", b)
    assert set(merged) == {("page", a), ("page", b), ("page", c)}


def test_fusion_keeps_files_which_have_no_vectors():
    page, file = uuid4(), uuid4()
    assert ("file", file) in fuse([("file", file)], [page])


def test_fusion_reads_no_deeper_than_it_is_given():
    pages = [uuid4() for _ in range(FUSION_DEPTH)]
    assert len(fuse([], pages)) == FUSION_DEPTH
