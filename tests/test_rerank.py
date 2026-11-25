"""The reranker: reads the first results again, never fails a search, sees only yours."""

import asyncio
from uuid import uuid4

import httpx
import pytest
from test_search import SUBSCRIPTIONS, subscriptions_piece

from reef import embedding, rerank
from reef.access import Principal, arm
from reef.breaker import Breaker
from reef.config import RerankSettings, get_settings
from reef.db import DB, transaction_scope
from reef.engrams import save_engram
from reef.models import Attachment, AttachmentStatus
from reef.search import reorder, search_engrams

#: The fake reranker likes passages holding this word, and nothing else.
ANSWER = "zebra"

FILLER = "word " * 100


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


class FakeReranker:
    """Stands in for the reranking service and remembers what it was sent."""

    model_id = "fake-reranker"

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.fail: Exception | None = None
        self.in_transaction: list[bool] = []

    async def score(self, query: str, documents: list[str]) -> list[float]:
        self.in_transaction.append(DB.current_transaction.get() is not None)
        self.calls.append((query, documents))
        if self.fail is not None:
            raise self.fail
        return [float(text.lower().count(ANSWER)) for text in documents]

    @property
    def sent(self) -> str:
        """Every passage sent so far, as one text."""
        return "\n".join(text for _, documents in self.calls for text in documents)


class SubjectEmbedder:
    """Stands in for the embedding service: a text points at its subject."""

    model_id = "fake-subjects"

    def __init__(self) -> None:
        self.in_transaction: list[bool] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.in_transaction.append(DB.current_transaction.get() is not None)
        return [self._vector(text.lower()) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        for word, vector in (
            ("garage", [1.0, 0.0, 0.0, 0.0]),
            ("holiday", [0.0, 1.0, 0.0, 0.0]),
        ):
            if word in text:
                return vector
        return [0.0, 0.0, 1.0, 0.0]


@pytest.fixture
def reranker(monkeypatch):
    """Configure a reranking service, switched on, and answer for it."""
    fake = FakeReranker()
    settings = get_settings()
    monkeypatch.setattr(settings.rerank, "url", "http://reranker.internal")
    monkeypatch.setattr(settings.rerank, "enabled", True)
    monkeypatch.setattr(settings.rerank, "candidates", 15)
    monkeypatch.setattr(settings.rerank, "passages", 2)
    monkeypatch.setattr(settings.rerank, "seconds", 3.0)
    monkeypatch.setattr(settings.rerank, "min_engrams", 0)
    monkeypatch.setattr(rerank, "HttpReranker", lambda url, model, timeout: fake)
    monkeypatch.setattr(
        rerank,
        "breaker",
        Breaker(
            get_settings().rerank.rest_after_failures,
            get_settings().rerank.rest_seconds,
        ),
    )
    return fake


@pytest.fixture
def embedder(monkeypatch):
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


async def _boilers(me) -> None:
    """Engrams that all match "boiler"; the one with the answer matches worst."""
    for n in range(4):
        await save_engram(
            me, "personal", f"boiler-{n}.md", "boiler " * 5 + f"note {n}", message="x"
        )
    await save_engram(
        me,
        "personal",
        "answer.md",
        f"A long engram. {FILLER} The boiler was serviced by the {ANSWER} company.",
        message="x",
    )


def _paths(results: list[dict]) -> list[str]:
    return [r["path"] for r in results if r["kind"] == "page"]


# --- what it does --------------------------------------------------------------


async def test_the_reranker_puts_the_answer_first(tx, household, reranker):
    me = principal_for(household["wouter"])
    await _boilers(me)
    assert _paths(await search_engrams(me, "boiler", rerank=False))[0] != "answer.md"
    assert _paths(await search_engrams(me, "boiler"))[0] == "answer.md"
    query, documents = reranker.calls[-1]
    assert query == "boiler"
    assert len(documents) == 5


async def test_reordered_hits_keep_their_own_passages(tx, household, reranker):
    """Moving the last hit to the front moves its passage with it."""
    me = principal_for(household["wouter"])
    await _boilers(me)
    before = await search_engrams(me, "boiler", rerank=False, passages=True)
    after = await search_engrams(me, "boiler", passages=True)

    assert reranker.calls
    assert _paths(before) != _paths(after)
    assert after[0]["path"] == "answer.md"
    assert {hit["path"]: hit["passage"] for hit in after} == {
        hit["path"]: hit["passage"] for hit in before
    }
    expected = {f"boiler-{number}.md": f"note {number}" for number in range(4)}
    expected["answer.md"] = f"the {ANSWER} company"
    assert {hit["path"] for hit in after} == set(expected)
    for hit in after:
        assert hit["passage"] != hit["snippet"]
        assert expected[hit["path"]] in hit["passage"]
        assert all(
            marker not in hit["passage"]
            for path, marker in expected.items()
            if path != hit["path"]
        )


async def test_it_is_on_by_default():
    assert RerankSettings.model_fields["enabled"].default is True


async def test_the_switch_turns_it_off(tx, household, reranker, monkeypatch):
    me = principal_for(household["wouter"])
    await _boilers(me)
    monkeypatch.setattr(get_settings().rerank, "enabled", False)
    assert _paths(await search_engrams(me, "boiler"))[0] != "answer.md"
    assert reranker.calls == []


async def test_without_an_address_nothing_is_sent(tx, household, reranker, monkeypatch):
    me = principal_for(household["wouter"])
    await _boilers(me)
    monkeypatch.setattr(get_settings().rerank, "url", "")
    await search_engrams(me, "boiler")
    assert reranker.calls == []


@pytest.mark.parametrize("query", ['"boiler note"', "boiler -note"])
async def test_an_exact_query_is_not_read_again(tx, household, reranker, query):
    me = principal_for(household["wouter"])
    await _boilers(me)
    await search_engrams(me, query)
    assert reranker.calls == []


async def test_one_result_is_not_worth_a_request(tx, household, reranker):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "only.md", "The only boiler.", message="x")
    assert _paths(await search_engrams(me, "boiler")) == ["only.md"]
    assert reranker.calls == []


async def test_only_the_candidates_are_read_and_the_limit_holds(
    tx, household, reranker, monkeypatch
):
    me = principal_for(household["wouter"])
    await _boilers(me)
    monkeypatch.setattr(get_settings().rerank, "candidates", 2)
    results = await search_engrams(me, "boiler", limit=3)
    assert len(results) == 3
    assert len(reranker.calls[-1][1]) == 2


async def test_a_file_is_read_by_its_name_and_description(tx, household, reranker):
    me = principal_for(household["wouter"])
    await _boilers(me)
    await arm(me)
    await Attachment(
        space_id=household["w_personal"].id,
        object_key="files/manual",
        filename="manual.pdf",
        mime="application/pdf",
        byte_size=1,
        description=f"The boiler manual, from {ANSWER}.",
        status=AttachmentStatus.READY.value,
    ).save()
    results = await search_engrams(me, "boiler")
    assert {results[0]["kind"], results[1]["kind"]} == {"file", "page"}
    assert "manual.pdf\n\nThe boiler manual" in reranker.sent


# --- what it reads -------------------------------------------------------------


async def test_an_engram_is_read_by_its_pieces_closest_to_the_query(
    tx, household, reranker, embedder, monkeypatch
):
    me = principal_for(household["wouter"])
    monkeypatch.setattr(get_settings().rerank, "passages", 1)
    # Long enough to be cut into sections: a short engram is one piece.
    body = (
        "Plans for the year.\n\n"
        f"## Garage\n\ngarage {FILLER * 3} MARKERG\n\n"
        f"## Holiday\n\nholiday {FILLER * 3} MARKERH"
    )
    engram = await save_engram(me, "personal", "plans.md", body, message="x")
    other = await save_engram(me, "personal", "other.md", "holiday notes", message="x")
    for engram_id in (engram.id, other.id):
        await embedding.embed_engram(me, engram_id, embedder)
    await search_engrams(me, "holiday")
    documents = reranker.calls[-1][1]
    mine = [text for text in documents if "MARKER" in text]
    assert len(mine) == 1
    assert "MARKERH" in mine[0] and "MARKERG" not in mine[0]


async def test_the_reranker_is_never_sent_another_persons_text(tx, household, reranker):
    """Passages are read under row security, like every other read."""
    partner = principal_for(household["partner"])
    await save_engram(
        partner, "personal", "diary.md", f"boiler secret {ANSWER}", message="x"
    )
    await save_engram(partner, "household", "shared.md", "boiler shared", message="x")
    me = principal_for(household["wouter"])
    await _boilers(me)
    results = await search_engrams(me, "boiler")
    assert "diary.md" not in _paths(results)
    assert "secret" not in reranker.sent
    assert "boiler shared" in reranker.sent


# --- never what fails a search -------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("down"),
        TimeoutError(),
        rerank.BadAnswer("junk"),
        ValueError(),
    ],
)
async def test_a_failing_reranker_keeps_the_order(tx, household, reranker, failure):
    me = principal_for(household["wouter"])
    await _boilers(me)
    before = _paths(await search_engrams(me, "boiler", rerank=False))
    reranker.fail = failure
    assert _paths(await search_engrams(me, "boiler")) == before


async def test_a_slow_reranker_is_not_waited_for(tx, household, reranker, monkeypatch):
    me = principal_for(household["wouter"])
    await _boilers(me)
    monkeypatch.setattr(get_settings().rerank, "seconds", 0.01)

    async def slow(query: str, documents: list[str]) -> list[float]:
        await asyncio.sleep(1)
        return [9.0] * len(documents)

    monkeypatch.setattr(reranker, "score", slow)
    assert _paths(await search_engrams(me, "boiler"))[0] != "answer.md"


async def test_wrong_scores_keep_the_order(tx, household, reranker, monkeypatch):
    me = principal_for(household["wouter"])
    await _boilers(me)
    before = _paths(await search_engrams(me, "boiler", rerank=False))

    async def short(query: str, documents: list[str]) -> list[float]:
        return [1.0]

    monkeypatch.setattr(reranker, "score", short)
    assert _paths(await search_engrams(me, "boiler")) == before


async def test_a_reranker_that_keeps_failing_is_left_alone(tx, household, reranker):
    me = principal_for(household["wouter"])
    await _boilers(me)
    reranker.fail = httpx.ConnectError("down")
    for _ in range(get_settings().rerank.rest_after_failures):
        await search_engrams(me, "boiler")
    asked = len(reranker.calls)
    reranker.fail = None
    await search_engrams(me, "boiler")
    assert len(reranker.calls) == asked


async def test_a_refused_query_never_rests_the_reranker(tx, household, reranker):
    me = principal_for(household["wouter"])
    await _boilers(me)
    request = httpx.Request("POST", "http://reranker.internal/v1/rerank")
    reranker.fail = httpx.HTTPStatusError(
        "refused", request=request, response=httpx.Response(413, request=request)
    )
    for _ in range(get_settings().rerank.rest_after_failures + 2):
        await search_engrams(me, "boiler")
    reranker.fail = None
    assert _paths(await search_engrams(me, "boiler"))[0] == "answer.md"


async def test_a_failure_logs_no_text(tx, household, reranker, caplog):
    me = principal_for(household["wouter"])
    await _boilers(me)
    reranker.fail = httpx.ConnectError("boiler")
    with caplog.at_level("WARNING"):
        await search_engrams(me, "boiler")
    assert "ConnectError" in caplog.text
    assert "boiler" not in caplog.text


# --- no model call holds a database connection ---------------------------------


async def test_no_model_is_asked_inside_a_transaction(household, reranker, embedder):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await _boilers(me)
        engram = await save_engram(
            me, "personal", "garage.md", "boiler in the garage", message="x"
        )
        await embedding.embed_engram(me, engram.id, embedder)
    embedder.in_transaction.clear()
    results = await search_engrams(me, "boiler garage", log=True)
    assert results
    assert reranker.in_transaction == [False]
    assert embedder.in_transaction == [False]


async def test_inside_a_transaction_it_still_works(tx, household, reranker):
    me = principal_for(household["wouter"])
    await _boilers(me)
    assert _paths(await search_engrams(me, "boiler"))[0] == "answer.md"


# --- the parts -----------------------------------------------------------------


def test_reorder_follows_the_best_passage_of_each_row():
    a, b, c, d = (("page", uuid4()) for _ in range(4))
    # Two passages of a, one of b; c was not read; d was not a candidate.
    assert reorder([a, b, c, d], 3, [0, 0, 1], [0.1, 0.9, 0.5]) == [a, b, c, d]
    assert reorder([a, b, c, d], 3, [0, 1], [0.1, 0.5]) == [b, a, c, d]


def test_reorder_leaves_an_unread_candidate_in_its_place():
    a, b, c = (("page", uuid4()) for _ in range(3))
    assert reorder([a, b, c], 3, [1, 2], [0.1, 0.9]) == [a, c, b]


def test_reorder_keeps_ties_in_their_old_order():
    a, b = ("page", uuid4()), ("page", uuid4())
    assert reorder([a, b], 2, [0, 1], [1.0, 1.0]) == [a, b]


@pytest.mark.parametrize(
    "answer",
    [
        {
            "results": [
                {"index": 1, "relevance_score": 0.2},
                {"index": 0, "relevance_score": 0.9},
            ]
        },
        [{"index": 1, "score": 0.2}, {"index": 0, "score": 0.9}],
    ],
)
def test_both_answer_shapes_are_read(answer):
    assert rerank.scores_from(answer, 2) == [0.9, 0.2]


@pytest.mark.parametrize(
    "answer",
    [
        {"results": [{"index": 0, "relevance_score": 0.9}]},
        {
            "results": [
                {"index": 0, "relevance_score": 1},
                {"index": 0, "relevance_score": 1},
            ]
        },
        {
            "results": [
                {"index": 0, "relevance_score": float("nan")},
                {"index": 1, "relevance_score": 1},
            ]
        },
        {
            "results": [
                {"index": 0, "relevance_score": 10**400},
                {"index": 1, "relevance_score": 1},
            ]
        },
        {
            "results": [
                {"index": 0, "relevance_score": True},
                {"index": 1, "relevance_score": 1},
            ]
        },
        {
            "results": [
                {"index": "0", "relevance_score": 1},
                {"index": 1, "relevance_score": 1},
            ]
        },
        {"data": []},
        "junk",
        None,
        [
            {"index": 0, "score": 5.0},
            {"index": 0, "score": -3.0},
            {"index": 1, "score": 1},
        ],
    ],
)
def test_a_bad_answer_is_refused(answer):
    with pytest.raises(rerank.BadAnswer):
        rerank.scores_from(answer, 2)


async def test_the_http_client_speaks_the_rerank_protocol(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        return httpx.Response(
            200, json={"results": [{"index": 0, "relevance_score": 0.5}]}
        )

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def client(**kwargs):
        seen["trust_env"] = kwargs.get("trust_env")
        return original(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    reranker = rerank.HttpReranker("http://reranker.internal/", "m", 1.0)
    assert await reranker.score("q", ["d"]) == [0.5]
    assert seen["url"] == "http://reranker.internal/v1/rerank"
    assert b'"documents":["d"]' in seen["body"].replace(b" ", b"")
    # A proxy from the environment would carry engram text elsewhere.
    assert seen["trust_env"] is False


# --- found in review -----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "counts"),
    [
        (400, False),
        (413, False),
        (422, False),
        (307, True),
        (401, True),
        (404, True),
        (429, True),
        (503, True),
    ],
)
async def test_only_a_refused_query_is_not_a_failure(
    tx, household, reranker, status, counts
):
    me = principal_for(household["wouter"])
    await _boilers(me)
    request = httpx.Request("POST", "http://reranker.internal/v1/rerank")
    reranker.fail = httpx.HTTPStatusError(
        "x", request=request, response=httpx.Response(status, request=request)
    )
    await search_engrams(me, "boiler")
    assert rerank.breaker.failures == (1 if counts else 0)


async def test_a_failure_logs_its_status(tx, household, reranker, caplog):
    me = principal_for(household["wouter"])
    await _boilers(me)
    request = httpx.Request("POST", "http://reranker.internal/v1/rerank")
    reranker.fail = httpx.HTTPStatusError(
        "x", request=request, response=httpx.Response(404, request=request)
    )
    with caplog.at_level("WARNING"):
        await search_engrams(me, "boiler")
    assert "HTTPStatusError 404" in caplog.text


async def test_a_search_without_a_request_does_not_use_up_the_trial(
    tx, household, reranker
):
    """After a rest, the trial belongs to a search that really asks."""
    me = principal_for(household["wouter"])
    await _boilers(me)
    rerank.breaker.failures = rerank.breaker.threshold
    rerank.breaker.resting_until = 0.0  # the rest is over
    assert await search_engrams(me, "nothing-matches-this") == []
    assert _paths(await search_engrams(me, "boiler"))[0] == "answer.md"
    assert rerank.breaker.failures == 0


async def test_non_numbers_from_a_reranker_keep_the_order(
    tx, household, reranker, monkeypatch
):
    me = principal_for(household["wouter"])
    await _boilers(me)
    before = _paths(await search_engrams(me, "boiler", rerank=False))

    async def odd(query: str, documents: list[str]) -> list:
        return [float("nan")] * len(documents)

    monkeypatch.setattr(reranker, "score", odd)
    assert _paths(await search_engrams(me, "boiler")) == before


async def test_a_failed_read_of_passages_keeps_the_order(
    tx, household, reranker, monkeypatch
):
    from reef import search

    me = principal_for(household["wouter"])
    await _boilers(me)
    before = _paths(await search_engrams(me, "boiler", rerank=False))
    monkeypatch.setattr(search, "_ENGRAM_TEXT_SQL", "SELECT no_such_column {} {} {}")
    assert _paths(await search_engrams(me, "boiler")) == before
    assert reranker.calls == []


async def test_an_engram_with_no_text_is_read_by_its_title(tx, household, reranker):
    me = principal_for(household["wouter"])
    await _boilers(me)
    await save_engram(
        me, "personal", "empty.md", "   ", message="x", title=f"Boiler {ANSWER}"
    )
    results = _paths(await search_engrams(me, "boiler"))
    assert f"Boiler {ANSWER}" in reranker.sent
    assert results[0] in {"empty.md", "answer.md"}


async def test_without_vectors_a_reread_hit_gets_its_snippet_widened(
    tx, household, reranker
):
    """Read again, but with no embedding service: each passage goes by words."""
    me = principal_for(household["wouter"])
    await save_engram(
        me,
        "personal",
        "finance/subscriptions.md",
        SUBSCRIPTIONS,
        message="x",
        title="Abonnementen",
    )
    await save_engram(
        me, "personal", "tafel.md", "De krant ligt op tafel.", message="x"
    )
    results = await search_engrams(me, "krant abonnement opgezegd", passages=True)
    assert reranker.calls
    [hit] = [r for r in results if r["path"] == "finance/subscriptions.md"]
    assert hit["passage"] == subscriptions_piece(None)
    assert "Opgezegd op 1 oktober 2026" in hit["passage"]


async def test_scoping_to_one_space_holds_for_the_reranker(tx, household, reranker):
    partner = principal_for(household["partner"])
    await save_engram(
        partner, "household", "ours.md", "boiler in the hall", message="x"
    )
    await save_engram(
        partner, "household", "ours2.md", "boiler in the attic", message="x"
    )
    me = principal_for(household["wouter"])
    await _boilers(me)
    results = await search_engrams(me, "boiler", space="household")
    assert {r["cove"] for r in results} == {"household"}
    assert "note 0" not in reranker.sent


async def test_an_engram_deleted_while_the_reranker_reads_is_not_shown(
    tx, household, reranker, monkeypatch
):
    from reef.engrams import delete_engram

    me = principal_for(household["wouter"])
    await _boilers(me)
    original = reranker.score

    async def meanwhile(query: str, documents: list[str]) -> list[float]:
        await delete_engram(me, "personal", "answer.md")
        return await original(query, documents)

    monkeypatch.setattr(reranker, "score", meanwhile)
    assert "answer.md" not in _paths(await search_engrams(me, "boiler"))


async def test_a_space_left_while_the_reranker_reads_is_not_shown(
    tx, household, reranker, monkeypatch
):
    from reef.spaces import leave_space

    partner = principal_for(household["partner"])
    await save_engram(partner, "household", "shared.md", "boiler zebra", message="x")
    me = principal_for(household["wouter"])
    await _boilers(me)
    original = reranker.score

    async def meanwhile(query: str, documents: list[str]) -> list[float]:
        await leave_space(me, "household")
        return await original(query, documents)

    monkeypatch.setattr(reranker, "score", meanwhile)
    assert "shared.md" not in _paths(await search_engrams(me, "boiler"))


async def test_the_search_log_works_outside_a_transaction(
    household, reranker, monkeypatch
):
    from reef.models import SearchLog

    monkeypatch.setattr(get_settings().search, "log", True)
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await _boilers(me)
    await search_engrams(me, "boiler", log=True)
    async with transaction_scope():
        await arm(me)
        rows = await SearchLog.raw("SELECT engram_ids FROM search_log")
    assert len(rows) == 1 and rows[0]["engram_ids"]


async def test_the_search_tools_ask_no_model_inside_a_transaction(
    household, reranker, embedder, monkeypatch
):
    from reef.server import recall, search

    me = principal_for(household["wouter"])
    monkeypatch.setattr(
        get_settings().dev, "principal_email", household["wouter"].email
    )
    async with transaction_scope():
        await _boilers(me)
    results = await recall("", question="boiler")
    assert results and results[0]["engram"] == "answer.md"
    connector = await search("boiler")
    assert len(connector["results"]) > 1
    assert reranker.in_transaction == [False, False]
    assert embedder.in_transaction == [False, False]


# --- only a large memory is reread ---------------------------------------------


async def test_a_small_memory_is_not_reread(tx, household, reranker, monkeypatch):
    me = principal_for(household["wouter"])
    await _boilers(me)
    monkeypatch.setattr(get_settings().rerank, "min_engrams", 1000)
    assert _paths(await search_engrams(me, "boiler"))[0] != "answer.md"
    assert reranker.calls == []


async def test_a_memory_at_the_threshold_is_reread(
    tx, household, reranker, monkeypatch
):
    me = principal_for(household["wouter"])
    await _boilers(me)
    monkeypatch.setattr(get_settings().rerank, "min_engrams", 5)
    assert _paths(await search_engrams(me, "boiler"))[0] == "answer.md"


async def test_only_the_engrams_of_the_space_searched_count(
    tx, household, reranker, monkeypatch
):
    partner = principal_for(household["partner"])
    for n in range(2):
        await save_engram(partner, "household", f"h{n}.md", f"boiler {n}", message="x")
    me = principal_for(household["wouter"])
    await _boilers(me)
    monkeypatch.setattr(get_settings().rerank, "min_engrams", 5)
    await search_engrams(me, "boiler", space="household")
    assert reranker.calls == []


async def test_nobody_elses_engrams_count(tx, household, reranker, monkeypatch):
    """Row security does the counting."""
    partner = principal_for(household["partner"])
    for n in range(10):
        await save_engram(partner, "personal", f"p{n}.md", f"boiler {n}", message="x")
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "a.md", "boiler one", message="x")
    await save_engram(me, "personal", "b.md", "boiler two", message="x")
    monkeypatch.setattr(get_settings().rerank, "min_engrams", 5)
    await search_engrams(me, "boiler")
    assert reranker.calls == []
