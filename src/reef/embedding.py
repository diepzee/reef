"""Embed the pieces of a page, and keep the vectors current.

**Where the text goes.** To the address in ``REEF_EMBEDDING_URL`` and
nowhere else. That must be a service this deployment runs itself, on a
private network: reef promises that pages are sent nowhere on their own.
With no address set there is no embedding at all. Nothing is queued, the
tables stay empty, and search stays keyword search.

**When.** A saved page is embedded a little later, by the worker, so a
page edited five times in a row is embedded once. Old pages are picked up
by a backfill that the person's own index load asks for.

**What it costs.** As little as possible:

- A piece whose text did not change is never embedded again. Each stored
  vector carries the digest of the exact text it was made from, and that
  digest is compared *in the job*, at the moment of embedding.
- At most one job waits per page.
- At most ``REEF_EMBEDDING_DAILY_CAP`` pieces are embedded for one person
  in one day. A page that does not fit in what is left waits whole, and
  keeps its old vectors meanwhile.

**Whose it is.** A vector is as private as its page. Row security shows a
vector exactly when it shows the page, and a job runs as the person it is
for, so it embeds only what that person may read and write.

**What is logged.** Nothing about content. A failed job records the class
of its error, as every job does.

Everything here assumes it runs inside :func:`reef.db.transaction_scope`.
"""

import math
from datetime import timedelta
from typing import Protocol
from uuid import UUID, uuid4

import httpx

from reef import sections
from reef.access import Principal, arm
from reef.config import get_settings
from reef.indexfields import summary
from reef.jobs import BACKFILL_EMBEDDINGS, EMBED_PAGE, enqueue
from reef.models import EmbeddingSpend, Job, Page, PageEmbedding

#: How many pieces go to the service in one request.
BATCH = 16

#: How long a page waits when the day's cap is reached.
CAP_RETRY = timedelta(hours=6)

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
    """Raised when a page does not fit in what is left of the day's cap."""


class BadEmbedding(Exception):
    """Raised when the service answers with something that is not vectors."""


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
        vectors: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for start in range(0, len(texts), BATCH):
                response = await client.post(
                    f"{self.url}/v1/embeddings",
                    json={
                        "model": self.model_id,
                        "input": texts[start : start + BATCH],
                    },
                )
                response.raise_for_status()
                rows = sorted(response.json()["data"], key=lambda row: row["index"])
                vectors.extend(row["embedding"] for row in rows)
        return vectors


def get_embedder() -> Embedder | None:
    """Return the configured embedding service, if there is one.

    :returns: the embedder, or None when ``REEF_EMBEDDING_URL`` is not set
    """
    settings = get_settings()
    if not settings.embedding_url:
        return None
    return HttpEmbedder(settings.embedding_url, settings.embedding_model)


def _checked(vectors: list[list[float]], count: int) -> list[list[float]]:
    """Refuse an answer that is not one sound vector per piece.

    :param vectors: what the service returned
    :param count: how many pieces were sent
    :returns: the vectors, unchanged
    :raises BadEmbedding: if the count, a length or a number is wrong
    """
    if len(vectors) != count or not vectors:
        raise BadEmbedding("wrong number of vectors")
    dims = len(vectors[0])
    for vector in vectors:
        if len(vector) != dims or not all(math.isfinite(x) for x in vector):
            raise BadEmbedding("a vector is malformed")
    return vectors


def _literal(vector: list[float]) -> str:
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


async def embed_page(principal: Principal, page_id: UUID, embedder: Embedder) -> int:
    """Bring one page's vectors up to date.

    Embeds the pieces whose text changed, removes the vectors of pieces
    that are gone, and leaves the rest alone. Vectors of another model are
    removed: they cannot be compared with this model's.

    Does nothing for a page the person cannot write to, and answers the
    same for a page that does not exist.

    :param principal: the person the work is for
    :param page_id: the page
    :param embedder: the embedding service
    :returns: how many pieces were embedded
    :raises CapReached: if the page does not fit in today's cap. Its old
        vectors are kept.
    """
    await arm(principal)
    rows = await Page.raw(
        "SELECT title, description, body FROM pages WHERE id = {} "
        "AND cove_id IN (SELECT reef_member_cove_ids())",
        page_id,
    )
    if not rows:
        return 0
    page = rows[0]
    pieces = sections.split(
        page["title"] or "", page["description"] or summary(page["body"]), page["body"]
    )
    wanted = {piece.anchor: piece for piece in pieces}
    stored = await PageEmbedding.raw(
        "SELECT id, section_anchor, model_id, content_hash FROM page_embeddings "
        "WHERE page_id = {}",
        page_id,
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
        left = get_settings().embedding_daily_cap - await spent_today(principal)
        if len(missing) > left:
            raise CapReached
        vectors = _checked(
            await embedder.embed(
                [passage(embedder.model_id, piece.text) for piece in missing]
            ),
            len(missing),
        )
    if stale:
        await PageEmbedding.raw(
            "DELETE FROM page_embeddings WHERE id = ANY({}::uuid[])",
            [str(row_id) for row_id in stale],
        )
    if not missing:
        return 0
    for piece, vector in zip(missing, vectors, strict=True):
        await PageEmbedding.raw(
            "INSERT INTO page_embeddings "
            "(id, page_id, section_anchor, model_id, dims, content_hash, embedding) "
            "VALUES ({}, {}, {}, {}, {}, {}, {}::vector)",
            uuid4(),
            page_id,
            piece.anchor,
            embedder.model_id,
            len(vector),
            piece.content_hash,
            _literal(vector),
        )
    await _charge(principal, len(missing))
    return len(missing)


async def backfill(principal: Principal, embedder: Embedder) -> int:
    """Embed every page the person may write to that is not current.

    A page that is current costs one read and no embedding.

    :param principal: the person the work is for
    :param embedder: the embedding service
    :returns: how many pieces were embedded
    :raises CapReached: when the day's cap stops it. What was embedded
        before that stays.
    """
    await arm(principal)
    rows = await Page.raw(
        "SELECT id FROM pages WHERE cove_id IN (SELECT reef_member_cove_ids()) "
        "ORDER BY updated_at DESC"
    )
    return sum([await embed_page(principal, row["id"], embedder) for row in rows])


async def request(
    principal: Principal, page_id: UUID, delay: timedelta | None = None
) -> bool:
    """Ask for a page to be embedded, a little later.

    Does nothing when there is no embedding service, or when a job for this
    page is already waiting.

    :param principal: the person who saved the page
    :param page_id: the page
    :param delay: how long to wait; the configured delay by default
    :returns: True if a job was added
    """
    settings = get_settings()
    if not settings.embedding_url:
        return False
    await arm(principal)
    waiting = await Job.raw(
        "SELECT 1 FROM jobs WHERE person_id = {} AND kind = {} "
        "AND status = 'queued' AND subject_ids = ARRAY[{}]::uuid[]",
        principal.person_id,
        EMBED_PAGE,
        str(page_id),
    )
    if waiting:
        return False
    if delay is None:
        delay = timedelta(seconds=settings.embedding_delay_seconds)
    return await enqueue(principal, EMBED_PAGE, (page_id,), delay=delay)


async def request_backfill(principal: Principal) -> bool:
    """Ask for the person's pages to be embedded, if any has no vectors.

    Called when the person's index is loaded. That is how old pages get
    their vectors without anything looking across people.

    :param principal: the person whose index was just built
    :returns: True if a job was added
    """
    settings = get_settings()
    if not settings.embedding_url:
        return False
    await arm(principal)
    lacking = await Page.raw(
        "SELECT 1 FROM pages p "
        "WHERE p.cove_id IN (SELECT reef_member_cove_ids()) "
        "AND btrim(p.body) <> '' AND NOT EXISTS ("
        "SELECT 1 FROM page_embeddings e WHERE e.page_id = p.id "
        "AND e.model_id = {} AND e.section_anchor IS NULL) LIMIT 1",
        settings.embedding_model,
    )
    if not lacking:
        return False
    return await enqueue(principal, BACKFILL_EMBEDDINGS)
