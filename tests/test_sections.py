"""Cutting a page into the pieces that get embedded."""

from reef import sections
from reef.sections import INTRO, MAX_CHARS, MIN_CHARS, PAGE_CHARS, split

LONG = "word " * 100  # 500 characters: a section worth embedding
SHORT = "A short note."


def _page(*blocks: str) -> str:
    return "\n\n".join(blocks)


def _anchors(pieces) -> list[str | None]:
    return [piece.anchor for piece in pieces]


def test_an_empty_page_gives_nothing():
    assert split("Boiler", "", "  \n ") == []


def test_a_short_page_is_one_piece_with_its_title_in_front():
    [piece] = split("Boiler", "A Vaillant.", "A Vaillant.\n\nServiced in May.")
    assert piece.anchor is None
    assert piece.text == "Boiler\n\nA Vaillant.\n\nServiced in May."


def test_a_long_page_gives_the_page_and_its_sections():
    body = _page(LONG, "## Car", LONG, "## House", LONG, "### Boiler", LONG)
    pieces = split("Insurance", "", body)
    assert _anchors(pieces) == [None, INTRO, "car", "house", "house/boiler"]
    assert pieces[4].heading_path == ("House", "Boiler")


def test_a_section_carries_title_description_and_heading_path():
    body = _page("What we pay for.", LONG, "## Car", LONG * 4)
    car = split("Insurance", "What we pay for.", body)[-1]
    assert car.text.startswith("Insurance\nWhat we pay for.\nCar\n\nword")


def test_the_description_is_not_said_twice():
    body = _page("What we pay for. " + LONG, "## Car", LONG * 4)
    intro = split("Insurance", "What we pay for.", body)[1]
    assert intro.anchor == INTRO
    assert intro.text.count("What we pay for.") == 1


def test_a_short_section_joins_the_one_before_it_and_keeps_its_heading():
    body = _page("## Car", LONG * 4, "## Bike", SHORT, "## House", LONG)
    pieces = split("Insurance", "", body)
    assert _anchors(pieces) == [None, "car", "car~2", "house"]
    assert "Bike\n\nA short note." in pieces[2].body


def test_a_short_opening_joins_the_section_after_it():
    body = _page(SHORT, "## Car", LONG * 5)
    pieces = split("Insurance", "", body)
    assert pieces[1].anchor == "car"
    assert pieces[1].body.startswith(SHORT)


def test_nothing_is_dropped():
    """Every word of the body is in some section, also from short ones."""
    body = _page("## A", "alpha " + LONG, "## B", "beta", "### C", "gamma " + LONG * 6)
    joined = " ".join(piece.body for piece in split("T", "", body)[1:])
    for word in ("alpha", "beta", "gamma"):
        assert word in joined
    assert joined.count("word") == body.count("word")


def test_a_long_section_is_cut_between_paragraphs():
    body = _page("## Log", *[LONG] * 9)
    pieces = split("Climbing", "", body)[1:]
    assert _anchors(pieces) == ["log", "log~2", "log~3"]
    assert all(len(piece.body) <= MAX_CHARS for piece in pieces)
    assert all(piece.heading_path == ("Log",) for piece in pieces)


def test_one_huge_paragraph_is_cut_where_it_has_to_be():
    pieces = split("T", "", "## Log\n\n" + "x" * (MAX_CHARS * 2 + 10))[1:]
    assert [len(piece.body) for piece in pieces] == [MAX_CHARS, MAX_CHARS, 10]


def test_the_whole_page_piece_is_cut_at_its_limit():
    whole = split("T", "", "## Log\n\n" + _page(*[LONG] * 40))[0]
    assert len(whole.body) == PAGE_CHARS


def test_a_hash_sign_in_a_code_block_is_not_a_heading():
    body = _page("## Setup", LONG, "```sh\n## not a heading\n```", LONG * 4)
    assert _anchors(split("T", "", body)) == [None, "setup", "setup~2"]


def test_level_four_headings_stay_inside_their_parent():
    body = _page("## Car", LONG, "#### Detail", LONG * 4)
    pieces = split("T", "", body)
    assert _anchors(pieces) == [None, "car", "car~2"]
    assert "#### Detail" in pieces[1].body + pieces[2].body


def test_a_level_one_heading_is_not_part_of_the_path():
    body = _page("# Insurance", "## Car", LONG * 5)
    assert split("Insurance", "", body)[1].heading_path == ("Car",)


def test_two_sections_with_one_name_get_different_anchors():
    body = _page("## 2025", "### Notes", LONG, "## 2026", "### Notes", LONG * 4)
    assert _anchors(split("T", "", body))[1:3] == ["2025/notes", "2026/notes"]
    body = _page("## Notes", LONG, "## Notes", LONG * 4)
    assert _anchors(split("T", "", body))[1:3] == ["notes", "notes-2"]


def test_editing_one_section_changes_that_section_and_the_page_piece():
    before = split("T", "", _page("## A", LONG * 3, "## B", LONG * 3))
    after = split("T", "", _page("## A", LONG * 3, "## B", LONG * 3 + "new"))
    changed = [
        b.anchor
        for b, a in zip(before, after, strict=True)
        if b.content_hash != a.content_hash
    ]
    assert changed == [None, "b"]


def test_renaming_the_page_changes_every_digest():
    """The title is part of what is embedded."""
    body = _page("## A", LONG * 3, "## B", LONG * 3)
    before, after = split("Old", "", body), split("New", "", body)
    assert all(
        b.content_hash != a.content_hash for b, a in zip(before, after, strict=True)
    )


def test_the_limits_are_what_the_experiment_measured():
    assert (MIN_CHARS, MAX_CHARS) == (200, 2000)
    assert sections.INTRO.startswith("~")


def test_a_crafted_heading_cannot_occupy_the_worker():
    """One line of alternating marks and spaces once took cubic time."""
    import time

    evil = "## " + "# " * 60_000 + "x #   y"
    started = time.monotonic()
    split("T", "", "\n\n".join([evil, LONG * 5, "## Real", LONG * 5]))
    assert time.monotonic() - started < 1.0


def test_closing_marks_are_not_part_of_the_heading():
    body = _page("## Car ##", LONG * 5, "## C# notes", LONG * 5)
    paths = [piece.heading_path for piece in split("T", "", body)[1:]]
    assert ("Car",) in paths
    assert ("C# notes",) in paths


def test_a_very_long_line_is_text_not_a_heading():
    body = _page("## " + "x" * 400, LONG * 5)
    anchors = _anchors(split("T", "", body))[1:]
    assert anchors and all(anchor.startswith(INTRO) for anchor in anchors)
