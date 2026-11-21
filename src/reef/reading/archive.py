"""Look inside an Office file's ZIP directory without unpacking anything.

An Office file is a ZIP archive, and an upload is untrusted: a few kilobytes
can unpack to gigabytes. This module reads the archive's central directory
with :mod:`struct` and nothing else, so it is cheap and safe to run before
any library opens the bytes, and in the worker itself, which uses it to
bound the child's answer (:func:`reef.reading.sandbox.office_answer_bytes`).

The walk must agree with how :mod:`zipfile` reads the same bytes, since
the libraries use it: it ends exactly at the end record, and a ZIP64
locator is refused. Every limit sits below the point where ZIP64 starts, so
ZIP64 archives are
refused as too large.
"""

import struct
from collections.abc import Iterator

from reef.reading import FileKind, TooLarge, Unreadable

ZIP_MAX_BYTES = 200_000_000
"""The most an archive may unpack to, by what its directory declares."""

ZIP_MAX_ENTRIES = 10_000
"""The most entries an archive may have."""

MAIN_PARTS: dict[FileKind, str] = {
    FileKind.DOCX: "word/document.xml",
    FileKind.XLSX: "xl/workbook.xml",
    FileKind.PPTX: "ppt/presentation.xml",
}
"""The part every file of a format has."""

_END_OF_DIRECTORY = b"PK\x05\x06"
_END_SIZE = 22
_ENTRY = b"PK\x01\x02"
_ENTRY_SIZE = 46
_COMMENT_MAX = 65_535
_ZIP64_LOCATOR = b"PK\x06\x07"
_UNSET = 0xFFFFFFFF
_UNSET_COUNT = 0xFFFF


def entries(
    data: bytes, *, max_entries: int = ZIP_MAX_ENTRIES
) -> Iterator[tuple[bytes, int]]:
    """Walk the central directory.

    :param data: the archive's bytes
    :param max_entries: the most entries to allow
    :returns: each entry's raw name and its declared unpacked size
    :raises Unreadable: when the bytes are not a well-formed ZIP directory
    :raises TooLarge: when the directory holds more than ``max_entries``
        entries, or an entry needs ZIP64
    """
    end = data.rfind(_END_OF_DIRECTORY, max(0, len(data) - _END_SIZE - _COMMENT_MAX))
    if end < 0 or end + _END_SIZE > len(data):
        raise Unreadable
    count, size, offset = struct.unpack_from("<10xHLL", data, end)
    if data[max(0, end - 20) : max(0, end - 16)] == _ZIP64_LOCATOR:
        raise TooLarge
    if count == _UNSET_COUNT or size == _UNSET or offset == _UNSET:
        raise TooLarge
    if count > max_entries:
        raise TooLarge
    # Bytes before the archive, as in a self-extracting file, move the
    # directory; the end record is the fixed point.
    position = end - size
    if position < 0:
        raise Unreadable
    for _ in range(count):
        head = data[position : position + _ENTRY_SIZE]
        if len(head) < _ENTRY_SIZE or not head.startswith(_ENTRY):
            raise Unreadable
        unpacked, name_len, extra_len, comment_len = struct.unpack_from(
            "<LHHH", head, 24
        )
        if unpacked == _UNSET:
            raise TooLarge
        name_start = position + _ENTRY_SIZE
        name = data[name_start : name_start + name_len]
        if len(name) < name_len:
            raise Unreadable
        yield name, unpacked
        position = name_start + name_len + extra_len + comment_len
    # The directory must end where its end record starts: ``zipfile`` walks
    # by byte size, not by count, and entries left over would be its to read.
    if position != end:
        raise Unreadable


def unpacked_size(data: bytes, *, max_entries: int = ZIP_MAX_ENTRIES) -> int:
    """Add up what an archive says it unpacks to.

    :param data: the archive's bytes
    :param max_entries: the most entries to allow
    :returns: the sum of the declared sizes, in bytes
    :raises Unreadable: when the bytes are not a well-formed ZIP directory
    :raises TooLarge: when it has too many entries
    """
    return sum(size for _, size in entries(data, max_entries=max_entries))


def check_archive(
    data: bytes,
    kind: FileKind,
    *,
    max_bytes: int = ZIP_MAX_BYTES,
    max_entries: int = ZIP_MAX_ENTRIES,
) -> int:
    """Refuse an archive that is too big or is not the Office format it claims.

    Reads the central directory only; nothing is unpacked.

    :param data: the file's bytes
    :param kind: ``DOCX``, ``XLSX`` or ``PPTX``
    :param max_bytes: the most the entries may unpack to in all
    :param max_entries: the most entries the archive may have
    :returns: what the archive unpacks to, in bytes
    :raises TooLarge: past either cap
    :raises Unreadable: when it is not a ZIP, or lacks the format's main part
    """
    total = 0
    names: set[bytes] = set()
    for name, size in entries(data, max_entries=max_entries):
        total += size
        if total > max_bytes:
            raise TooLarge
        names.add(name)
    if MAIN_PARTS[kind].encode() not in names:
        raise Unreadable
    return total
