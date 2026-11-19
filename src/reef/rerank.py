"""Read the first results of a search next to the query, and order them again.

A reranker is a cross-encoder: it reads the query and one passage together
and says how well the passage answers it. That is more exact than
comparing two vectors, and far too slow to run over every page. So search
first finds candidates by words and by meaning, and the reranker reorders
only the first few. Measured on the benchmark on 18 November 2026, this
lifted the share of questions with a right page in the first five results
from 47.4% to 54.0%.

**Where the text goes.** To the address in ``REEF_RERANK_URL`` and nowhere
else: the query, and passages of pages the person can already read. That
must be a service this deployment runs itself, on a private network.

**On by default.** The reranker runs whenever an address is set.
``REEF_RERANK_ENABLED=0`` switches it off without removing the address.

**Never what makes a search fail.** Each request has a time limit
(``REEF_RERANK_SECONDS``). Whatever goes wrong, search keeps the order it
had. After repeated failures the service is left alone for a while, see
:mod:`reef.breaker`. Only the class of an error is logged, never text.

**The protocol** is ``POST {url}/v1/rerank`` with a model, a query and a
list of documents, as llama.cpp, vLLM and Infinity answer it. The answer
may be ``{"results": [{"index", "relevance_score"}]}`` or, as
text-embeddings-inference gives it, a bare list of ``{"index", "score"}``.
"""

import asyncio
import logging
from typing import Protocol

import httpx

from reef.breaker import Breaker
from reef.config import get_settings

#: How much of the query is sent, in characters.
QUERY_CHARS = 500

#: How much of one passage is sent, in characters. About 500 tokens, the
#: window the reranker was measured with.
PASSAGE_CHARS = 2000

#: Failures in a row before searches leave the service alone.
FAILURES_TO_REST = 3

#: How long they leave it alone, in seconds.
REST_SECONDS = 60.0

#: The largest score accepted. Cross-encoders give logits or probabilities;
#: anything beyond this is not a score.
_MAX_SCORE = 1e6

log = logging.getLogger(__name__)


class BadAnswer(Exception):
    """Raised when the service answers with something that is not scores."""


class Reranker(Protocol):
    """Anything that scores passages against a query."""

    model_id: str

    async def score(self, query: str, documents: list[str]) -> list[float]:
        """Score each passage against the query.

        :param query: the query
        :param documents: the passages
        :returns: one score per passage, in order, higher is better
        """
        ...


class HttpReranker:
    """A reranking service that speaks the ``/v1/rerank`` protocol."""

    def __init__(self, url: str, model_id: str, timeout: float) -> None:
        """Remember where the service is.

        :param url: the service's base address
        :param model_id: the model it serves
        :param timeout: how long one request may take, in seconds
        """
        self.url = url.rstrip("/")
        self.model_id = model_id
        self.timeout = timeout

    async def score(self, query: str, documents: list[str]) -> list[float]:
        """Score each passage against the query, in one request.

        :param query: the query
        :param documents: the passages
        :returns: one score per passage, in order
        :raises BadAnswer: if the answer is not one score per passage
        :raises httpx.HTTPError: if the service cannot be reached or refuses
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.url}/v1/rerank",
                json={
                    "model": self.model_id,
                    "query": query,
                    "documents": documents,
                    "top_n": len(documents),
                    "return_documents": False,
                },
            )
            response.raise_for_status()
            return scores_from(response.json(), len(documents))


def scores_from(answer: object, count: int) -> list[float]:
    """Read one answer of the rerank protocol.

    :param answer: the parsed response body
    :param count: how many documents were sent
    :returns: the scores, in document order
    :raises BadAnswer: unless the answer holds exactly one finite score for
        each document
    """
    try:
        rows = answer["results"] if isinstance(answer, dict) else answer
        found: dict[int, float] = {}
        for row in rows:
            index = row["index"]
            value = row["relevance_score"] if "relevance_score" in row else row["score"]
            # type() and not isinstance(): True is an int to isinstance. The
            # bound also refuses NaN, which compares false.
            if type(index) is not int or type(value) not in (int, float):
                raise BadAnswer("not a score")
            if not -_MAX_SCORE <= value <= _MAX_SCORE:
                raise BadAnswer("an impossible score")
            found[index] = float(value)
    except (KeyError, TypeError) as error:
        raise BadAnswer("not a rerank response") from error
    if sorted(found) != list(range(count)):
        raise BadAnswer("wrong scores for the documents")
    return [found[index] for index in range(count)]


def get_reranker() -> Reranker | None:
    """Return the configured reranker, if there is one and it is switched on.

    :returns: the reranker, or None without an address or with the switch off
    """
    settings = get_settings()
    if not settings.rerank_url or not settings.rerank_enabled:
        return None
    return HttpReranker(
        settings.rerank_url, settings.rerank_model, settings.rerank_seconds
    )


#: After a rest, how long the one search that tries again has before the
#: others may try too, in seconds. Longer than any sensible time limit.
PROBE_SECONDS = 10.0

#: Whether searches may ask the service now. See :mod:`reef.breaker`.
#: :func:`wanted` asks it, once per search; :func:`rerank` reports to it.
breaker = Breaker(FAILURES_TO_REST, REST_SECONDS, PROBE_SECONDS)


def wanted() -> bool:
    """Say whether a search should prepare passages for the reranker.

    Cheap, and asks nothing of the service. A search checks it before it
    collects passages, so a resting or absent reranker costs no reads.

    :returns: True if there is a reranker and it may be asked now
    """
    return get_reranker() is not None and breaker.ready()


async def rerank(query: str, documents: list[str]) -> list[float] | None:
    """Score passages against a query, quickly or not at all.

    Call it outside any transaction: it can take ``REEF_RERANK_SECONDS``,
    and a transaction would hold a database connection all that time. Call
    it only after :func:`wanted` said yes in the same search.

    :param query: the query as typed
    :param documents: the passages, already chosen
    :returns: one score per passage, or None when there is no reranker or
        it gave no usable answer in time
    """
    reranker = get_reranker()
    if reranker is None or not documents:
        return None
    try:
        async with asyncio.timeout(get_settings().rerank_seconds):
            scores = await reranker.score(
                query[:QUERY_CHARS], [text[:PASSAGE_CHARS] for text in documents]
            )
        if not isinstance(scores, list) or len(scores) != len(documents):
            raise BadAnswer("wrong number of scores")
    # Broad on purpose: whatever went wrong, the search must still answer.
    except Exception as error:  # noqa: BLE001
        refused = (
            isinstance(error, httpx.HTTPStatusError)
            and error.response.status_code < 500
        )
        if not refused and not isinstance(error, BadAnswer):
            breaker.failed()
        log.warning("search keeps its order (%s)", type(error).__name__)
        return None
    breaker.succeeded()
    return scores
