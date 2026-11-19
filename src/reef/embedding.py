"""Embed the pieces of an engram, and keep the vectors current.

**Where the text goes.** To the address in ``REEF_EMBEDDING_URL`` and
nowhere else. That must be a service this deployment runs itself, on a
private network: reef promises that engrams are sent nowhere on their own.
With no address set there is no embedding at all. Nothing is queued, the
tables stay empty, and search stays keyword search.

**When.** A saved engram is embedded a little later, by the worker, so an
engram edited five times in a row is embedded once. Old engrams are picked up
by a backfill that the person's own index load asks for.

**What it costs.** As little as possible:

- A piece whose text did not change is never embedded again. Each stored
  vector carries the digest of the exact text it was made from, and that
  digest is compared *in the job*, at the moment of embedding.
- At most one job waits per engram and person.
- At most ``REEF_EMBEDDING_DAILY_CAP`` pieces are sent for one person in
  one day, counting the ones the service failed on. An engram that does not
  fit in what is left waits whole, and keeps its old vectors meanwhile.

**In a shared space** the vectors are shared and the cap is not. Whoever's
job embeds an engram pays for it from their own day. A member whose own cap
is spent can therefore leave engrams for another member's backfill to pay
for. That costs the other member allowance, never privacy or correctness:
a vector is made from the engram's text and nothing else.

**Whose it is.** A vector is as private as its engram. Row security shows a
vector exactly when it shows the engram, and a job runs as the person it is
for, so it embeds only what that person may read and write.

**What is logged.** Nothing about content. A failed job records the class
of its error, as every job does.

Everything here assumes it runs inside :func:`reef.db.transaction_scope`.
"""

import asyncio
import logging
from datetime import timedelta
from typing import Protocol
from uuid import UUID, uuid4

import httpx

from reef import sections
from reef.access import Principal, arm
from reef.breaker import Breaker, describe, is_refusal
from reef.config import get_settings
from reef.indexfields import summary
from reef.jobs import BACKFILL_EMBEDDINGS, EMBED_ENGRAM, enqueue
from reef.models import EmbeddingSpend, Engram, EngramEmbedding, Job

#: The longest vector accepted. The table's own check has the same bound.
MAX_DIMS = 4096

#: The largest number a stored vector can hold: pgvector keeps 32-bit floats.
_FLOAT32_MAX = 3.4028234e38

#: After a rest, how long the one search that tries again has before the
#: others may try too, in seconds. Longer than any sensible time limit.
PROBE_SECONDS = 10.0

#: Whether searches may ask the service now. See :mod:`reef.breaker`.
breaker = Breaker(
    get_settings().embedding.rest_after_failures,
    get_settings().embedding.rest_seconds,
    PROBE_SECONDS,
)

log = logging.getLogger(__name__)

#: How each model wants a passage and a question written, by the start of
#: its name. These are the forms the experiment of 18 November 2026 was
#: measured with. A model not listed gets the bare text.
FORMATS: dict[str, tuple[str, str]] = {
    "embeddinggemma": (
        "title: none | text: {text}",
        "task: search result | query: {text}",
    ),
    "qwen3-embedding": (
        "{text}",
        "Instruct: Given a question, retrieve the notes that answer it\nQuery:{text}",
    ),
}


class CapReached(Exception):
    """Raised when an engram does not fit in what is left of the day's cap."""


class BadEmbedding(Exception):
    """Raised when the service answers with something that is not vectors."""


class ServiceFailed(Exception):
    """Raised when the service could not embed an engram.

    The pieces that were sent are already charged to the day's spend when
    this is raised. Whoever catches it must let the transaction commit, or
    the charge is lost and a failing engram could be sent without limit.
    """


class Embedder(Protocol):
    """Anything that turns passages into vectors."""

    model_id: str

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed passages.

        :param texts: the passages, already in the model's passage form
        :returns: one vector per passage, in order
        """
        ...


def passage(model_id: str, text: str) -> str:
    """Write a passage the way the model wants it.

    :param model_id: the model's name
    :param text: the piece's text, context in front
    :returns: what is sent to the model
    """
    for prefix, (form, _) in FORMATS.items():
        if model_id.startswith(prefix):
            return form.format(text=text)
    return text


def question(model_id: str, text: str) -> str:
    """Write a question the way the model wants it.

    :param model_id: the model's name
    :param text: the question
    :returns: what is sent to the model
    """
    for prefix, (_, form) in FORMATS.items():
        if model_id.startswith(prefix):
            return form.format(text=text)
    return text


class HttpEmbedder:
    """An embedding service that speaks the OpenAI embeddings protocol.

    ``POST {url}/v1/embeddings`` with a model and a list of inputs. Ollama,
    text-embeddings-inference, llama.cpp and vLLM all answer it, so the
    service behind the address can change without a change here.
    """

    def __init__(self, url: str, model_id: str, timeout: float = 120.0) -> None:
        """Remember where the service is.

        :param url: the service's base address
        :param model_id: the model it serves
        :param timeout: how long one request may take, in seconds
        """
        self.url = url.rstrip("/")
        self.model_id = model_id
        self.timeout = timeout

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed passages, a batch at a time.

        :param texts: the passages
        :returns: one vector per passage, in order
        :raises BadEmbedding: if the answer is not one sound vector per passage
        :raises httpx.HTTPError: if the service cannot be reached or refuses
        """
        batch = get_settings().embedding.batch
        vectors: list[list[float]] = []
        # trust_env=False: a proxy set in the environment would carry engram
        # text somewhere other than this address.
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            for start in range(0, len(texts), batch):
                chunk = texts[start : start + batch]
                response = await client.post(
                    f"{self.url}/v1/embeddings",
                    json={"model": self.model_id, "input": chunk},
                )
                response.raise_for_status()
                vectors.extend(_rows(response.json(), len(chunk)))
        return vectors


def _rows(answer: object, count: int) -> list[list[float]]:
    """Read one response of the embeddings protocol.

    :param answer: the parsed response body
    :param count: how many inputs were sent
    :returns: the vectors, in input order
    :raises BadEmbedding: if the answer does not hold exactly one vector
        for each input
    """
    try:
        rows = sorted(answer["data"], key=lambda row: row["index"])
        if [row["index"] for row in rows] != list(range(count)):
            raise BadEmbedding("wrong vectors for the inputs")
        return [row["embedding"] for row in rows]
    except (KeyError, TypeError) as error:
        raise BadEmbedding("not an embeddings response") from error


def get_embedder() -> Embedder | None:
    """Return the configured embedding service, if there is one.

    :returns: the embedder, or None when ``REEF_EMBEDDING_URL`` is not set
    """
    settings = get_settings()
    if not settings.embedding.url:
        return None
    return HttpEmbedder(settings.embedding.url, settings.embedding.model)


async def embed_question(text: str) -> tuple[str, list[float]] | None:
    """Embed a search question, quickly or not at all.

    The question goes to the same service the engrams go to, and nowhere
    else. Whatever goes wrong, the answer is None and the search carries on
    with keywords alone: a search must never fail because of this service.

    After ``REEF_EMBEDDING_REST_AFTER_FAILURES`` failures in a row the service is left
    alone for ``REEF_EMBEDDING_REST_SECONDS``, see :data:`breaker`. A question
    the service refuses (see :data:`reef.breaker.REFUSALS`), or an answer
    that is not a vector, does not count as a failure: those say something
    about one question, not about the service.

    Call it outside any transaction: it can take ``REEF_EMBEDDING_SECONDS``,
    and a transaction would hold a database connection all that time.

    Only the class of an error is logged, never the question.

    :param text: the question as typed
    :returns: the model's name and the question's vector, or None when
        there is no service or it gave no usable answer in time
    """
    settings = get_settings().embedding
    embedder = get_embedder()
    if embedder is None or not breaker.ready():
        return None
    try:
        async with asyncio.timeout(settings.seconds):
            vector = _checked(
                await embedder.embed(
                    [question(embedder.model_id, text[: settings.question_chars])]
                ),
                1,
            )[0]
        if not any(vector):
            # Nothing is close to a vector without a direction.
            raise BadEmbedding("a vector of zeros")
    # Broad on purpose: whatever went wrong, the search must still answer.
    except Exception as error:  # noqa: BLE001
        if not is_refusal(error) and not isinstance(error, BadEmbedding):
            breaker.failed()
        log.warning("search answers by keywords alone (%s)", describe(error))
        return None
    breaker.succeeded()
    return embedder.model_id, vector


def _checked(vectors: list[list[float]], count: int) -> list[list[float]]:
    """Refuse an answer that is not one sound vector per piece.

    :param vectors: what the service returned
    :param count: how many pieces were sent
    :returns: the vectors, unchanged
    :raises BadEmbedding: if the count, a length or a number is wrong
    """
    if not isinstance(vectors, list) or len(vectors) != count or not vectors:
        raise BadEmbedding("wrong number of vectors")
    dims = len(vectors[0]) if isinstance(vectors[0], list) else 0
    if not 1 <= dims <= MAX_DIMS:
        raise BadEmbedding("a vector has an impossible length")
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dims:
            raise BadEmbedding("a vector is malformed")
        # type() and not isinstance(): True is an int to isinstance. The
        # bound also refuses NaN and infinity, which compare false.
        if not all(type(x) in (int, float) and abs(x) <= _FLOAT32_MAX for x in vector):
            raise BadEmbedding("a vector holds something that is not a number")
    return vectors


def literal(vector: list[float]) -> str:
    """Write a vector the way pgvector reads it.

    :param vector: the numbers
    :returns: ``[1.0,2.0,...]``
    """
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


async def spent_today(principal: Principal) -> int:
    """Return how many pieces were embedded for the person today.

    :param principal: the person
    :returns: the count, zero if none
    """
    await arm(principal)
    rows = await EmbeddingSpend.raw(
        "SELECT pieces FROM embedding_spend "
        "WHERE person_id = {} AND day = CURRENT_DATE",
        principal.person_id,
    )
    return rows[0]["pieces"] if rows else 0


async def _charge(principal: Principal, pieces: int) -> None:
    """Add to the person's spend for today.

    :param principal: the person
    :param pieces: how many pieces were embedded
    """
    await EmbeddingSpend.raw(
        "INSERT INTO embedding_spend (id, person_id, day, pieces) "
        "VALUES ({}, {}, CURRENT_DATE, {}) "
        "ON CONFLICT (person_id, day) "
        "DO UPDATE SET pieces = embedding_spend.pieces + EXCLUDED.pieces",
        uuid4(),
        principal.person_id,
        pieces,
    )


async def embed_engram(
    principal: Principal, engram_id: UUID, embedder: Embedder
) -> int:
    """Bring one engram's vectors up to date.

    Embeds the pieces whose text changed, removes the vectors of pieces
    that are gone, and leaves the rest alone. Vectors of another model are
    removed: they cannot be compared with this model's.

    Does nothing for an engram the person cannot write to, and answers the
    same for an engram that does not exist.

    :param principal: the person the work is for
    :param engram_id: the engram
    :param embedder: the embedding service
    :returns: how many pieces were embedded. Zero also for an engram with more
        changed pieces than the whole daily cap: it could never fit.
    :raises CapReached: if the engram does not fit in what is left of today's
        cap. Its old vectors are kept.
    :raises ServiceFailed: if the service failed or answered badly. The
        pieces sent are charged, and the old vectors are kept.
    """
    await arm(principal)
    rows = await Engram.raw(
        "SELECT title, description, body FROM engrams WHERE id = {} "
        "AND space_id IN (SELECT reef_member_space_ids())",
        engram_id,
    )
    if not rows:
        return 0
    engram = rows[0]
    pieces = sections.split(
        engram["title"] or "",
        engram["description"] or summary(engram["body"]),
        engram["body"],
    )
    wanted = {piece.anchor: piece for piece in pieces}
    stored = await EngramEmbedding.raw(
        "SELECT id, section_anchor, model_id, content_hash FROM engram_embeddings "
        "WHERE engram_id = {}",
        engram_id,
    )
    current: set[str | None] = set()
    stale: list[UUID] = []
    for row in stored:
        piece = wanted.get(row["section_anchor"])
        if (
            row["model_id"] == embedder.model_id
            and piece is not None
            and piece.content_hash == row["content_hash"]
        ):
            current.add(row["section_anchor"])
        else:
            stale.append(row["id"])
    missing = [piece for piece in pieces if piece.anchor not in current]

    if missing:
        cap = get_settings().embedding.daily_cap
        if len(missing) > cap:
            # It can never fit, on any day. Waiting for it would be forever.
            return 0
        if len(missing) > cap - await spent_today(principal):
            raise CapReached
        try:
            async with asyncio.timeout(get_settings().embedding.engram_seconds):
                vectors = _checked(
                    await embedder.embed(
                        [passage(embedder.model_id, piece.text) for piece in missing]
                    ),
                    len(missing),
                )
        except (httpx.HTTPError, BadEmbedding, TimeoutError) as error:
            # The text was sent, so it counts, whatever came back.
            await _charge(principal, len(missing))
            raise ServiceFailed(type(error).__name__) from error
    if stale:
        await EngramEmbedding.raw(
            "DELETE FROM engram_embeddings WHERE id = ANY({}::uuid[])",
            [str(row_id) for row_id in stale],
        )
    if not missing:
        return 0
    for piece, vector in zip(missing, vectors, strict=True):
        await EngramEmbedding.raw(
            "INSERT INTO engram_embeddings "
            "(id, engram_id, section_anchor, model_id, dims, content_hash, embedding) "
            "VALUES ({}, {}, {}, {}, {}, {}, {}::vector)",
            uuid4(),
            engram_id,
            piece.anchor,
            embedder.model_id,
            len(vector),
            piece.content_hash,
            literal(vector),
        )
    await _charge(principal, len(missing))
    return len(missing)


async def backfill(principal: Principal) -> int:
    """Queue an embedding job for every engram of the person's without vectors.

    It embeds nothing itself. Each engram then gets its own job and its own
    transaction, so one engram the service refuses cannot undo the others,
    and one that does not fit in the cap cannot block the rest.

    :param principal: the person the work is for
    :returns: how many jobs were added
    """
    return sum(
        [
            await request(principal, engram_id, delay=timedelta(0))
            for engram_id in await _lacking(principal)
        ]
    )


async def _lacking(principal: Principal, limit: int | None = None) -> list[UUID]:
    """Find the person's writable engrams with no whole-engram vector of this model.

    An engram with only white space has no pieces and can have no vector, so
    it does not count: it would be asked for on every index load, forever.

    :param principal: the person
    :param limit: stop after this many
    :returns: the engrams' ids, most recently changed first
    """
    await arm(principal)
    rows = await Engram.raw(
        "SELECT p.id FROM engrams p "
        "WHERE p.space_id IN (SELECT reef_member_space_ids()) "
        "AND p.body ~ '\\S' AND NOT EXISTS ("
        "SELECT 1 FROM engram_embeddings e WHERE e.engram_id = p.id "
        "AND e.model_id = {} AND e.section_anchor IS NULL) "
        "ORDER BY p.updated_at DESC" + (f" LIMIT {int(limit)}" if limit else ""),
        get_settings().embedding.model,
    )
    return [row["id"] for row in rows]


async def request(
    principal: Principal, engram_id: UUID, delay: timedelta | None = None
) -> bool:
    """Ask for an engram to be embedded, a little later.

    Does nothing when there is no embedding service, or when a job for this
    engram is already waiting.

    :param principal: the person who saved the engram
    :param engram_id: the engram
    :param delay: how long to wait; the configured delay by default
    :returns: True if a job was added
    """
    settings = get_settings()
    if not settings.embedding.url:
        return False
    await arm(principal)
    waiting = await Job.raw(
        "SELECT 1 FROM jobs WHERE person_id = {} AND kind = {} "
        "AND status = 'queued' AND subject_ids = ARRAY[{}]::uuid[]",
        principal.person_id,
        EMBED_ENGRAM,
        str(engram_id),
    )
    if waiting:
        return False
    if delay is None:
        delay = timedelta(seconds=settings.embedding.delay_seconds)
    return await enqueue(principal, EMBED_ENGRAM, (engram_id,), delay=delay)


async def request_backfill(principal: Principal) -> bool:
    """Ask for the person's engrams to be embedded, if any has no vectors.

    Called when the person's index is loaded. That is how old engrams get
    their vectors without anything looking across people.

    :param principal: the person whose index was just built
    :returns: True if a job was added
    """
    if not get_settings().embedding.url:
        return False
    if not await _lacking(principal, limit=1):
        return False
    return await enqueue(principal, BACKFILL_EMBEDDINGS)
