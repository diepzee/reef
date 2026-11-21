"""Reading the text inside stored files, on Reef's own servers.

A file's kind comes from its bytes (:mod:`reef.reading.kinds`). Every file is
parsed in a child process that holds no secret of the worker
(:mod:`reef.reading.sandbox`, :mod:`reef.reading.child`): uploaded files are
untrusted. The text formats are read by Reef's own code
(:mod:`reef.reading.formats`); a scan page is read by a :class:`ScanReader`.

A read that fails raises a :class:`ReadFailed`, and only its class name is
kept, in ``attachments.text_error``. No error here carries a message: what a
child process said, and what a file holds, never reach an exception, a log
line or a span.

This module holds the shared names only, so a child process importing it
pulls in nothing heavy. See ``docs/specs/2026-11-21-file-text-design.md``.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

from reef import rls


class FileKind(StrEnum):
    """What a file is, as its bytes say."""

    PDF = "pdf"
    IMAGE = "image"
    MARKDOWN = "markdown"
    PLAIN = "plain"
    HTML = "html"
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"
    OTHER = "other"


TEXT_FORMATS: frozenset[FileKind] = frozenset(
    {
        FileKind.MARKDOWN,
        FileKind.PLAIN,
        FileKind.HTML,
        FileKind.DOCX,
        FileKind.XLSX,
        FileKind.PPTX,
    }
)
"""The formats Reef reads whole with its own code. They have no pages of their
own: their text is cut between paragraphs into pages."""


@dataclass(frozen=True)
class ScanPage:
    """One scanned page, ready for a :class:`ScanReader`.

    ``number`` counts from 1 within its file. ``image`` is a PNG, turned
    upright, without metadata. ``source`` says whether it came from a PDF page
    or from an image file.
    """

    number: int
    image: bytes
    source: Literal["pdf", "image"]


@dataclass(frozen=True)
class Reading:
    """What a reader made of one page.

    ``reader`` is one of :data:`reef.rls.READERS`; ``model`` names the model
    or version behind it. ``confidence`` runs from 0 to 1 when the reader
    gives one. The token counts are for readers that are billed by them.
    """

    text: str
    reader: str
    model: str
    confidence: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class ScanReader(Protocol):
    """Reads the text on one scanned page.

    ``name`` is the reader's value in :data:`reef.rls.READERS`.
    ``leaves_reef`` is True when the page is sent outside Reef's own servers;
    such a reader is only ever called through the gate.
    """

    name: str
    leaves_reef: bool

    async def read(self, page: ScanPage) -> Reading:
        """Read one page.

        :param page: the page
        :returns: its text, and what reading it took
        :raises ReadFailed: when the page cannot be read
        """
        ...


READER_NAMES: dict[str, str] = {reader: "Reef" for reader in rls.READERS}
"""Who read a page, as a person is told: every reader runs on Reef's own
servers."""


class ReadFailed(Exception):
    """A file could not be read. Its class name is stored, never a message.

    The file is marked ``unreadable`` and not read again.
    """


class Unreadable(ReadFailed):
    """The file could not be parsed."""


class Encrypted(ReadFailed):
    """The file is locked with a password."""


class TooLarge(ReadFailed):
    """The file, or what it unpacks to, is larger than Reef reads."""


class ReadTimedOut(ReadFailed):
    """Reading took longer than it may."""


class ChildCrashed(ReadFailed):
    """The child process ended without a usable answer."""


class SandboxUnavailable(Exception):
    """The child process cannot be started safely.

    Not a :class:`ReadFailed`: the file is not at fault. The job fails, and
    the queue tries it again.
    """
