"""Tell what a file is from its first bytes.

The MIME type a caller gave is not used: anyone can claim any type. A binary
format carries a signature in its bytes, and that signature decides. Text
formats carry none, so Reef checks that the bytes are text, and only then
does the filename's suffix say which format it is.

What Reef does not read is :attr:`FileKind.OTHER`: PostScript, Photoshop,
SVG, XML, SQL, unknown binaries. It is found by name and description only.

This module is pure. It reads no file and no table.
"""

import re
from pathlib import PurePosixPath

from reef.reading import FileKind

HEAD_BYTES = 8_192
"""The most bytes looked at."""

_PDF_WITHIN = 1_024
"""A PDF's signature may follow some junk, but not much."""

_IMAGE_SIGNATURES = (
    b"\xff\xd8\xff",  # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"II*\x00",  # TIFF, little-endian
    b"MM\x00*",  # TIFF, big-endian
)

_ZIP = b"PK\x03\x04"

_OFFICE_SUFFIXES = {
    ".docx": FileKind.DOCX,
    ".xlsx": FileKind.XLSX,
    ".pptx": FileKind.PPTX,
}

_TEXT_SUFFIXES = {
    ".md": FileKind.MARKDOWN,
    ".markdown": FileKind.MARKDOWN,
    ".txt": FileKind.PLAIN,
    ".text": FileKind.PLAIN,
    ".html": FileKind.HTML,
    ".htm": FileKind.HTML,
}

_NOT_TEXT = re.compile(rb"[\x00-\x08\x0b\x0e-\x1f\x7f]")
"""A control byte other than tab, line feed, form feed and carriage return.
Bytes from 0x80 up are left alone: they are UTF-8 or Windows-1252 text."""

_BYTE_ORDER_MARK = b"\xef\xbb\xbf"

_TEXT_NOT_READ = (b"%!ps", b"<?xml", b"<svg")
"""Text that is a format of its own: PostScript, XML, SVG."""

_HTML_STARTS = (b"<!doctype html", b"<html")


def _is_image(head: bytes) -> bool:
    """Tell whether the bytes start like an image Reef reads.

    :param head: the first bytes of the file
    :returns: True for JPEG, PNG, WebP and TIFF
    """
    if head.startswith(_IMAGE_SIGNATURES):
        return True
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def _start(head: bytes) -> bytes:
    """Return the start of a text, for comparing with a known opening.

    :param head: the first bytes of a text file
    :returns: its first bytes, lower case, after a byte order mark and
        leading whitespace
    """
    return head.removeprefix(_BYTE_ORDER_MARK).lstrip()[:32].lower()


def likely_kind(head: bytes, filename: str) -> FileKind:
    """Tell what a file is from its first bytes and its name.

    :param head: the file's first bytes; only the first 8,192 are looked at
    :param filename: the file's name, which only tells text formats apart
    :returns: the kind. ``OTHER`` for anything Reef does not read.
    """
    head = head[:HEAD_BYTES]
    if b"%PDF-" in head[:_PDF_WITHIN]:
        return FileKind.PDF
    if _is_image(head):
        return FileKind.IMAGE
    suffix = PurePosixPath(filename).suffix.lower()
    if head.startswith(_ZIP):
        return _OFFICE_SUFFIXES.get(suffix, FileKind.OTHER)
    if _NOT_TEXT.search(head):
        return FileKind.OTHER
    start = _start(head)
    if start.startswith(_TEXT_NOT_READ):
        return FileKind.OTHER
    kind = _TEXT_SUFFIXES.get(suffix, FileKind.OTHER)
    if kind is FileKind.PLAIN and start.startswith(_HTML_STARTS):
        return FileKind.HTML
    return kind
