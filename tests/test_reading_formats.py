"""Markdown, plain text and HTML, read by Reef's own code."""

import json

import pytest
from filefixtures import html_page, long_text, paragraphs

from reef.reading import FileKind, sandbox
from reef.reading.formats import decode_text, html_text, text_parts
from reef.sections import cut

PAGE_CHARS = 20_000


def _lines(text: str) -> list[str]:
    """List the lines of a text that hold something.

    :param text: the text
    :returns: its non-blank lines, in order
    """
    return [line for line in text.splitlines() if line.strip()]


def test_markdown_and_plain_text_are_kept_as_written():
    """A short file is one part, word for word."""
    text = "# Boiler\n\nServiced in *May*.\n\n- Vaillant\n- 2019"
    for kind in (FileKind.MARKDOWN, FileKind.PLAIN):
        assert text_parts(text.encode(), kind, PAGE_CHARS) == [text]
    assert decode_text(text.encode()) == text


def test_windows_1252_text_is_decoded():
    """Text that is not UTF-8 is read as Windows-1252."""
    assert decode_text("café ë".encode("cp1252")) == "café ë"


def test_bytes_windows_1252_leaves_undefined_are_replaced():
    """A byte Windows-1252 does not define becomes a replacement character."""
    assert decode_text(b"a\x81b\xe9") == "a�bé"


def test_a_byte_order_mark_is_dropped():
    """The UTF-8 byte order mark is not text."""
    assert decode_text(b"\xef\xbb\xbfhello") == "hello"
    assert text_parts(b"\xef\xbb\xbf# Notes", FileKind.MARKDOWN, PAGE_CHARS) == [
        "# Notes"
    ]


def test_line_endings_and_control_characters_are_made_plain():
    """Windows and old Mac line endings become newlines; control characters go.

    Paragraphs are found by blank lines, so a file written on Windows must
    cut like any other; Postgres cannot store a NUL character. Tab and form
    feed are text.
    """
    assert decode_text(b"one\r\n\r\ntwo\rthree\x00") == "one\n\ntwo\nthree"
    assert decode_text(b"a\x01b\x1b[0mc\x7fd\te\x0cf") == "ab[0mcd\te\x0cf"


def test_html_keeps_visible_text_only():
    """Scripts, styles and markup go; entities and line breaks are kept."""
    body = (
        "<p>First   &amp; <b>bold</b>\n paragraph.</p><p>Second.</p>"
        "<template><p>ZEBRAQUILT</p></template>"
        "<noscript>ZEBRAQUILT</noscript>"
        "<ul><li>one</li><li>two<br>three</li></ul>"
        "<table><tr><td>cell</td><td>next</td></tr></table>"
        "<!-- ZEBRAQUILT -->"
    )
    text = html_text(html_page(body))
    assert "ZEBRAQUILT" not in text
    assert "<" not in text
    assert _lines(text) == [
        "Plan",
        "First & bold paragraph.",
        "Second.",
        "one",
        "two",
        "three",
        "cell next",
    ]


def test_html_paragraphs_are_cut_like_markdown_paragraphs():
    """Block elements are set apart by blank lines, so cutting finds them."""
    text = html_text(b"<p>one</p><div>two</div><h2>three</h2>")
    assert text == "one\n\ntwo\n\nthree"


def test_preformatted_html_keeps_its_lines():
    """Code in ``pre`` keeps its line breaks and indentation."""
    text = html_text(b"<pre>def f():\n    return 1</pre><p>after</p>")
    assert text == "def f():\n    return 1\n\nafter"


def test_html_text_formats_are_read_through_html():
    """An HTML file's parts carry its text, not its markup."""
    page = html_page("<p>Hello</p>")
    assert text_parts(page, FileKind.HTML, PAGE_CHARS) == ["Plan\n\nHello"]


def test_long_text_is_cut_between_paragraphs_into_parts():
    """45 paragraphs of 1,000 characters make three parts, none lost."""
    text = long_text(45, 1_000)
    parts = text_parts(text.encode(), FileKind.MARKDOWN, PAGE_CHARS)
    assert len(parts) == 3
    assert all(len(part) <= PAGE_CHARS for part in parts)
    assert "\n\n".join(parts) == text
    kept = [paragraph for part in parts for paragraph in part.split("\n\n")]
    assert kept == paragraphs(45, 1_000)


def test_an_empty_file_has_no_parts():
    """Nothing to read gives nothing to store."""
    assert text_parts(b"", FileKind.PLAIN, PAGE_CHARS) == []


def test_cut_splits_a_paragraph_longer_than_the_limit():
    """A paragraph that does not fit is cut where it has to be."""
    assert cut("a" * 25, 10) == ["a" * 10, "a" * 10, "a" * 5]
    assert cut("one\n\ntwo\n\nthree", 9) == ["one\n\ntwo", "three"]


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (b"\x81" * 50_000, FileKind.PLAIN),
        (b"\x80" * 50_000, FileKind.PLAIN),
        (b"\x81\n\n" * 20_000, FileKind.PLAIN),
        ("€".encode() * 20_000, FileKind.MARKDOWN),
        (b'"\\' * 25_000, FileKind.MARKDOWN),
        (b"a\t" * 25_000, FileKind.PLAIN),
        (b"<p>\x81" * 20_000, FileKind.HTML),
        (b"<br>\x81" * 20_000, FileKind.HTML),
        (b"<td>\x81" * 20_000, FileKind.HTML),
        (b"<pre>\x81\n</pre>" * 10_000, FileKind.HTML),
        (b"&#65533;&quot;&#9;" * 10_000, FileKind.HTML),
    ],
    ids=[
        "undefined-cp1252",
        "euro-cp1252",
        "undefined-cp1252-paragraphs",
        "euro-utf8",
        "quotes-backslashes",
        "tabs",
        "html-paragraphs",
        "html-breaks",
        "html-cells",
        "html-pre",
        "html-references",
    ],
)
def test_an_answer_stays_within_its_files_bound(data: bytes, kind: FileKind):
    """The worker's bound on a ``parts`` answer holds without its slack.

    Each case is a worst case for growth: bytes Windows-1252 turns into
    three, characters JSON escapes, markup that turns into line breaks.

    :param data: the file
    :param kind: its kind
    """
    part_chars = 1_000
    answer = json.dumps(
        {"parts": text_parts(data, kind, part_chars)}, ensure_ascii=False
    ).encode()
    envelope = len(b'{"parts": []}')
    bound = sandbox.parts_answer_bytes(data, part_chars) - sandbox.ANSWER_SLACK
    assert len(answer) <= bound + envelope
