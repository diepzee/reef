"""Search inside files: by the words on their pages and by the meaning of their chunks.

Pages are stored directly, as the worker would leave them. Vectors are made
by the real embedding path with a fake model, so a chunk is cut exactly as it
is in production.
"""

from uuid import UUID, uuid4

import pytest
from test_search_hybrid import SubjectEmbedder

from reef import embedding, rerank, search
from reef.access import Principal, arm
from reef.breaker import Breaker
from reef.config import get_settings
from reef.engrams import save_engram
from reef.models import Attachment, AttachmentStatus, Engram
from reef.search import PASSAGE_CHARS, fuse, search_engrams

#: Longer than the start of the text the whole-file chunk holds, so a word
#: after it reaches no vector but its page's own.
FILLER = "word " * 1700

#: A file of three pages, each about one subject of the fake model.
NOTES = [
    f"Holiday plans. {FILLER}",
    # One chunk, longer than a passage.
    "The vehicle went in for its yearly check. " + "word " * 360,
    "The dwelling got new paint. " + "word " * 60,
]

#: A file of three pages; only the last holds the word searched for.
LETTER = [
    "Dear neighbours,",
    "About the hedge.",
    "The xylophone recital starts at eight.",
]


def principal_for(person) -> Principal:
    """Return the principal a seeded person acts as.

    :param person: a seeded person
    :returns: the principal
    """
    return Principal(person_id=person.id, email=person.email)


class RecordingReranker:
    """Stands in for the reranking service and remembers what it was sent."""

    model_id = "fake-reranker"

    def __init__(self) -> None:
        """Start with nothing sent."""
        self.documents: list[str] = []

    async def score(self, query: str, documents: list[str]) -> list[float]:
        """Remember the passages and like them all the same.

        :param query: the search text
        :param documents: the passages
        :returns: one score per passage
        """
        self.documents.extend(documents)
        return [1.0] * len(documents)


@pytest.fixture(autouse=True)
def reading(monkeypatch):
    """Switch reading on: these tests are about the text inside files.

    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the reading settings
    """
    settings = get_settings().reading
    monkeypatch.setattr(settings, "enabled", True)
    return settings


@pytest.fixture
def service(monkeypatch):
    """Configure an embedding service, and answer for it.

    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the fake model
    """
    fake = SubjectEmbedder()
    settings = get_settings()
    monkeypatch.setattr(settings.embedding, "url", "http://embedder.internal")
    monkeypatch.setattr(settings.embedding, "model", fake.model_id)
    monkeypatch.setattr(embedding, "get_embedder", lambda: fake)
    monkeypatch.setattr(
        embedding,
        "breaker",
        Breaker(
            settings.embedding.rest_after_failures, settings.embedding.rest_seconds
        ),
    )
    return fake


@pytest.fixture
def reranker(monkeypatch):
    """Configure a reranking service for any memory, and answer for it.

    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the fake reranker
    """
    fake = RecordingReranker()
    settings = get_settings()
    monkeypatch.setattr(settings.rerank, "url", "http://reranker.internal")
    monkeypatch.setattr(settings.rerank, "enabled", True)
    monkeypatch.setattr(settings.rerank, "passages", 2)
    monkeypatch.setattr(settings.rerank, "min_engrams", 0)
    monkeypatch.setattr(rerank, "HttpReranker", lambda url, model, timeout: fake)
    monkeypatch.setattr(
        rerank,
        "breaker",
        Breaker(settings.rerank.rest_after_failures, settings.rerank.rest_seconds),
    )
    return fake


async def _file(
    principal: Principal,
    space,
    filename: str,
    description: str,
    pages: list[str] | None = None,
) -> UUID:
    """Store a ready file and its pages, numbered from 1.

    :param principal: who stores it
    :param space: the seeded space it goes in
    :param filename: its name
    :param description: its description
    :param pages: the text of each page; none for a file without text
    :returns: the file's id
    """
    await arm(principal)
    file = Attachment(
        space_id=space.id,
        object_key=f"files/{filename}",
        filename=filename,
        mime="application/pdf",
        byte_size=1,
        description=description,
        status=AttachmentStatus.READY.value,
    )
    await file.save()
    for number, text in enumerate(pages or [], 1):
        await Attachment.raw(
            "INSERT INTO file_pages (id, attachment_id, number, text, reader) "
            "VALUES (gen_random_uuid(), {}, {}, {}, 'tesseract')",
            file.id,
            number,
            text,
        )
    return file.id


async def _engram(
    principal: Principal, service, space: str, path: str, body: str
) -> None:
    """Save an engram and give it its vectors at once.

    :param principal: who saves it
    :param service: the fake model
    :param space: the person's name for the space
    :param path: the engram's path
    :param body: its text
    """
    engram = await save_engram(principal, space, path, body, message="x")
    await embedding.embed_engram(principal, engram.id, service)


def _files(results: list[dict]) -> list[dict]:
    """Keep the file hits.

    :param results: what a search returned
    :returns: the hits that are files
    """
    return [hit for hit in results if hit["kind"] == "file"]


# --- by words --------------------------------------------------------------------


async def test_a_file_is_found_by_a_word_on_one_of_its_pages(tx, household):
    """The word is only on page 3: the hit names that page and bolds the word."""
    me = principal_for(household["wouter"])
    await _file(me, household["w_personal"], "letter.pdf", "A scanned letter.", LETTER)
    results = await search_engrams(me, "xylophone")
    assert [(r["kind"], r["filename"], r["page"]) for r in results] == [
        ("file", "letter.pdf", 3)
    ]
    assert "**xylophone**" in results[0]["snippet"]


async def test_a_file_counts_once_by_its_best_page(tx, household):
    """Three matching pages make one hit, on the page with every word."""
    me = principal_for(household["wouter"])
    await _file(
        me,
        household["w_personal"],
        "programme.pdf",
        "A concert programme.",
        [
            "Notes on the xylophone.",
            "The xylophone recital is on Friday.",
            # Ranks higher for the one word, but lacks the other.
            "xylophone " * 20,
        ],
    )
    results = await search_engrams(me, "xylophone recital")
    assert [(r["kind"], r["page"]) for r in results] == [("file", 2)]
    assert "**recital**" in results[0]["snippet"]


async def test_page_words_are_not_searched_with_the_switch_off(
    tx, household, reading, monkeypatch
):
    """Switched off, search runs today's statements and reads no file text."""
    monkeypatch.setattr(reading, "enabled", False)
    me = principal_for(household["wouter"])
    await _file(me, household["w_personal"], "letter.pdf", "A scanned letter.", LETTER)
    statements: list[str] = []
    original = Engram.raw

    def recording(sql: str, *args):
        statements.append(sql)
        return original(sql, *args)

    monkeypatch.setattr(Engram, "raw", recording)
    assert await search_engrams(me, "xylophone") == []
    [hit] = await search_engrams(me, "letter", passages=True)
    assert hit["page"] is None
    assert hit["passage"] == hit["snippet"]
    assert not [sql for sql in statements if "file_pages" in sql]
    assert not [sql for sql in statements if "file_embeddings" in sql]


# --- by meaning ----------------------------------------------------------------


async def test_a_photo_is_found_by_the_meaning_of_its_description(
    tx, household, service
):
    """A file without text is found by its whole-file chunk alone."""
    me = principal_for(household["wouter"])
    await _engram(me, service, "personal", "auto.md", "The garage visit is in May.")
    photo = await _file(
        me,
        household["w_personal"],
        "IMG_0042.jpg",
        "Our dwelling, seen from the street.",
    )
    await embedding.embed_file(me, photo, service)
    results = await search_engrams(me, "house")
    assert (results[0]["kind"], results[0]["filename"], results[0]["page"]) == (
        "file",
        "IMG_0042.jpg",
        None,
    )


async def test_a_file_hit_carries_the_passage_closest_to_the_question(
    tx, household, service
):
    """Meaning names the page and the passage, over a page that matches a word."""
    me = principal_for(household["wouter"])
    notes = await _file(
        me, household["w_personal"], "notes.pdf", "Notes from the year.", NOTES
    )
    await embedding.embed_file(me, notes, service)
    # "paint" is on page 3; the meaning of the question is on page 2.
    [hit] = await search_engrams(me, "car paint", passages=True)
    assert hit["page"] == 2
    assert "page 2" in hit["passage"]
    assert "The vehicle went in" in hit["passage"]
    assert len(hit["passage"]) <= PASSAGE_CHARS
    [hit] = await search_engrams(me, "car paint")
    assert hit["page"] == 2
    assert "passage" not in hit


async def test_without_vectors_the_passage_is_the_best_pages_snippet(
    tx, household, service
):
    """A file not embedded yet is shown by its best page by words."""
    me = principal_for(household["wouter"])
    await _engram(me, service, "personal", "auto.md", "The garage visit is in May.")
    await _file(me, household["w_personal"], "letter.pdf", "A scanned letter.", LETTER)
    [hit] = _files(await search_engrams(me, "xylophone", passages=True))
    assert hit["page"] == 3
    assert "**xylophone**" in hit["snippet"]
    assert hit["passage"] == hit["snippet"]


async def test_a_file_is_not_sure_in_a_whole_question(tx, household, service):
    """A page shares some word with nearly any question; that proves little."""
    me = principal_for(household["wouter"])
    await _engram(me, service, "personal", "auto.md", "The garage visit is in May.")
    await _file(
        me,
        household["w_personal"],
        "minutes.pdf",
        "Meeting minutes.",
        ["Nobody looked at the boiler."],
    )
    results = await search_engrams(me, "when was the vehicle last looked at")
    assert [r["kind"] for r in results] == ["page", "file"]
    assert results[1]["page"] == 1


def test_fuse_merges_files_and_engrams_by_kind_and_id():
    """A row is its kind and id together, in both rankings."""
    engram, file = uuid4(), uuid4()
    merged = fuse(
        [("page", engram, False), ("file", file, False)],
        # The same id as an engram is another row.
        [("file", file), ("page", file)],
    )
    assert merged[0] == ("file", file)
    assert sorted(merged, key=str) == sorted(
        [("page", engram), ("file", file), ("page", file)], key=str
    )


async def test_the_reranker_reads_a_files_best_chunks(tx, household, service, reranker):
    """The reranker reads a file's closest chunks, page label and all."""
    me = principal_for(household["wouter"])
    await _engram(me, service, "personal", "auto.md", "The garage visit is in May.")
    notes = await _file(
        me, household["w_personal"], "notes.pdf", "Notes from the year.", NOTES
    )
    await embedding.embed_file(me, notes, service)
    await search_engrams(me, "vehicle")
    mine = [text for text in reranker.documents if text.startswith("notes.pdf")]
    assert len(mine) == 2
    assert any("page 2" in text and "The vehicle went in" in text for text in mine)


async def test_a_file_without_vectors_is_reread_by_its_start(
    tx, household, service, reranker
):
    """Without vectors, the reranker reads the file's name, description and start."""
    me = principal_for(household["wouter"])
    await _engram(me, service, "personal", "auto.md", "The garage visit is in May.")
    await _file(me, household["w_personal"], "letter.pdf", "A scanned letter.", LETTER)
    await search_engrams(me, "xylophone")
    assert "letter.pdf\n\nA scanned letter.\n\nDear neighbours," in "\n".join(
        reranker.documents
    )


# --- whose files ---------------------------------------------------------------


async def test_nobody_finds_text_in_a_file_they_cannot_open(
    tx, household, service, reranker
):
    """Neither words nor meaning reach a file the searcher cannot open."""
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    diary = await _file(
        me,
        household["w_personal"],
        "diary.pdf",
        "Scanned pages.",
        ["The xylophone stands in the dwelling. " + "word " * 50],
    )
    await embedding.embed_file(me, diary, service)
    await _engram(
        partner, service, "personal", "auto.md", "The garage visit is in May."
    )
    await _engram(partner, service, "personal", "trip.md", "The holiday is booked.")
    # The owner finds it, by words and by meaning.
    assert _files(await search_engrams(me, "xylophone"))
    assert _files(await search_engrams(me, "house"))
    reranker.documents.clear()
    for question in ("xylophone", "house"):
        results = await search_engrams(partner, question, passages=True)
        assert results
        assert not _files(results)
        assert "xylophone" not in str(results)
    assert reranker.documents
    assert "xylophone" not in "\n".join(reranker.documents)


# --- never what fails a search -------------------------------------------------


async def test_a_failing_ranking_by_meaning_leaves_the_pages_words(
    tx, household, service, monkeypatch
):
    """A failed vector statement leaves search by words, pages included."""
    me = principal_for(household["wouter"])
    letter = await _file(
        me, household["w_personal"], "letter.pdf", "A scanned letter.", LETTER
    )
    await embedding.embed_file(me, letter, service)
    monkeypatch.setattr(search, "_SEMANTIC_FILES_SQL", "SELECT no_such_column%s%s")
    [hit] = await search_engrams(me, "xylophone", passages=True)
    assert hit["page"] == 3
    assert hit["passage"] == hit["snippet"]


async def test_a_failing_read_of_the_closest_chunks_leaves_the_snippet(
    tx, household, service, monkeypatch
):
    """A failed chunk read leaves the page by words and its snippet."""
    me = principal_for(household["wouter"])
    await _engram(me, service, "personal", "auto.md", "The garage visit is in May.")
    letter = await _file(
        me, household["w_personal"], "letter.pdf", "A scanned letter.", LETTER
    )
    await embedding.embed_file(me, letter, service)
    monkeypatch.setattr(search, "_FILE_PASSAGES_SQL", "SELECT no_such_column")
    [hit] = _files(await search_engrams(me, "xylophone", passages=True))
    assert hit["page"] == 3
    assert hit["passage"] == hit["snippet"]
