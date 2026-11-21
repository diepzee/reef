"""Read the text formats with Reef's own code.

Markdown and plain text are kept as written. HTML keeps its visible text:
no scripts, styles or markup. Neither has pages of its own, so the text is
cut between paragraphs into parts, and each part becomes a page.

These run in the child process (:mod:`reef.reading.child`), never in the
worker itself: the bytes are untrusted.
"""

import codecs
import re
from html.parser import HTMLParser

from reef.reading import FileKind
from reef.sections import cut

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


def text_parts(data: bytes, kind: FileKind, part_chars: int) -> list[str]:
    """Read a Markdown, plain text or HTML file into parts.

    :param data: the file's bytes
    :param kind: the file's kind
    :param part_chars: the most characters in one part
    :returns: the text cut between paragraphs, each part at most
        ``part_chars`` long. An empty file gives none.
    :raises ValueError: for a kind this module does not read
    """
    match kind:
        case FileKind.MARKDOWN | FileKind.PLAIN:
            text = decode_text(data)
        case FileKind.HTML:
            text = html_text(data)
        case _:
            raise ValueError(f"not read as text here: {kind}")
    return cut(text, part_chars)
