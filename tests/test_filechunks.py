"""File chunks: the whole-file chunk first, then each page, cut between paragraphs."""

from reef import filechunks
from reef.config import get_settings

PAGE = "word " * 120


def test_a_file_without_text_is_one_chunk_of_name_and_description():
    chunks = filechunks.split("plan.png", "Floor plan of the ground floor", [])
    assert [chunk.anchor for chunk in chunks] == [None]
    assert chunks[0].text.startswith("plan.png\nFloor plan of the ground floor")


def test_each_page_is_a_chunk_that_says_its_page():
    chunks = filechunks.split("a.pdf", "A report", [(1, PAGE), (2, PAGE)])
    assert [chunk.anchor for chunk in chunks] == [None, "p1", "p2"]
    assert "page 2" in chunks[2].text
    assert "page 2" not in chunks[1].text


def test_a_long_page_is_cut_between_paragraphs():
    limit = get_settings().embedding.piece_max_chars
    paragraph = "x" * (limit // 2 + 100)
    text = "\n\n".join([paragraph] * 3)
    chunks = filechunks.split("a.pdf", "A report", [(1, text), (2, PAGE)])
    assert [chunk.anchor for chunk in chunks] == [
        None,
        "p1",
        "p1~2",
        "p1~3",
        "p2",
    ]
    assert all(len(chunk.body) <= limit for chunk in chunks[1:])


def test_a_short_page_joins_the_one_before():
    chunks = filechunks.split("a.pdf", "A report", [(1, PAGE), (2, "Signed.")])
    assert [chunk.anchor for chunk in chunks] == [None, "p1"]
    assert chunks[1].body.endswith("Signed.")


def test_a_one_page_file_has_no_page_label():
    chunks = filechunks.split("a.pdf", "A report", [(1, PAGE)])
    assert [chunk.anchor for chunk in chunks] == [None, "p1"]
    assert all("page 1" not in chunk.text for chunk in chunks)


def test_an_empty_page_is_no_chunk():
    chunks = filechunks.split("a.pdf", "A report", [(1, PAGE), (2, "  "), (3, PAGE)])
    assert [chunk.anchor for chunk in chunks] == [None, "p1", "p3"]


def test_the_whole_file_chunk_stops_at_the_engram_limit():
    limit = get_settings().embedding.engram_chars
    pages = [(number, "y" * 1500) for number in range(1, 20)]
    whole = filechunks.split("a.pdf", "A report", pages)[0]
    assert len(whole.body) <= limit


def test_page_of_reads_the_anchor():
    assert filechunks.page_of("p12~3") == 12
    assert filechunks.page_of("p4") == 4
    assert filechunks.page_of(None) is None
    assert filechunks.page_of("car") is None
