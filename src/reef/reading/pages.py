"""Read PDF pages and images, and run Tesseract on a scan, in the child.

A PDF page whose text layer holds :data:`SCAN_TEXT_CHARS` or more non-space
characters is a text page, and that layer is its text. Any other page is a
scan page: it is rendered and read by a :class:`~reef.reading.ScanReader`.
An image file, or one frame of a TIFF, is a scan page too, turned upright
from its EXIF data.

A scan page leaves here as a greyscale PNG and nothing else: no EXIF, no
GPS position, no colour profile. Greyscale is all OCR uses, and it keeps the
PNG, and the bound on the child's answer, small. The child prints the PNG
as raw bytes after its answer's JSON (see :mod:`reef.reading.child`).

Every page is held to :data:`MAX_PIXELS` before a pixel is decoded or
rendered, from an image's header or a PDF page's declared size:

- a PDF page too large at the configured resolution is rendered at a lower
  one that fits, down to :data:`MIN_SCAN_DPI`: a house plan on A0 is
  62 million pixels at 200 dpi, and fits at 160;
- a JPEG too large is decoded at a half, a quarter or an eighth of its size;
- anything that still does not fit is ``TooLarge``. A PDF can declare a
  page 200 inches wide.

A page with a side under :data:`MIN_SIDE` pixels is ``Unreadable``: it holds
no line of text, and a page that thin would let a PNG outgrow its pixels.

Every function here runs in the child process (:mod:`reef.reading.child`),
never in the worker: the bytes are untrusted. The libraries are imported
inside the functions, so the worker can import the limits without them.
"""

import contextlib
import io
import math
import re
import struct
import subprocess
from collections.abc import Iterator
from typing import TYPE_CHECKING

from reef.reading import (
    Encrypted,
    FileKind,
    OutOfProcesses,
    ReadFailed,
    TooLarge,
    Unreadable,
    out_of_processes,
)

if TYPE_CHECKING:
    import pypdfium2 as pdfium
    from PIL import Image, ImageFile

SCAN_TEXT_CHARS = 20
"""A PDF page whose text layer has fewer non-space characters is a scan page.
A page number or a stamp is not the page's text."""

MAX_PIXELS = 40_000_000
"""The most pixels a page may have, checked before decoding or rendering.
An A4 page at 200 dpi has 3.9 million. A larger PDF page is rendered at a
lower resolution, down to :data:`MIN_SCAN_DPI`, and a larger JPEG is decoded
smaller; past that, and for any other image, the page is ``TooLarge``."""

MIN_SCAN_DPI = 100
"""The lowest resolution a PDF page is rendered at to fit :data:`MAX_PIXELS`.
Below it, text is too small to read, and the page is ``TooLarge``."""

MIN_SIDE = 64
"""The fewest pixels a page may have on either side."""

NOISE_WORDS = 3
"""A scan read into fewer words is stored with empty text."""

NOISE_CONFIDENCE = 40.0
"""A scan whose mean word confidence, from 0 to 100, is lower is noise, and
is stored with empty text."""

IMAGE_FORMATS = ("JPEG", "PNG", "WEBP", "TIFF")
"""The image formats Reef reads. Pillow opens no other: some of its formats
start other programs."""

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
"""How every PNG starts."""

_DRAFT_FORMATS = ("JPEG", "MPO")
"""What Pillow opens a JPEG as. Only these decode at a smaller scale."""

_DRAFT_SCALES = (2, 4, 8)
"""The scales a JPEG can be decoded at, smallest reduction first."""

_POINTS_PER_INCH = 72
_LINE_END = re.compile(r"\r\n?")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")
"""Control characters other than tab, line feed and form feed. NUL is one:
Postgres cannot store it."""


@contextlib.contextmanager
def _plain_failures() -> Iterator[None]:
    """Turn whatever a library raises into a failure class, its message dropped.

    :raises ReadFailed: as raised
    :raises TooLarge: for a lack of memory
    :raises OutOfProcesses: when a process or thread could not start, such
        as Tesseract: the worker is short of room, the file is not at fault
    :raises Unreadable: for anything else
    """
    try:
        yield
    except ReadFailed:
        raise
    except MemoryError:
        raise TooLarge from None
    except Exception as error:  # noqa: BLE001 - a library's error is never kept
        if out_of_processes(error):
            raise OutOfProcesses from None
        raise Unreadable from None


def plain_text(text: str, chars: int) -> str:
    """Make a page's text fit for storing.

    Line endings become ``\\n``. Control characters go, NUL among them, so
    every character left is at most four bytes in the child's answer.

    :param text: the text as a library gave it
    :param chars: the most characters to keep
    :returns: the text, trimmed, at most ``chars`` long
    """
    return _CONTROL.sub("", _LINE_END.sub("\n", text)).strip()[:chars]


def _check_pixels(width: int, height: int) -> None:
    """Refuse a page with too many pixels, or too few on a side.

    :param width: its width, in pixels
    :param height: its height, in pixels
    :raises TooLarge: past :data:`MAX_PIXELS`
    :raises Unreadable: when either side is under :data:`MIN_SIDE`
    """
    if width < MIN_SIDE or height < MIN_SIDE:
        raise Unreadable
    if width * height > MAX_PIXELS:
        raise TooLarge


def _png(image: "Image.Image") -> bytes:
    """Encode a page as a greyscale PNG with nothing but its pixels.

    :param image: the page, upright
    :returns: the PNG
    """
    from PIL import Image

    if image.has_transparency_data:
        # A see-through page is read on white, as a viewer shows it.
        white = Image.new("RGBA", image.size, "white")
        image = Image.alpha_composite(white, image.convert("RGBA"))
    if image.mode.startswith("I;16"):
        # Pillow would clip 16-bit values to white rather than scale them.
        image = image.convert("I").point(lambda value: value / 256, "L")
    image = image.convert("L")
    # A fresh image: no EXIF, colour profile or other chunk comes along.
    clean = Image.frombytes("L", image.size, image.tobytes())
    buffer = io.BytesIO()
    clean.save(buffer, "PNG", compress_level=1)
    return buffer.getvalue()


def _open_pdf(data: bytes) -> "pdfium.PdfDocument":
    """Open a PDF.

    :param data: the file's bytes
    :returns: the document
    :raises Encrypted: when it needs a password, or uses a lock PDFium lacks
    :raises Unreadable: when it is not a PDF PDFium can open
    """
    import pypdfium2 as pdfium

    try:
        return pdfium.PdfDocument(data)
    except pdfium.PdfiumError as error:
        locked = error.err_code in (
            pdfium.raw.FPDF_ERR_PASSWORD,
            pdfium.raw.FPDF_ERR_SECURITY,
        )
    raise Encrypted if locked else Unreadable


def _open_image(data: bytes) -> "ImageFile.ImageFile":
    """Open an image and read its header, decoding nothing.

    :param data: the file's bytes
    :returns: the image
    :raises TooLarge: when Pillow's own check refuses it
    """
    from PIL import Image

    # Reef's check (:func:`_fit`) is the one that counts. Pillow's stays as
    # a backstop, far enough above to let a JPEG an eighth the size fit.
    Image.MAX_IMAGE_PIXELS = 64 * MAX_PIXELS
    try:
        return Image.open(io.BytesIO(data), formats=IMAGE_FORMATS)
    except Image.DecompressionBombError:
        raise TooLarge from None


def _fit(image: "ImageFile.ImageFile") -> None:
    """Hold the current frame to :data:`MAX_PIXELS`, from its header.

    A JPEG too large is set to decode at the smallest reduction that fits:
    the decoder then never holds the full size.

    :param image: the image, at the frame to read
    :raises TooLarge: when the frame does not fit
    :raises Unreadable: when a side is under :data:`MIN_SIDE`
    """
    width, height = image.size
    if width * height > MAX_PIXELS and image.format in _DRAFT_FORMATS:
        for scale in _DRAFT_SCALES:
            if math.ceil(width / scale) * math.ceil(height / scale) <= MAX_PIXELS:
                image.draft("L", (max(1, width // scale), max(1, height // scale)))
                break
    _check_pixels(*image.size)


def _frames(image: "ImageFile.ImageFile", max_pages: int) -> int:
    """Count an image's pages, stopping once there are more than allowed.

    :param image: the image
    :param max_pages: the most pages that will be read
    :returns: the frames of a TIFF, at most ``max_pages + 1``, which means
        "more"; 1 for any other image
    """
    if image.format != "TIFF":
        return 1
    count = 1
    while count <= max_pages:
        try:
            image.seek(count)
        except EOFError:
            break
        count += 1
    return count


def survey(data: bytes, kind: FileKind, max_pages: int) -> int:
    """Count a PDF's pages or an image's frames.

    Only a TIFF has more than one frame that counts: an animated PNG or WebP
    is its first frame.

    :param data: the file's bytes
    :param kind: ``PDF`` or ``IMAGE``
    :param max_pages: the most pages that will be read
    :returns: the number of pages; for a TIFF at most ``max_pages + 1``
    :raises Encrypted: for a PDF locked with a password
    :raises TooLarge: for an image whose first frame is too large
    :raises Unreadable: for a file that cannot be opened
    :raises ValueError: for another kind
    """
    with _plain_failures():
        match kind:
            case FileKind.PDF:
                return len(_open_pdf(data))
            case FileKind.IMAGE:
                image = _open_image(data)
                _fit(image)
                return _frames(image, max_pages)
    raise ValueError(f"no pages here: {kind}")


def _side(points: float, dpi: int) -> int:
    """Return a PDF page's side in pixels, as PDFium's renderer sizes it.

    :param points: the side, in points of 1/72 inch
    :param dpi: the resolution
    :returns: the side, in pixels
    """
    return math.ceil(points * dpi / _POINTS_PER_INCH)


def fitting_dpi(width: float, height: float, dpi: int) -> int:
    """Choose the resolution a PDF page is rendered at.

    :param width: the page's width, in points
    :param height: its height, in points
    :param dpi: the configured resolution
    :returns: ``dpi``, or the highest lower one at which the page fits
        :data:`MAX_PIXELS`
    :raises TooLarge: when that is under :data:`MIN_SCAN_DPI`, or under
        ``dpi`` if that is lower still
    :raises Unreadable: when the page has no area
    """
    if not (math.isfinite(width) and math.isfinite(height)) or min(width, height) <= 0:
        raise Unreadable
    fit = math.floor(_POINTS_PER_INCH * math.sqrt(MAX_PIXELS / (width * height)))
    used = min(dpi, fit)
    floor = min(MIN_SCAN_DPI, dpi)
    # Each side rounds up, so the square root can overshoot by a pixel row.
    while used >= floor and _side(width, used) * _side(height, used) > MAX_PIXELS:
        used -= 1
    if used < floor:
        raise TooLarge
    return used


def pdf_page(data: bytes, number: int, dpi: int, page_chars: int) -> dict:
    """Read one PDF page's text layer, or render it when it has none.

    :param data: the file's bytes
    :param number: the page, from 1
    :param dpi: the resolution a scan page is rendered at, when it fits
    :param page_chars: the most characters of text to give
    :returns: ``{"text": ...}`` for a text page; ``{"png": ..., "dpi": ...}``
        for a scan page, the PNG's bytes and the resolution it was rendered at
    :raises Encrypted: for a PDF locked with a password
    :raises TooLarge: for a scan page that fits :data:`MAX_PIXELS` only under
        :data:`MIN_SCAN_DPI`
    :raises Unreadable: for a page that does not exist or cannot be read
    """
    with _plain_failures():
        pdf = _open_pdf(data)
        if not 1 <= number <= len(pdf):
            raise Unreadable
        page = pdf[number - 1]
        text = plain_text(page.get_textpage().get_text_range(), page_chars)
        if sum(not char.isspace() for char in text) >= SCAN_TEXT_CHARS:
            return {"text": text}
        width, height = page.get_size()
        used = fitting_dpi(width, height, dpi)
        _check_pixels(_side(width, used), _side(height, used))
        image = page.render(scale=used / _POINTS_PER_INCH, grayscale=True).to_pil()
        # What was rendered, not what was planned, is what the bound counts.
        _check_pixels(*image.size)
        return {"png": _png(image), "dpi": used}


def image_frame(data: bytes, number: int) -> dict:
    """Turn one image, or one frame of a TIFF, into an upright PNG.

    :param data: the file's bytes
    :param number: the frame, from 1
    :returns: ``{"png": ...}``, the PNG's bytes
    :raises TooLarge: for a frame that does not fit :data:`MAX_PIXELS`
    :raises Unreadable: for a frame that does not exist or cannot be decoded
    """
    from PIL import ImageOps

    with _plain_failures():
        image = _open_image(data)
        if number != 1 and image.format != "TIFF":
            raise Unreadable
        image.seek(number - 1)
        # Each frame of a TIFF has a size of its own.
        _fit(image)
        return {"png": _png(ImageOps.exif_transpose(image))}


def _check_png(png: bytes) -> None:
    """Refuse a PNG that cannot be a page, from its header alone.

    :param png: the PNG
    :raises TooLarge: past :data:`MAX_PIXELS`
    :raises Unreadable: when the bytes do not start like a PNG, or a side is
        under :data:`MIN_SIDE`
    """
    if not png.startswith(PNG_SIGNATURE) or png[12:16] != b"IHDR":
        raise Unreadable
    _check_pixels(*struct.unpack_from(">II", png, 16))


def words(tsv: str) -> list[tuple[tuple[int, int, int], float, str]]:
    """List the words in Tesseract's TSV output.

    A row that is not a word, or does not parse, is skipped: one bad row
    does not cost the page.

    :param tsv: the output, a header line first
    :returns: each word's block, paragraph and line, its confidence from 0
        to 100, and its text, in reading order
    """
    found: list[tuple[tuple[int, int, int], float, str]] = []
    for row in tsv.splitlines()[1:]:
        cells = row.split("\t")
        if len(cells) != 12 or cells[0] != "5":
            continue
        try:
            line = (int(cells[2]), int(cells[3]), int(cells[4]))
            confidence = float(cells[10])
        except ValueError:
            continue
        text = cells[11].strip()
        if text and math.isfinite(confidence) and confidence >= 0:
            found.append((line, min(confidence, 100.0), text))
    return found


def _layout(found: list[tuple[tuple[int, int, int], float, str]]) -> str:
    """Rebuild a page's text from its words.

    :param found: from :func:`words`
    :returns: words on a line joined by a space, lines by a newline, and
        paragraphs by a blank line
    """
    lines: dict[tuple[int, int, int], list[str]] = {}
    for line, _, text in found:
        lines.setdefault(line, []).append(text)
    paragraphs: dict[tuple[int, int], list[str]] = {}
    for (block, paragraph, _), texts in lines.items():
        paragraphs.setdefault((block, paragraph), []).append(" ".join(texts))
    return "\n\n".join("\n".join(texts) for texts in paragraphs.values())


def tsv_reading(tsv: str, page_chars: int) -> tuple[str, float | None]:
    """Turn Tesseract's TSV output into a page's text and confidence.

    A page with fewer than :data:`NOISE_WORDS` words, or a mean word
    confidence under :data:`NOISE_CONFIDENCE`, gives empty text.

    :param tsv: the output
    :param page_chars: the most characters of text to give
    :returns: the text, and the mean word confidence from 0 to 1, or None
        without words
    """
    found = words(tsv)
    if not found:
        return "", None
    confidence = sum(score for _, score, _ in found) / len(found)
    noise = len(found) < NOISE_WORDS or confidence < NOISE_CONFIDENCE
    text = "" if noise else plain_text(_layout(found), page_chars)
    return text, round(confidence / 100, 4)


def ocr(png: bytes, tesseract: str, languages: str, dpi: int, page_chars: int) -> dict:
    """Read a scan page with Tesseract.

    Tesseract runs in this process's environment and under its limits: one
    thread, no secret.

    :param png: the page, a PNG
    :param tesseract: the program's absolute path
    :param languages: the languages, joined by ``+``
    :param dpi: the resolution the page was rendered at
    :param page_chars: the most characters of text to give
    :returns: ``{"text": ..., "confidence": ...}``, as :func:`tsv_reading`
    :raises TooLarge: for a PNG with more than :data:`MAX_PIXELS`
    :raises OutOfProcesses: when Tesseract cannot be started for lack of
        processes
    :raises Unreadable: when the bytes are not a PNG, or Tesseract fails
    """
    with _plain_failures():
        _check_png(png)
        run = subprocess.run(
            [tesseract, "stdin", "stdout", "-l", languages, "--dpi", str(dpi), "tsv"],
            input=png,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if run.returncode != 0:
            raise Unreadable
        text, confidence = tsv_reading(
            run.stdout.decode("utf-8", "replace"), page_chars
        )
    return {"text": text, "confidence": confidence}
