"""Read files on Reef's own servers: the worker's side of every child call.

Each function here starts one child (:func:`reef.reading.sandbox.run_child`)
and turns its answer into a :class:`~reef.reading.Reading`, a
:class:`~reef.reading.ScanPage` or a count. The worker never parses a file
or decodes an image itself. Each call may take ``reading.page_seconds``; a
text format gets that time for the whole file.

No text that reaches the worker holds a NUL character, whatever the child
says: Postgres cannot store one.
"""

import asyncio
import base64
import binascii
import functools
import os
import shutil
import subprocess

from reef.config import get_settings
from reef.reading import (
    TEXT_FORMATS,
    ChildCrashed,
    FileKind,
    Reading,
    SandboxUnavailable,
    ScanPage,
)
from reef.reading.sandbox import child_environment, run_child

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _no_nul(text: str) -> str:
    """Drop NUL characters.

    :param text: a text from the child
    :returns: the text without them
    """
    return text.replace("\x00", "")


def _png(answer: dict) -> bytes:
    """Take a PNG out of a child's answer, without decoding the image.

    :param answer: ``{"png": base64}``
    :returns: the PNG's bytes
    :raises ChildCrashed: when it is not base64, or not a PNG
    """
    try:
        png = base64.b64decode(answer["png"], validate=True)
    except (binascii.Error, ValueError):
        png = b""
    if not png.startswith(_PNG_SIGNATURE):
        raise ChildCrashed
    return png


async def survey(data: bytes, kind: FileKind) -> int:
    """Count a file's pages: a PDF's pages, or an image's frames.

    :param data: the file's bytes
    :param kind: ``PDF`` or ``IMAGE``
    :returns: the number of pages, at least one
    :raises ReadFailed: when the file cannot be read
    :raises SandboxUnavailable: when the child cannot be started safely
    :raises ValueError: for a kind without pages of its own
    """
    if kind not in (FileKind.PDF, FileKind.IMAGE):
        raise ValueError(f"no pages of its own: {kind}")
    settings = get_settings().reading
    answer = await run_child(
        ["survey", kind.value], data, seconds=settings.page_seconds
    )
    return answer["pages"]


async def open_page(
    data: bytes, kind: FileKind, number: int, *, dpi: int
) -> Reading | ScanPage:
    """Open one page of a PDF or an image.

    :param data: the file's bytes
    :param kind: ``PDF`` or ``IMAGE``
    :param number: the page, from 1
    :param dpi: the resolution a PDF's scan page is rendered at
    :returns: a :class:`Reading` of a PDF page's text layer, read by
        ``pdf-text`` with no model but PDFium; else a :class:`ScanPage`, for
        a PDF page without one or for an image or TIFF frame
    :raises ReadFailed: when the page cannot be read
    :raises SandboxUnavailable: when the child cannot be started safely
    :raises ValueError: for a kind without pages of its own
    """
    settings = get_settings().reading
    match kind:
        case FileKind.PDF:
            args = ["page", str(number), str(dpi), str(settings.page_chars)]
            answer = await run_child(args, data, seconds=settings.page_seconds)
            if "text" in answer:
                text = _no_nul(answer["text"])
                return Reading(text=text, reader="pdf-text", model="pdfium")
            return ScanPage(number=number, image=_png(answer), source="pdf")
        case FileKind.IMAGE:
            answer = await run_child(
                ["frame", str(number)], data, seconds=settings.page_seconds
            )
            return ScanPage(number=number, image=_png(answer), source="image")
    raise ValueError(f"no pages of its own: {kind}")


async def read_parts(data: bytes, kind: FileKind) -> tuple[list[str], int]:
    """Read a text format whole, in one child call, into its pages.

    :param data: the file's bytes
    :param kind: one of :data:`reef.reading.TEXT_FORMATS`
    :returns: at most ``reading.pages_per_file`` parts of at most
        ``reading.page_chars`` characters, and how many pages the file has.
        For Word, Excel and PowerPoint that count stops at
        ``pages_per_file + 1``, which means "more".
    :raises ReadFailed: when the file cannot be read
    :raises SandboxUnavailable: when the child cannot be started safely
    :raises ValueError: for a kind that is not a text format
    """
    if kind not in TEXT_FORMATS:
        raise ValueError(f"not a text format: {kind}")
    settings = get_settings().reading
    args = [
        "parts",
        kind.value,
        str(settings.page_chars),
        str(settings.pages_per_file),
    ]
    answer = await run_child(args, data, seconds=settings.page_seconds)
    return [_no_nul(part) for part in answer["parts"]], answer["pages"]


@functools.cache
def tesseract_command(languages: str) -> str:
    """Find Tesseract once, and check it reads every language asked for.

    Checked in the child's own environment, so the child finds the same
    language files. A missing program or language is not a file's fault: no
    file is marked unreadable for it.

    :param languages: the languages, joined by ``+``
    :returns: Tesseract's absolute path
    :raises SandboxUnavailable: when Tesseract or a language is missing
    """
    found = shutil.which("tesseract")
    if found is None:
        raise SandboxUnavailable
    program = os.path.abspath(found)
    try:
        listed = subprocess.run(
            [program, "--list-langs"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=child_environment(),
            timeout=30,
            check=False,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError) as error:
        raise SandboxUnavailable from error
    # The first line is a heading; the rest name one language each.
    known = set(listed.splitlines()[1:])
    if not set(languages.split("+")) <= known:
        raise SandboxUnavailable
    return program


class TesseractReader:
    """Reads a scan page with Tesseract, on Reef's own servers.

    Tesseract runs in the child, one thread to a page, in the languages and
    at the resolution the settings give.
    """

    name = "tesseract"
    leaves_reef = False

    async def read(self, page: ScanPage) -> Reading:
        """Read one scan page.

        :param page: the page
        :returns: its text, empty when it held no words or only noise, with
            the mean word confidence and Tesseract's version and languages
        :raises ReadFailed: when the page cannot be read
        :raises SandboxUnavailable: when Tesseract, a language or the child
            is missing
        """
        settings = get_settings().reading
        # Once per worker; off the event loop, which other pages share.
        tesseract = await asyncio.to_thread(tesseract_command, settings.languages)
        args = [
            "ocr",
            tesseract,
            settings.languages,
            str(settings.dpi),
            str(settings.page_chars),
        ]
        answer = await run_child(args, page.image, seconds=settings.page_seconds)
        return Reading(
            text=_no_nul(answer["text"]),
            reader=self.name,
            model=answer["model"],
            confidence=answer["confidence"],
        )
