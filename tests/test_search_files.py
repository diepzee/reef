"""Search inside files: by the words on their pages and by the meaning of their chunks.

Pages are stored directly, as the worker would leave them. Vectors are made
by the real embedding path with a fake model, so a chunk is cut exactly as it
is in production.
"""

import hashlib
from uuid import UUID, uuid4

import pytest
from test_search_hybrid import SubjectEmbedder

from reef import embedding, filechunks, rerank, search, sections
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
    tx, household, reading, service, reranker, monkeypatch
):
    """Switched off, search runs today's statements and reads no file text.

    The file has pages and vectors, and there is a reranker, so every place
    that asks whether reading is on is reached: the ranking by meaning, the
    reranker's reads and the reads for passages.
    """
    me = principal_for(household["wouter"])
    await _engram(me, service, "personal", "auto.md", "The garage visit is in May.")
    letter = await _file(
        me, household["w_personal"], "letter.pdf", "A scanned letter.", LETTER
    )
    await embedding.embed_file(me, letter, service)
    monkeypatch.setattr(reading, "enabled", False)
    statements: list[str] = []
    original = Engram.raw

    def recording(sql: str, *args):
        statements.append(sql)
        return original(sql, *args)

    monkeypatch.setattr(Engram, "raw", recording)
    assert not _files(await search_engrams(me, "xylophone", passages=True))
    [hit] = _files(await search_engrams(me, "letter", passages=True))
    assert hit["page"] is None
    assert hit["passage"] == hit["snippet"]
    # The reranker read the file as it always did.
    assert "letter.pdf\n\nA scanned letter." in reranker.documents
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


async def test_a_search_of_engrams_alone_finds_no_file(tx, household, service):
    """Neither by words nor by meaning: the file matches both ways, and better."""
    me = principal_for(household["wouter"])
    await _engram(me, service, "personal", "auto.md", "The garage visit is in May.")
    photo = await _file(
        me,
        household["w_personal"],
        "house.jpg",
        "Our house, seen from the street.",
        ["The house in May."],
    )
    await embedding.embed_file(me, photo, service)
    assert _files(await search_engrams(me, "house"))
    assert _files(await search_engrams(me, "garage house"))
    for query in ("house", "garage house"):
        results = await search_engrams(me, query, files=False)
        assert [(r["kind"], r["path"]) for r in results] == [("page", "auto.md")]


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


class LabelEmbedder:
    """Stands in for the embedding service: a chunk that names its page points away."""

    model_id = "fake-labels"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Point a chunk of one page one way, and anything else the other.

        :param texts: what to embed
        :returns: one vector per text
        """
        return [[0.0, 1.0] if "\npage " in text else [1.0, 0.0] for text in texts]


async def test_when_the_whole_file_chunk_is_closest_the_hit_names_no_page(
    tx, household, monkeypatch
):
    """The passage then comes from no one page, so the hit names none.

    Page 3 holds the word searched for, but the page by words gives way:
    ``page`` always says where the passage came from.
    """
    fake = LabelEmbedder()
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
    me = principal_for(household["wouter"])
    # Long enough for each page to be a chunk of its own.
    pages = [f"{line} {'word ' * 50}" for line in LETTER]
    letter = await _file(
        me, household["w_personal"], "letter.pdf", "A scanned letter.", pages
    )
    await embedding.embed_file(me, letter, fake)
    [hit] = await search_engrams(me, "xylophone", passages=True)
    assert hit["page"] is None
    assert hit["passage"].startswith(
        "letter.pdf\nA scanned letter.\n\nDear neighbours,"
    )
    assert "**xylophone**" not in hit["snippet"]


async def test_a_long_file_is_read_only_where_its_passage_is(
    tx, household, service, reranker, monkeypatch
):
    """A 200-page file in the candidates is read at its chunks' pages, not whole."""
    me = principal_for(household["wouter"])
    await _engram(me, service, "personal", "auto.md", "The garage visit is in May.")
    filler = "word " * 300
    pages = [f"Nothing to see. {filler}"] * 200
    pages[149] = "The vehicle went in for its yearly check. " + "word " * 60
    # A short page joins the chunk of the page before it.
    pages[150] = "Signed."
    ledger = await _file(
        me, household["w_personal"], "ledger.pdf", "A long ledger.", pages
    )
    await embedding.embed_file(me, ledger, service)
    returned: list[dict] = []
    original = Engram.raw

    def counting(sql: str, *args):
        query = original(sql, *args)
        if sql != search._FILE_PAGES_SQL:
            return query

        async def run():
            rows = await query
            returned.extend(rows)
            return rows

        return run()

    monkeypatch.setattr(Engram, "raw", counting)
    [hit] = _files(await search_engrams(me, "car", passages=True))
    assert hit["page"] == 150
    assert "page 150" in hit["passage"]
    assert "The vehicle went in" in hit["passage"]
    assert "page 151\n\nSigned." in hit["passage"]
    # The reranker read it too, by the same chunk.
    assert any("page 151\n\nSigned." in text for text in reranker.documents)
    # Pages 150 and 151 for each read, and one more page for the reranker's
    # second chunk: never the 200 pages, nor 300 000 characters.
    assert 0 < len(returned) <= 6
    assert sum(len(row["text"]) for row in returned) <= 3 * len(pages[0])


async def test_every_chunk_is_rebuilt_from_the_pages_read_as_it_was_cut(tx, household):
    """What the pages statement returns cuts each chunk as embedding cut it.

    The file has a short first page, empty pages, a page cut in two, short
    pages that join the chunk before them, and a start longer than the
    whole-file chunk holds.
    """
    me = principal_for(household["wouter"])
    pages = [
        "Cover.",
        "",
        "a " * 1500,
        "Signed.",
        "",
        "Note.",
        "b " * 150,
        "c " * 150,
        "End.",
        "d " * 4000,
        "e " * 150,
    ]
    ledger = await _file(me, household["w_personal"], "ledger.pdf", "A ledger.", pages)
    row = {"id": ledger, "filename": "ledger.pdf", "description": "A ledger."}
    every = filechunks.split("ledger.pdf", "A ledger.", list(enumerate(pages, 1)))
    assert [chunk.anchor for chunk in every] == [
        None,
        "p3",
        "p3~2",
        "p7",
        "p8",
        "p10",
        "p10~2",
        "p10~3",
        "p10~4",
        "p11",
    ]
    limit = get_settings().embedding.engram_chars
    for chunk in every:
        read = await search._read_pages(
            {ledger: {filechunks.page_of(chunk.anchor)}}, limit
        )
        rebuilt = search._file_chunks(row, read[ledger], [chunk.anchor])
        assert rebuilt == [chunk.text], chunk.anchor


async def test_a_page_short_once_stripped_is_short_to_the_pages_statement(
    tx, household
):
    """Line breaks around a page's text count no more in SQL than in Python.

    A text format's page is stored as cut, white space and all. This one
    strips to a few characters, so it joins the chunk before it.
    """
    me = principal_for(household["wouter"])
    pages = ["b " * 150, "\n" * 250 + "Signed.\n\t", "c " * 150]
    file = await _file(me, household["w_personal"], "letter.txt", "A letter.", pages)
    row = {"id": file, "filename": "letter.txt", "description": "A letter."}
    every = filechunks.split("letter.txt", "A letter.", list(enumerate(pages, 1)))
    assert [chunk.anchor for chunk in every] == [None, "p1", "p3"]
    limit = get_settings().embedding.engram_chars
    for chunk in every:
        read = await search._read_pages(
            {file: {filechunks.page_of(chunk.anchor)}}, limit
        )
        rebuilt = search._file_chunks(row, read[file], [chunk.anchor])
        assert rebuilt == [chunk.text], chunk.anchor


async def test_scoping_to_one_space_holds_for_file_chunks(tx, household, service):
    """A file in another space is not found by meaning either."""
    me = principal_for(household["wouter"])
    mine = await _file(
        me, household["w_personal"], "IMG_1.jpg", "Our dwelling, from the street."
    )
    ours = await _file(
        me, household["shared"], "IMG_2.jpg", "Our dwelling, from the garden."
    )
    for file in (mine, ours):
        await embedding.embed_file(me, file, service)
    results = await search_engrams(me, "house", space="household")
    assert [hit["filename"] for hit in results] == ["IMG_2.jpg"]
    results = await search_engrams(me, "house")
    assert sorted(hit["filename"] for hit in results) == ["IMG_1.jpg", "IMG_2.jpg"]


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


# --- files leave room for engrams -------------------------------------------------


async def test_files_cannot_push_the_right_engram_out_of_the_first_five(
    tx, household, service
):
    """More files than the fusion is deep, all as close to the question as
    the engram and all holding its word: the engram is still in the first
    five, which hold at most three files.

    The engrams are long, so every file outranks them by words too: without
    the rule they fall out of the keyword candidates and the files fill the
    first five.
    """
    me = principal_for(household["wouter"])
    filler = "word " * 100
    await _engram(
        me, service, "personal", "auto.md", f"The vehicle is due in May. {filler}"
    )
    await _engram(
        me,
        service,
        "personal",
        "garage.md",
        f"The vehicle garage is in Ghent. {filler}",
    )
    depth = get_settings().search.fusion_depth
    for number in range(depth + 5):
        file = await _file(
            me,
            household["w_personal"],
            f"garage-{number}.pdf",
            "A garage bill.",
            [f"The vehicle went in, vehicle check, vehicle bill {number}."],
        )
        await embedding.embed_file(me, file, service)
    hits = await search_engrams(me, "vehicle", limit=5, passages=True)
    assert [hit["kind"] for hit in hits].count("file") == 3
    assert {"auto.md", "garage.md"} <= {hit.get("path") for hit in hits}


async def test_by_words_alone_files_leave_room_for_engrams(tx, household):
    """Without an embedding service, six files that hold the word and two
    engrams that hold it: the first five hold both engrams."""
    me = principal_for(household["wouter"])
    for path in ("auto.md", "garage.md"):
        await save_engram(
            me, "personal", path, "The xylophone is due in May.", message="x"
        )
    for number in range(6):
        await _file(
            me,
            household["w_personal"],
            f"recital-{number}.pdf",
            "A programme.",
            ["The xylophone recital starts at eight. xylophone xylophone"],
        )
    hits = await search_engrams(me, "xylophone", limit=5)
    assert [hit["kind"] for hit in hits].count("file") == 3
    assert {"auto.md", "garage.md"} <= {hit.get("path") for hit in hits}


async def test_the_rule_holds_after_the_reranker(tx, household, service, reranker):
    """A reranker that likes every passage the same leaves the order, and the
    first five still hold at most three files.

    The engrams are as close in meaning as the files but lack the word, so
    without the rule the files, found both ways, fill the first five.
    """
    me = principal_for(household["wouter"])
    await _engram(me, service, "personal", "auto.md", "The car is due in May.")
    await _engram(me, service, "personal", "garage.md", "The car garage is in Ghent.")
    for number in range(8):
        file = await _file(
            me,
            household["w_personal"],
            f"garage-{number}.pdf",
            "A garage bill.",
            [f"The vehicle went in for its yearly check, bill {number}."],
        )
        await embedding.embed_file(me, file, service)
    hits = await search_engrams(me, "vehicle", limit=5, passages=True)
    assert [hit["kind"] for hit in hits].count("file") <= 3
    assert {"auto.md", "garage.md"} <= {hit.get("path") for hit in hits}


class FilesFirstReranker(RecordingReranker):
    """Stands in for a reranker that prefers every file to every engram."""

    async def score(self, query: str, documents: list[str]) -> list[float]:
        """Remember the passages and score a file's above an engram's.

        A file's passage starts with its filename, which in these tests holds
        ``invoice``; no engram's passage does.

        :param query: the search text
        :param documents: the passages
        :returns: 1 for each file passage, 0 for each engram passage
        """
        self.documents.extend(documents)
        return [1.0 if "invoice" in document else 0.0 for document in documents]


async def test_the_rule_holds_when_the_reranker_puts_files_first(
    tx, household, service, reranker, monkeypatch
):
    """A reranker that scores every file above every engram still leaves
    both engrams in the first five, beside three files.

    Before the reranker the rule has already put both engrams in the first
    five, and all ten rows are its candidates. The reranker then puts the
    eight files first. Only the rule applied again after the reranker moves
    the engrams back; without it the first five are five files.
    """
    fake = FilesFirstReranker()
    monkeypatch.setattr(rerank, "HttpReranker", lambda url, model, timeout: fake)
    me = principal_for(household["wouter"])
    await _engram(me, service, "personal", "auto.md", "The car is due in May.")
    await _engram(me, service, "personal", "garage.md", "The car garage is in Ghent.")
    for number in range(8):
        file = await _file(
            me,
            household["w_personal"],
            f"invoice-{number}.pdf",
            "A garage bill.",
            [f"The vehicle went in, vehicle check, vehicle bill {number}."],
        )
        await embedding.embed_file(me, file, service)
    hits = await search_engrams(me, "vehicle", limit=5, passages=True)
    assert any("invoice" in document for document in fake.documents)
    assert [hit["kind"] for hit in hits].count("file") <= 3
    assert {"auto.md", "garage.md"} <= {hit.get("path") for hit in hits}


async def test_an_engram_farther_than_every_file_is_still_a_candidate(
    tx, household, service
):
    """An engram that lacks the word and is farther in meaning than more
    files than the fusion is deep is still in the first five.

    The fake model gives "garage" a vector slightly off the question's,
    and every file the question's own. With engrams and files cut together
    by meaning, the closest ``fusion_depth`` rows are all files, and the
    engram, found by no word, is in no ranking at all. Only the cut per
    kind keeps it a candidate, and the rule then gives it a place.
    """
    me = principal_for(household["wouter"])
    await _engram(me, service, "personal", "auto.md", "The garage is in Ghent.")
    depth = get_settings().search.fusion_depth
    for number in range(depth + 5):
        file = await _file(
            me,
            household["w_personal"],
            f"bill-{number}.pdf",
            "A bill.",
            [f"The vehicle went in, vehicle check, vehicle bill {number}."],
        )
        await embedding.embed_file(me, file, service)
    hits = await search_engrams(me, "vehicle", limit=5)
    assert "auto.md" in {hit.get("path") for hit in hits}


async def test_only_files_still_fill_the_results(tx, household, service):
    """With no engram to show, five files are five results."""
    me = principal_for(household["wouter"])
    for number in range(6):
        file = await _file(
            me,
            household["w_personal"],
            f"garage-{number}.pdf",
            "A garage bill.",
            [f"The vehicle went in for its yearly check, bill {number}."],
        )
        await embedding.embed_file(me, file, service)
    hits = await search_engrams(me, "vehicle", limit=5)
    assert [hit["kind"] for hit in hits] == ["file"] * 5


async def test_the_setting_decides_how_many_files(tx, household, service, monkeypatch):
    """With the setting at 5 the files take every place, as before the rule.

    The engram is as close in meaning as the files but lacks the word, so
    the files, found both ways, outrank it; only the rule could move it up.
    """
    monkeypatch.setattr(get_settings().search, "files_in_five", 5)
    me = principal_for(household["wouter"])
    await _engram(me, service, "personal", "auto.md", "The car is due in May.")
    for number in range(6):
        file = await _file(
            me,
            household["w_personal"],
            f"garage-{number}.pdf",
            "A garage bill.",
            [f"The vehicle went in, vehicle check, vehicle bill {number}."],
        )
        await embedding.embed_file(me, file, service)
    hits = await search_engrams(me, "vehicle", limit=5)
    assert [hit["kind"] for hit in hits] == ["file"] * 5


async def test_with_reading_off_the_statements_are_as_before(
    tx, household, reading, service, reranker, monkeypatch
):
    """Switched off, search sends neither per-kind statement and never spreads.

    One search goes through the fusion and the reranker, one by words alone,
    each with files. Neither sends the statement that ranks files by meaning
    or the hits that hold a file's pages, and neither calls :func:`spread`.
    """
    me = principal_for(household["wouter"])
    await _engram(me, service, "personal", "auto.md", "The vehicle is due in May.")
    for number in range(6):
        file = await _file(
            me,
            household["w_personal"],
            f"garage-{number}.pdf",
            "A vehicle bill.",
            [f"The vehicle went in, vehicle check, vehicle bill {number}."],
        )
        await embedding.embed_file(me, file, service)
    monkeypatch.setattr(reading, "enabled", False)

    def refuse(*args, **kwargs):
        """Fail the test: with reading off nothing is spread.

        :param args: ignored
        :param kwargs: ignored
        :raises AssertionError: always
        """
        raise AssertionError("spread was called with reading off")

    monkeypatch.setattr(search, "spread", refuse)
    statements: list[str] = []
    original = Engram.raw

    def recording(sql: str, *args):
        """Remember a statement, then send it.

        :param sql: the statement
        :param args: its values
        :returns: what :meth:`Engram.raw` returns
        """
        statements.append(sql)
        return original(sql, *args)

    monkeypatch.setattr(Engram, "raw", recording)
    assert await search_engrams(me, "vehicle", limit=5, files=True)
    assert await search_engrams(
        me, "vehicle", limit=5, files=True, semantic=False, rerank=False
    )
    page_hits = search._PAGE_HITS_SQL.format(engram_filter="", file_filter="")
    hits = search._HITS_SQL.format(engram_filter="", file_filter="")
    assert search._SEMANTIC_FILES_SQL % ("", "") not in statements
    assert not [
        sql for sql in statements if sql.startswith(search._QUERY_SQL + page_hits)
    ]
    assert [sql for sql in statements if sql.startswith(search._QUERY_SQL + hits)]


def test_the_reading_off_statements_keep_their_text():
    """``_HITS_SQL`` and ``_SEMANTIC_SQL`` are byte for byte what they were
    before files were spread: their SHA-256, taken before the change."""
    assert hashlib.sha256(search._HITS_SQL.encode()).hexdigest() == (
        "1c12883bf5e763ade0a556dc076da74a10e562c6f36b45160e9ace4cf1ac806b"
    )
    assert hashlib.sha256(search._SEMANTIC_SQL.encode()).hexdigest() == (
        "51d5db5dfd71c0ce7403915562ebf1aed63d00a9cccb392ed7843446f3a69b4b"
    )


# --- a file's passage follows the words ------------------------------------------

#: A page chunk longer than a passage whose telling sentence lies behind the
#: cut: one paragraph of filler, then the sentence in a paragraph of its own.
#: The files that hold it are described without a word of the fake model's
#: subjects, so the page about the vehicle is the closest chunk.
BURIED = (
    "The vehicle papers. "
    + "word " * 330
    + "\n\nThe vehicle inspection sticker is number QX-7731.\n\n"
    + "word " * 20
)


async def test_a_long_chunk_is_cut_around_the_questions_words(tx, household, service):
    """The words asked for lie behind the first 1,500 characters of the
    closest chunk: the passage keeps the chunk's context lines and starts at
    their paragraph."""
    me = principal_for(household["wouter"])
    file = await _file(
        me,
        household["w_personal"],
        "papers.pdf",
        "Scanned papers.",
        [f"Holiday plans. {FILLER}", BURIED],
    )
    await embedding.embed_file(me, file, service)
    [hit] = _files(
        await search_engrams(me, "vehicle inspection sticker", passages=True)
    )
    assert "QX-7731" in hit["passage"]
    assert hit["passage"].startswith("papers.pdf\nScanned papers.\npage 2\n\n…\n\n")
    assert len(hit["passage"]) <= PASSAGE_CHARS
    assert hit["page"] == 2


async def test_a_long_chunk_without_the_questions_words_starts_at_its_start(
    tx, household, service
):
    """Found by meaning alone, with no word of the question in the chunk: the
    passage is the chunk's first 1,500 characters, as before."""
    me = principal_for(household["wouter"])
    file = await _file(
        me,
        household["w_personal"],
        "papers.pdf",
        "Scanned papers.",
        [f"Holiday plans. {FILLER}", BURIED],
    )
    await embedding.embed_file(me, file, service)
    [hit] = _files(await search_engrams(me, "car", passages=True))
    chunks = filechunks.split(
        "papers.pdf", "Scanned papers.", [(1, f"Holiday plans. {FILLER}"), (2, BURIED)]
    )
    closest = next(chunk for chunk in chunks if chunk.anchor == "p2")
    assert hit["passage"] == closest.text[:PASSAGE_CHARS]


async def test_the_whole_file_chunk_follows_the_words_too(tx, household, service):
    """A file of one long page: its whole-file chunk is closest, and the
    passage shows the paragraph behind the cut that holds the words."""
    me = principal_for(household["wouter"])
    file = await _file(
        me, household["w_personal"], "papers.pdf", "Scanned papers.", [BURIED]
    )
    await embedding.embed_file(me, file, service)
    [hit] = _files(
        await search_engrams(me, "vehicle inspection sticker", passages=True)
    )
    assert "QX-7731" in hit["passage"]
    assert len(hit["passage"]) <= PASSAGE_CHARS


async def test_the_second_part_of_a_long_page_follows_the_words_too(
    tx, household, service
):
    """A page longer than a chunk is cut in two; its second part is closest,
    and the passage shows the paragraph behind the cut that holds the words."""
    me = principal_for(household["wouter"])
    page = "The dwelling got new paint. " + "word " * 350 + "\n\n" + BURIED
    file = await _file(
        me,
        household["w_personal"],
        "papers.pdf",
        "Scanned papers.",
        [f"Holiday plans. {FILLER}", page],
    )
    await embedding.embed_file(me, file, service)
    chunks = filechunks.split(
        "papers.pdf", "Scanned papers.", [(1, f"Holiday plans. {FILLER}"), (2, page)]
    )
    second = next(chunk for chunk in chunks if chunk.anchor == "p2~2")
    assert second.body == BURIED.strip()
    [hit] = _files(
        await search_engrams(me, "vehicle inspection sticker", passages=True)
    )
    assert hit["page"] == 2
    assert "QX-7731" in hit["passage"]
    assert hit["passage"].startswith("papers.pdf\nScanned papers.\npage 2\n\n…\n\n")
    assert len(hit["passage"]) <= PASSAGE_CHARS


async def test_no_long_chunk_sends_no_headline_statement(
    tx, household, service, monkeypatch
):
    """Every closest chunk fits in a passage: search asks Postgres to mark
    nothing, and the passage is the chunk."""
    me = principal_for(household["wouter"])
    letter = await _file(
        me, household["w_personal"], "letter.pdf", "A scanned letter.", LETTER
    )
    await embedding.embed_file(me, letter, service)
    statements: list[str] = []
    original = Engram.raw

    def recording(sql: str, *args):
        """Remember a statement, then send it.

        :param sql: the statement
        :param args: its values
        :returns: what :meth:`Engram.raw` returns
        """
        statements.append(sql)
        return original(sql, *args)

    monkeypatch.setattr(Engram, "raw", recording)
    [hit] = _files(await search_engrams(me, "xylophone", passages=True))
    assert hit["passage"].startswith("letter.pdf\nA scanned letter.\n\nDear")
    assert "The xylophone recital" in hit["passage"]
    assert not [sql for sql in statements if search._CHUNK_HEADLINES_SQL in sql]


async def test_a_failing_headline_of_the_chunks_leaves_the_start(
    tx, household, service, monkeypatch
):
    """When the statement that marks the words fails, the search answers and
    the passage is the chunk's start."""
    me = principal_for(household["wouter"])
    file = await _file(
        me,
        household["w_personal"],
        "papers.pdf",
        "Scanned papers.",
        [f"Holiday plans. {FILLER}", BURIED],
    )
    await embedding.embed_file(me, file, service)
    monkeypatch.setattr(
        search, "_CHUNK_HEADLINES_SQL", "SELECT nothing FROM nowhere", raising=True
    )
    [hit] = _files(
        await search_engrams(me, "vehicle inspection sticker", passages=True)
    )
    assert hit["passage"].startswith(
        "papers.pdf\nScanned papers.\npage 2\n\nThe vehicle papers."
    )


def _papers_chunk(body: str) -> sections.Section:
    """Build page 2's chunk of ``papers.pdf``, as embedding cuts it.

    :param body: the chunk's body
    :returns: the chunk
    """
    return sections.piece("p2", ("page 2",), body, "papers.pdf", "Scanned papers.")


#: The context lines of :func:`_papers_chunk`, before its body.
PAPERS_HEADER = "papers.pdf\nScanned papers.\npage 2\n\n"


def test_a_fragment_across_the_cut_leaves_the_chunks_start():
    """A fragment on one long line that starts before the cut and ends behind
    it cannot be shown whole: the passage is the chunk's first 1,500
    characters."""
    body = "word " * 290 + "the marked sticker sits right here " + "word " * 100
    chunk = _papers_chunk(body)
    begin = len(PAPERS_HEADER) + body.index("the marked")
    assert begin < PASSAGE_CHARS < begin + len("the marked sticker sits right here")
    headline = "the marked **sticker** sits right here word"
    assert search._file_passage(chunk, headline) == chunk.text[:PASSAGE_CHARS]


def test_a_fragment_in_a_paragraph_longer_than_a_passage_starts_at_its_line():
    """The paragraph would push the fragment past the cut, so the passage
    starts at the fragment's own line."""
    body = (
        "Intro.\n\n"
        + "word " * 400
        + "\nThe vehicle inspection sticker is number QX-7731.\nword word"
    )
    passage = search._file_passage(
        _papers_chunk(body),
        "The vehicle **inspection** **sticker** is number QX-7731.",
    )
    assert passage.startswith(PAPERS_HEADER + "…\n\nThe vehicle inspection sticker")
    assert "QX-7731" in passage
    assert len(passage) <= PASSAGE_CHARS


def test_a_body_with_the_mark_character_is_still_followed():
    """The body's own emphasis does not hide the fragment: marks are set aside
    when the fragment is sought, and the passage keeps the body's text."""
    body = "word " * 400 + "\n\nThe **sticker** is number QX-7731.\n\nword"
    passage = search._file_passage(
        _papers_chunk(body), "The ****sticker**** is number QX-7731."
    )
    assert passage == PAPERS_HEADER + "…\n\nThe **sticker** is number QX-7731.\n\nword"


def test_a_headline_without_a_mark_leaves_the_chunks_start():
    """Postgres marked nothing: the passage is the chunk's start, even when
    the headline's text lies behind the cut."""
    body = "word " * 400 + "\n\nThe sticker is number QX-7731.\n\nword"
    chunk = _papers_chunk(body)
    passage = search._file_passage(chunk, "The sticker is number QX-7731.")
    assert passage == chunk.text[:PASSAGE_CHARS]


def test_a_short_chunk_is_its_own_passage():
    """A chunk that fits in a passage is shown whole, marked words or not."""
    chunk = _papers_chunk("The **sticker** is number QX-7731.")
    assert search._file_passage(chunk, "The ****sticker**** is") == chunk.text
    assert search._file_passage(chunk, None) == chunk.text
