"""Markdown, plain text and HTML, read by Reef's own code."""

import io
import json
import zipfile

import pytest
from filefixtures import (
    ZIP_HEAD,
    docx_bytes,
    forged_directory_docx,
    html_page,
    long_text,
    paragraphs,
    pptx_bytes,
    replace_part,
    xlsx_bytes,
    zip_bytes,
)

from reef.reading import FileKind, TooLarge, Unreadable, sandbox
from reef.reading.formats import check_archive, decode_text, html_text, text_parts
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
    max_parts = 50
    parts = text_parts(data, kind, part_chars)[:max_parts]
    answer = json.dumps({"parts": parts}, ensure_ascii=False).encode()
    bound = sandbox.parts_answer_bytes(part_chars, max_parts) - sandbox.ANSWER_SLACK
    assert len(answer) <= bound


def test_word_keeps_paragraphs_and_table_cells_in_order():
    """Body order holds, and a row's cells are joined by a bar."""
    data = docx_bytes(
        ["Before the table", [["Ketel", "Vaillant"], ["Jaar", "2019"]], "After it"]
    )
    (part,) = text_parts(data, FileKind.DOCX, PAGE_CHARS)
    assert _lines(part) == [
        "Before the table",
        "Ketel | Vaillant",
        "Jaar | 2019",
        "After it",
    ]


def test_word_is_cut_at_the_part_size():
    """A long Word file becomes several parts."""
    data = docx_bytes(paragraphs(10, 100))
    parts = text_parts(data, FileKind.DOCX, 450)
    assert len(parts) > 1
    assert all(len(part) <= 450 for part in parts)
    assert "".join(_lines("\n".join(parts))) == "".join(paragraphs(10, 100))


def test_excel_gives_one_page_per_sheet_with_its_name_first():
    """Each sheet is a page: its title, then its rows without empty cells."""
    data = xlsx_bytes(
        {
            "Budget": [["Ketel", 1250.5, None, ""], [], ["=A1*2", "x"]],
            "Leningen": [["Polis 4711"]],
        }
    )
    budget, loans = text_parts(data, FileKind.XLSX, PAGE_CHARS)
    assert _lines(budget)[:2] == ["Budget", "Ketel | 1250.5"]
    assert loans.splitlines()[0] == "Leningen"
    assert "Polis 4711" in loans
    assert "=A1*2" not in budget


def test_a_long_sheet_goes_on_over_the_next_pages():
    """A sheet past the part size continues on the next page, cut between rows."""
    rows = [[f"row{number:04d}", number] for number in range(200)]
    data = xlsx_bytes({"Big": rows})
    parts = text_parts(data, FileKind.XLSX, 500)
    assert len(parts) > 1
    assert all(len(part) <= 500 for part in parts)
    assert parts[0].startswith("Big\n")
    assert [line for part in parts for line in part.splitlines()][1:] == [
        f"row{number:04d} | {number}" for number in range(200)
    ]


def test_powerpoint_gives_one_page_per_slide():
    """Each slide is a page, an empty one included."""
    data = pptx_bytes([["Welkom", "Agenda"], [], ["Einde"]])
    first, second, third = text_parts(data, FileKind.PPTX, PAGE_CHARS)
    assert _lines(first) == ["Welkom", "Agenda"]
    assert second == ""
    assert third == "Einde"


def test_a_zip_bomb_is_refused_before_parsing():
    """What an archive unpacks to is checked from its directory alone."""
    data = zip_bytes({"word/document.xml": b"\x00" * 2_000_000})
    assert len(data) < 10_000
    with pytest.raises(TooLarge):
        check_archive(data, FileKind.DOCX, max_bytes=1_000_000)


def test_too_many_entries_are_refused():
    """A directory with more entries than the cap is refused."""
    data = zip_bytes({f"word/part{n}.xml": b"x" for n in range(30)})
    with pytest.raises(TooLarge):
        check_archive(data, FileKind.DOCX, max_entries=20)


def test_a_renamed_zip_is_unreadable():
    """A ZIP without the format's main part is not an Office file."""
    data = zip_bytes({"hello.txt": b"hi"})
    for kind in (FileKind.DOCX, FileKind.XLSX, FileKind.PPTX):
        with pytest.raises(Unreadable):
            text_parts(data, kind, PAGE_CHARS)
    with pytest.raises(Unreadable):
        text_parts(b"not a zip at all", FileKind.DOCX, PAGE_CHARS)
    with pytest.raises(Unreadable):
        text_parts(ZIP_HEAD + b"junk", FileKind.XLSX, PAGE_CHARS)


def test_a_broken_office_file_is_unreadable_without_the_librarys_message():
    """A library's error becomes ``Unreadable`` and says nothing."""
    data = replace_part(docx_bytes(["a"]), "word/document.xml", b"<w:document")
    with pytest.raises(Unreadable) as failure:
        text_parts(data, FileKind.DOCX, PAGE_CHARS)
    assert not failure.value.args


_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _with_shared_strings(sheet_xml: bytes, strings_xml: bytes) -> bytes:
    """Build an Excel file whose cells point into a shared strings part.

    :param sheet_xml: the first sheet's XML
    :param strings_xml: the shared strings part
    :returns: the ``.xlsx`` file, with the part listed so readers find it
    """
    data = xlsx_bytes({"S": [["seed"]]})
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        rels = archive.read("xl/_rels/workbook.xml.rels")
        types = archive.read("[Content_Types].xml")
    rels = rels.replace(
        b"</Relationships>",
        b'<Relationship Id="rId9" Target="sharedStrings.xml" Type="http://schemas'
        b'.openxmlformats.org/officeDocument/2006/relationships/sharedStrings"/>'
        b"</Relationships>",
    )
    types = types.replace(
        b"</Types>",
        b'<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.'
        b'openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>',
    )
    for name, content in (
        ("xl/_rels/workbook.xml.rels", rels),
        ("[Content_Types].xml", types),
        ("xl/sharedStrings.xml", strings_xml),
        ("xl/worksheets/sheet1.xml", sheet_xml),
    ):
        data = replace_part(data, name, content)
    return data


def _sheet(rows: bytes) -> bytes:
    """Wrap rows in a worksheet.

    :param rows: ``row`` elements
    :returns: the worksheet's XML
    """
    return (
        f'<worksheet xmlns="{_SHEET_NS}"><sheetData>'.encode()
        + rows
        + b"</sheetData></worksheet>"
    )


def test_shared_strings_are_read():
    """The helper's file reads, so the two tests below test what they say."""
    data = _with_shared_strings(
        _sheet(b'<row><c t="s"><v>0</v></c></row>'),
        f'<sst xmlns="{_SHEET_NS}"><si><t>gedeeld</t></si></sst>'.encode(),
    )
    assert "gedeeld" in text_parts(data, FileKind.XLSX, PAGE_CHARS)[0]


def test_xml_entities_are_refused():
    """A declared entity is never expanded; the file is unreadable."""
    data = _with_shared_strings(
        _sheet(b'<row><c t="s"><v>0</v></c></row>'),
        b'<?xml version="1.0"?><!DOCTYPE sst [<!ENTITY boom "EXPANDED">]>'
        + f'<sst xmlns="{_SHEET_NS}"><si><t>&boom;</t></si></sst>'.encode(),
    )
    with pytest.raises(Unreadable):
        text_parts(data, FileKind.XLSX, PAGE_CHARS)


def test_a_library_alone_would_not_expand_entities_either():
    """Defence in depth: openpyxl reads the same file without expanding."""
    from openpyxl import load_workbook

    data = _with_shared_strings(
        _sheet(b'<row><c t="s"><v>0</v></c></row>'),
        b'<?xml version="1.0"?><!DOCTYPE sst [<!ENTITY boom "EXPANDED">]>'
        + f'<sst xmlns="{_SHEET_NS}"><si><t>&boom;</t></si></sst>'.encode(),
    )
    with pytest.raises(Exception):  # noqa: B017 - whatever it refuses with
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        list(workbook.worksheets[0].iter_rows(values_only=True))


def test_entities_are_refused_in_word_and_powerpoint_too():
    """The same holds for the main part of Word and PowerPoint files."""
    doctype = b'<!DOCTYPE x [<!ENTITY boom "EXPANDED">]>'
    word = replace_part(docx_bytes(["a"]), "word/styles.xml", doctype + b"<x/>")
    slides = replace_part(pptx_bytes([["a"]]), "ppt/presProps.xml", doctype + b"<x/>")
    with pytest.raises(Unreadable):
        text_parts(word, FileKind.DOCX, PAGE_CHARS)
    with pytest.raises(Unreadable):
        text_parts(slides, FileKind.PPTX, PAGE_CHARS)


def test_shared_strings_cannot_multiply_the_text_past_the_archive():
    """A cell that points at one big string again and again is cut off."""
    data = _with_shared_strings(
        _sheet(b'<row><c t="s"><v>0</v></c></row>' * 2_000),
        f'<sst xmlns="{_SHEET_NS}"><si><t>'.encode()
        + b"x" * 100_000
        + b"</t></si></sst>",
    )
    assert len(data) < 10_000
    with pytest.raises(TooLarge):
        text_parts(data, FileKind.XLSX, PAGE_CHARS)


@pytest.mark.parametrize(
    ("data", "kind", "expected"),
    [
        (docx_bytes(["Dak", [["a", "b"]]]), FileKind.DOCX, "a | b"),
        (xlsx_bytes({"Budget": [["Polis 4711", 2]]}), FileKind.XLSX, "Polis 4711 | 2"),
        (pptx_bytes([["Welkom"]]), FileKind.PPTX, "Welkom"),
    ],
    ids=["docx", "xlsx", "pptx"],
)
async def test_each_office_format_round_trips_through_the_child(
    data: bytes, kind: FileKind, expected: str
):
    """The real child reads each format, within the worker's bound.

    :param data: the Office file
    :param kind: its kind
    :param expected: a line its text must hold
    """
    answer = await sandbox.run_child(
        ["parts", kind.value, str(PAGE_CHARS), "200"], data, seconds=60
    )
    assert expected in "\n".join(answer["parts"])


async def test_a_bad_office_file_fails_through_the_child_as_unreadable():
    """The child names ``Unreadable`` and the worker raises it."""
    with pytest.raises(Unreadable):
        await sandbox.run_child(
            ["parts", "docx", str(PAGE_CHARS), "200"], b"PK\x03\x04junk", seconds=60
        )


async def test_the_answer_bound_fits_an_office_file_that_unpacks_big():
    """Text far larger than the compressed bytes still fits the bound."""
    data = docx_bytes(paragraphs(2_000, 1_000))
    assert len(data) * 3 < 2_000 * 1_000
    answer = await sandbox.run_child(
        ["parts", "docx", str(PAGE_CHARS), "200"], data, seconds=60
    )
    assert len(answer["parts"]) > 1


def test_a_forged_directory_count_cannot_hide_entries_from_the_caps():
    """``zipfile`` walks the directory by size, so the caps are checked on its view.

    The end record says one entry; the directory holds a second that claims
    300 MB. Libraries would see both.
    """
    data = forged_directory_docx()
    assert len(data) < 10_000
    with pytest.raises(Unreadable):
        check_archive(data, FileKind.DOCX)
    with pytest.raises(Unreadable):
        text_parts(data, FileKind.DOCX, PAGE_CHARS)


def test_the_caps_hold_on_the_view_the_libraries_have():
    """Even where the walk by count and by size agree, ``zipfile``'s view is capped."""
    data = zip_bytes({"word/document.xml": b"<x/>", "big.xml": b"<x/>"})
    patched = bytearray(data)
    second = patched.rfind(b"PK\x01\x02")
    patched[second + 24 : second + 28] = (300_000_000).to_bytes(4, "little")
    with pytest.raises(TooLarge):
        text_parts(bytes(patched), FileKind.DOCX, PAGE_CHARS)


def test_a_zip64_locator_is_refused():
    """A ZIP64 locator before the end record sends ``zipfile`` elsewhere."""
    data = zip_bytes({"word/document.xml": b"<x/>"})
    end = data.rfind(b"PK\x05\x06")
    data = data[:end] + b"PK\x06\x07" + bytes(16) + data[end:]
    with pytest.raises(TooLarge):
        check_archive(data, FileKind.DOCX)


def test_a_text_format_is_read_whole_and_counted():
    """Only Office stops early; a long text file gives its true page count."""
    text = long_text(10, 100).encode()
    assert len(text_parts(text, FileKind.PLAIN, 250, max_parts=2)) == 5


def test_an_office_file_stops_one_part_past_the_limit():
    """Word, Excel and PowerPoint give ``max_parts + 1`` parts at most."""
    word = docx_bytes(paragraphs(50, 100))
    sheet = xlsx_bytes({"Big": [[f"row{n:04d}"] for n in range(500)]})
    slides = pptx_bytes([[f"slide {n}"] for n in range(20)])
    assert len(text_parts(word, FileKind.DOCX, 250, 3)) == 4
    assert len(text_parts(sheet, FileKind.XLSX, 100, 3)) == 4
    assert len(text_parts(slides, FileKind.PPTX, 250, 3)) == 4
    assert len(text_parts(slides, FileKind.PPTX, 250, 30)) == 20


async def test_the_child_gives_max_parts_and_the_page_count():
    """A long text gives exactly ``max_parts`` parts and its true count."""
    text = long_text(100, 100).encode()
    answer = await sandbox.run_child(["parts", "plain", "250", "10"], text, seconds=30)
    assert len(answer["parts"]) == 10
    assert answer["pages"] == 50
    word = docx_bytes(paragraphs(100, 100))
    answer = await sandbox.run_child(["parts", "docx", "250", "10"], word, seconds=30)
    assert len(answer["parts"]) == 10
    assert answer["pages"] == 11
