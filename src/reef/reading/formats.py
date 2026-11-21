"""Read the text formats with Reef's own code.

Markdown and plain text are kept as written. HTML keeps its visible text:
no scripts, styles or markup. Neither has pages of its own, so the text is
cut between paragraphs into parts, and each part becomes a page.

Word, Excel and PowerPoint files are read with python-docx, openpyxl and
python-pptx. Their archives are checked first (:func:`check_archive`), their
XML is checked for entities, and the text they give is held to what the
archive unpacks to, so no file multiplies itself. A Word file is cut like
the text formats; an Excel sheet and a PowerPoint slide are a page each, and
a longer one goes on over more pages.

These run in the child process (:mod:`reef.reading.child`), never in the
worker itself: the bytes are untrusted.
"""

import codecs
import io
import re
import zipfile
from collections.abc import Callable, Iterable, Iterator
from html.parser import HTMLParser
from typing import Any
from xml.sax.handler import ContentHandler

import defusedxml.expatreader
from pptx.enum.shapes import MSO_SHAPE_TYPE

from reef.reading import FileKind, TooLarge, Unreadable
from reef.reading.archive import (
    MAIN_PARTS,
    ZIP_MAX_BYTES,
    ZIP_MAX_ENTRIES,
    check_archive,
)
from reef.sections import cut

__all__ = [
    "MAIN_PARTS",
    "ZIP_MAX_BYTES",
    "ZIP_MAX_ENTRIES",
    "check_archive",
    "decode_text",
    "html_text",
    "text_parts",
]

_LINE_END = re.compile(r"\r\n?")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")
"""Control characters other than tab, line feed and form feed."""
_SPACE = re.compile(r"[ \t\n\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")

_HIDDEN = frozenset({"script", "style", "template", "noscript"})
"""Elements whose content is never shown as text."""

_BLOCKS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "caption",
        "dd",
        "details",
        "dialog",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hgroup",
        "hr",
        "legend",
        "li",
        "main",
        "nav",
        "ol",
        "option",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "tbody",
        "tfoot",
        "thead",
        "title",
        "tr",
        "ul",
    }
)
"""Elements that end a line. Each is set apart by a blank line, as a Markdown
paragraph is, so cutting the text finds them."""

_CELLS = frozenset({"td", "th"})


def decode_text(data: bytes) -> str:
    """Turn a text file's bytes into text.

    UTF-8 when the bytes are UTF-8, else Windows-1252, where a byte it does
    not define becomes a replacement character. A UTF-8 byte order mark is
    dropped. Line endings become ``\\n``, so a file written on Windows cuts
    into paragraphs like any other. Other control characters are dropped:
    they are not text, Postgres cannot store NUL, and JSON spells each one
    in six characters on its way out of the child.

    :param data: the file's bytes
    :returns: the text
    """
    data = data.removeprefix(codecs.BOM_UTF8)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    return _CONTROL.sub("", _LINE_END.sub("\n", text))


class _VisibleText(HTMLParser):
    """Collects the text a browser would show.

    Whitespace collapses as in a browser, except inside ``pre``. A block
    element ends a paragraph, ``br`` ends a line, and table cells in a row
    are set apart by a space.
    """

    def __init__(self) -> None:
        """Start with no text."""
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._hidden = 0
        self._pre = 0
        self._newlines = 0
        self._space = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Note where an element starts.

        :param tag: the element's name, lower case
        :param attrs: its attributes, unused
        """
        if tag in _HIDDEN:
            self._hidden += 1
        elif tag == "br":
            self._break(1)
        elif tag in _BLOCKS:
            self._break(2)
            if tag == "pre":
                self._pre += 1
        elif tag in _CELLS:
            self._space = True

    def handle_endtag(self, tag: str) -> None:
        """Note where an element ends.

        :param tag: the element's name, lower case
        """
        if tag in _HIDDEN:
            self._hidden = max(0, self._hidden - 1)
        elif tag in _BLOCKS:
            if tag == "pre":
                self._pre = max(0, self._pre - 1)
            self._break(2)
        elif tag in _CELLS:
            self._space = True

    def handle_data(self, data: str) -> None:
        """Keep text that is shown.

        :param data: text between tags, with character references resolved
        """
        if self._hidden:
            return
        if not self._pre:
            data = _SPACE.sub(" ", data)
            if self._after_space():
                data = data.lstrip(" ")
        if not data:
            return
        if self._out and self._newlines:
            self._out.append("\n" * self._newlines)
        elif self._space and not self._after_space() and not data.startswith(" "):
            self._out.append(" ")
        self._newlines = 0
        self._space = False
        self._out.append(data)

    def _after_space(self) -> bool:
        """Tell whether the next text follows whitespace, so needs none itself.

        :returns: True at the start, before a pending break, and after a
            space or a newline
        """
        return (
            not self._out or bool(self._newlines) or self._out[-1].endswith((" ", "\n"))
        )

    def _break(self, newlines: int) -> None:
        """End a line or a paragraph before the next text.

        :param newlines: 1 for a line, 2 for a paragraph
        """
        if self._out and not self._pre:
            self._out[-1] = self._out[-1].rstrip(" ")
        self._newlines = max(self._newlines, newlines)
        self._space = False

    def text(self) -> str:
        """Return what was collected.

        :returns: the visible text, paragraphs set apart by one blank line
        """
        return _BLANK_LINES.sub("\n\n", "".join(self._out)).strip()


def html_text(data: bytes) -> str:
    """Keep the visible text of an HTML file.

    Scripts, styles, templates, ``noscript`` and comments go, and so does all
    markup. Character references such as ``&amp;`` are resolved.

    :param data: the file's bytes
    :returns: the text, one paragraph per block element
    """
    parser = _VisibleText()
    parser.feed(decode_text(data))
    parser.close()
    return parser.text()


_GROUP = MSO_SHAPE_TYPE.GROUP
"""A shape that holds other shapes."""

_CELL_JOIN = " | "
"""Between the cells of a row."""

_XML_PARTS = (b".xml", b".rels")
_XML_CHUNK = 64 * 1024


class _Budget:
    """Holds the text read from one file to what its archive unpacks to.

    Text comes from XML, so it cannot honestly be longer than the XML, but a
    shared string used by a million cells can. The budget is in characters.
    """

    def __init__(self, chars: int) -> None:
        """Start with the whole allowance.

        :param chars: the most characters the file may give
        """
        self._left = chars

    def spend(self, text: str) -> str:
        """Charge some text.

        :param text: text just read
        :returns: the same text
        :raises TooLarge: when the allowance is used up
        """
        self._left -= len(text)
        if self._left < 0:
            raise TooLarge
        return text


class _StopAtRoot(ContentHandler):
    """Ends a parse when the first element starts."""

    def startElement(self, name: str, attrs: Any) -> None:
        """Stop: the prolog, where a DTD would be, is behind us.

        :param name: the element's name
        :param attrs: its attributes
        :raises StopIteration: always
        """
        raise StopIteration


def _refuse_dtds(archive: zipfile.ZipFile) -> None:
    """Refuse any XML part that declares a DTD, and so any entity.

    Reads each part up to its first element, where a DOCTYPE would have to
    be. Libraries parse without entities too; this does not rely on that.

    :param archive: the opened archive
    :raises Exception: when a part has a DTD or is malformed before its first
        element; :func:`_office` turns it into ``Unreadable``
    """
    for info in archive.infolist():
        if not info.filename.encode().endswith(_XML_PARTS):
            continue
        parser = defusedxml.expatreader.create_parser(forbid_dtd=True)
        parser.setContentHandler(_StopAtRoot())
        with archive.open(info) as part:
            try:
                while chunk := part.read(_XML_CHUNK):
                    parser.feed(chunk)
            except StopIteration:
                continue


def _office(
    data: bytes,
    kind: FileKind,
    read: Callable[[io.BytesIO, _Budget], list[str]],
    max_parts: int | None,
) -> list[str]:
    """Check an Office file, run its reader and turn every failure plain.

    :param data: the file's bytes
    :param kind: its kind
    :param read: reads the opened file into parts, within the budget
    :param max_parts: when given, the reader may stop once it has more parts
        than this, and the parts returned are cut to ``max_parts + 1``
    :returns: the parts
    :raises TooLarge: when the archive or its text is too large
    :raises Unreadable: for anything else that goes wrong, whatever the
        library says: its message is never kept
    """
    total = check_archive(data, kind)
    failed = False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            # The libraries see the archive as ``zipfile`` does, so the caps
            # hold for that view too.
            infos = archive.infolist()
            if (
                len(infos) > ZIP_MAX_ENTRIES
                or sum(info.file_size for info in infos) > ZIP_MAX_BYTES
            ):
                raise TooLarge
            _refuse_dtds(archive)
        parts = read(io.BytesIO(data), _Budget(total))
        return parts if max_parts is None else parts[: max_parts + 1]
    except (TooLarge, Unreadable):
        raise
    except MemoryError:
        raise TooLarge from None
    except Exception:  # noqa: BLE001 - a library's error is never kept
        failed = True
    assert failed
    raise Unreadable


def _plain(text: str) -> str:
    """Make a text from an Office file fit for storing.

    :param text: the text
    :returns: the text with line breaks plain and control characters gone
    """
    return _CONTROL.sub("", text.replace("\x0b", "\n"))


def _docx_blocks(blocks: Iterable[Any]) -> Iterator[str]:
    """List a Word body's paragraphs and table rows in order.

    :param blocks: paragraphs and tables, from ``iter_inner_content``
    :returns: one text per non-empty paragraph, and one per table row
    """
    for block in blocks:
        if hasattr(block, "rows"):
            for row in block.rows:
                cells: list[str] = []
                seen: set[int] = set()
                for cell in row.cells:
                    # A merged cell is listed once per column it spans.
                    if id(cell._tc) in seen:
                        continue
                    seen.add(id(cell._tc))
                    text = " ".join(_docx_blocks(cell.iter_inner_content()))
                    if text:
                        cells.append(text)
                if cells:
                    yield _CELL_JOIN.join(cells)
        elif text := _plain(block.text).strip():
            yield text


def _read_docx(
    file: io.BytesIO, budget: _Budget, part_chars: int, max_parts: int | None
) -> list[str]:
    """Read a Word file: paragraphs and table cells, in body order.

    :param file: the opened file
    :param budget: the text allowance
    :param part_chars: the most characters in one part
    :param max_parts: stop reading once the text fills more parts than this
    :returns: the text cut between paragraphs
    """
    from docx import Document

    document = Document(file)
    lines: list[str] = []
    chars = 0
    for line in _docx_blocks(document.iter_inner_content()):
        lines.append(budget.spend(line))
        chars += len(line) + 2
        # Parts are at most ``part_chars`` long, so this fills more than
        # ``max_parts`` of them.
        if max_parts is not None and chars > (max_parts + 1) * part_chars:
            break
    return cut("\n\n".join(lines), part_chars)


def _pages(
    title: str, rows: Iterable[str], part_chars: int, max_parts: int | None
) -> list[str]:
    """Cut a sheet into pages between its rows.

    :param title: the sheet's name, the first line of the first page
    :param rows: the sheet's rows, one text each
    :param part_chars: the most characters in one page
    :param max_parts: stop reading rows once there are more pages than this
    :returns: the pages, each of whole rows unless a row is longer than a page
    """
    pages: list[str] = []
    current = title[:part_chars]
    for row in rows:
        if max_parts is not None and len(pages) > max_parts:
            break
        while len(row) > part_chars:
            if current:
                pages.append(current)
                current = ""
            pages.append(row[:part_chars])
            row = row[part_chars:]
        if current and len(current) + 1 + len(row) > part_chars:
            pages.append(current)
            current = ""
        current = f"{current}\n{row}" if current else row
    pages.append(current)
    return pages


def _read_xlsx(
    file: io.BytesIO, budget: _Budget, part_chars: int, max_parts: int | None
) -> list[str]:
    """Read an Excel file: each sheet's name, then its rows.

    Cached values are read, never formulas: a formula without a cached value
    gives nothing.

    :param file: the opened file
    :param budget: the text allowance
    :param part_chars: the most characters in one page
    :param max_parts: stop once there are more pages than this, if given
    :returns: the pages, a sheet after a sheet
    """
    from openpyxl import load_workbook

    workbook = load_workbook(file, read_only=True, data_only=True)
    pages: list[str] = []
    for sheet in workbook.worksheets:
        # A sheet's declared size would have rows of empty cells padded to it.
        sheet.reset_dimensions()
        rows = (
            _CELL_JOIN.join(
                _plain(str(value)) for value in values if value not in (None, "")
            )
            for values in sheet.iter_rows(values_only=True)
        )
        pages += _pages(
            budget.spend(_plain(sheet.title)),
            (budget.spend(row) for row in rows if row),
            part_chars,
            max_parts,
        )
        if max_parts is not None and len(pages) > max_parts:
            break
    workbook.close()
    return pages


def _shape_lines(shapes: Iterable[Any]) -> Iterator[str]:
    """List the text of a slide's shapes in shape order.

    :param shapes: shapes, from a slide or a group
    :returns: one text per text frame, one per table row, groups entered
    """
    for shape in shapes:
        if shape.shape_type == _GROUP:
            yield from _shape_lines(shape.shapes)
        elif shape.has_table:
            for row in shape.table.rows:
                cells = [_plain(cell.text).strip() for cell in row.cells]
                if line := _CELL_JOIN.join(cell for cell in cells if cell):
                    yield line
        elif shape.has_text_frame and (text := _plain(shape.text_frame.text).strip()):
            yield text


def _read_pptx(
    file: io.BytesIO, budget: _Budget, part_chars: int, max_parts: int | None
) -> list[str]:
    """Read a PowerPoint file: a page per slide, empty ones included.

    :param file: the opened file
    :param budget: the text allowance
    :param part_chars: the most characters in one page
    :param max_parts: stop once there are more pages than this, if given
    :returns: the pages, a slide after a slide; a slide longer than a page
        goes on over more
    """
    from pptx import Presentation

    pages: list[str] = []
    for slide in Presentation(file).slides:
        lines = (budget.spend(line) for line in _shape_lines(slide.shapes))
        pages += cut("\n\n".join(lines), part_chars) or [""]
        if max_parts is not None and len(pages) > max_parts:
            break
    return pages


def text_parts(
    data: bytes, kind: FileKind, part_chars: int, max_parts: int | None = None
) -> list[str]:
    """Read a Markdown, plain text, HTML, Word, Excel or PowerPoint file.

    :param data: the file's bytes
    :param kind: the file's kind
    :param part_chars: the most characters in one part
    :param max_parts: for Word, Excel and PowerPoint, stop extracting once
        there are more parts than this, and return at most ``max_parts + 1``:
        the last says "more than ``max_parts``". Text formats are read whole.
    :returns: the parts, each at most ``part_chars`` long. Text formats and
        Word are cut between paragraphs, and an empty file gives none. Excel
        gives a page per sheet and PowerPoint a page per slide, a longer one
        going on over more.
    :raises Unreadable: for an Office file that is not one, or is damaged
    :raises TooLarge: for an Office file that unpacks to too much
    :raises ValueError: for a kind this module does not read
    """
    match kind:
        case FileKind.MARKDOWN | FileKind.PLAIN:
            text = decode_text(data)
        case FileKind.HTML:
            text = html_text(data)
        case FileKind.DOCX:
            return _office(
                data,
                kind,
                lambda f, b: _read_docx(f, b, part_chars, max_parts),
                max_parts,
            )
        case FileKind.XLSX:
            return _office(
                data,
                kind,
                lambda f, b: _read_xlsx(f, b, part_chars, max_parts),
                max_parts,
            )
        case FileKind.PPTX:
            return _office(
                data,
                kind,
                lambda f, b: _read_pptx(f, b, part_chars, max_parts),
                max_parts,
            )
        case _:
            raise ValueError(f"not read as text here: {kind}")
    return cut(text, part_chars)
