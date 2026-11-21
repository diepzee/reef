"""Read PDF pages and images, and run Tesseract on a scan, in the child.

A PDF page whose text layer holds :data:`SCAN_TEXT_CHARS` or more non-space
characters is a text page, and that layer is its text. Any other page is a
scan page: it is rendered and read by a :class:`~reef.reading.ScanReader`.
An image file, or one frame of a TIFF, is a scan page too, turned upright
from its EXIF data.

A scan page leaves here as a greyscale PNG and nothing else: no EXIF, no
GPS position, no colour profile. Greyscale is all OCR uses, and it keeps the
PNG, and the bound on the child's answer, small.

A page's size is checked against :data:`MAX_PIXELS` before a pixel is
decoded or rendered: from an image's header, or from a PDF page's declared
size and the resolution. A PDF can declare a page 200 inches wide.

Every function here runs in the child process (:mod:`reef.reading.child`),
never in the worker: the bytes are untrusted. The libraries are imported
inside the functions, so the worker can import the limits without them.
"""

import base64
import contextlib
import io
import math
import re
import struct
import subprocess
from collections.abc import Iterator
from typing import TYPE_CHECKING

from reef.reading import Encrypted, FileKind, ReadFailed, TooLarge, Unreadable

if TYPE_CHECKING:
    import pypdfium2 as pdfium
    from PIL import Image

SCAN_TEXT_CHARS = 20
"""A PDF page whose text layer has fewer non-space characters is a scan page.
A page number or a stamp is not the page's text."""

MAX_PIXELS = 40_000_000
"""The most pixels a page may have, checked before decoding or rendering.
An A4 page at 200 dpi has 3.9 million; a 48-megapixel photo is refused."""

NOISE_WORDS = 3
"""A scan read into fewer words is stored with empty text."""

NOISE_CONFIDENCE = 40.0
"""A scan whose mean word confidence, from 0 to 100, is lower is noise, and
is stored with empty text."""

MODEL_CHARS = 200
"""The most characters in the name of the model that read a page."""

IMAGE_FORMATS = ("JPEG", "PNG", "WEBP", "TIFF")
"""The image formats Reef reads. Pillow opens no other: some of its formats
start other programs."""

_POINTS_PER_INCH = 72
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_LINE_END = re.compile(r"\r\n?")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")
"""Control characters other than tab, line feed and form feed. NUL is one:
Postgres cannot store it."""
_VERSION = re.compile(r"[^A-Za-z0-9._ -]")


@contextlib.contextmanager
def _plain_failures() -> Iterator[None]:
    """Turn whatever a library raises into a failure class, its message dropped.

    :raises ReadFailed: as raised
    :raises TooLarge: for a lack of memory
    :raises Unreadable: for anything else
    """
    try:
        yield
    except ReadFailed:
        raise
    except MemoryError:
        raise TooLarge from None
    except Exception:  # noqa: BLE001 - a library's error is never kept
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


def _png(image: "Image.Image") -> str:
    """Encode a page as a greyscale PNG with nothing but its pixels.

    :param image: the page, upright
    :returns: the PNG, in base64
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
    return base64.b64encode(buffer.getvalue()).decode("ascii")


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


def _open_image(data: bytes) -> "Image.ImageFile.ImageFile":
    """Open an image without decoding it.

    :param data: the file's bytes
    :returns: the image, its header read
    :raises TooLarge: when its first frame has more than :data:`MAX_PIXELS`
    """
    from PIL import Image

    # Pillow's own check, which refuses at twice this, stays as a backstop.
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        image = Image.open(io.BytesIO(data), formats=IMAGE_FORMATS)
    except Image.DecompressionBombError:
        raise TooLarge from None
    _check_pixels(*image.size)
    return image


def _check_pixels(width: int, height: int) -> None:
    """Refuse a page with too many pixels, or none.

    :param width: its width, in pixels
    :param height: its height, in pixels
    :raises TooLarge: past :data:`MAX_PIXELS`
    :raises Unreadable: when either side is under one pixel
    """
    if width < 1 or height < 1:
        raise Unreadable
    if width * height > MAX_PIXELS:
        raise TooLarge


def survey(data: bytes, kind: FileKind) -> int:
    """Count a PDF's pages or an image's frames.

    Only a TIFF has more than one frame that counts: an animated PNG or WebP
    is its first frame.

    :param data: the file's bytes
    :param kind: ``PDF`` or ``IMAGE``
    :returns: the number of pages
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
                return image.n_frames if image.format == "TIFF" else 1
    raise ValueError(f"no pages here: {kind}")


def pdf_page(data: bytes, number: int, dpi: int, page_chars: int) -> dict:
    """Read one PDF page's text layer, or render it when it has none.

    :param data: the file's bytes
    :param number: the page, from 1
    :param dpi: the resolution a scan page is rendered at
    :param page_chars: the most characters of text to give
    :returns: ``{"text": ...}`` for a text page, ``{"png": ...}`` for a scan
        page, the PNG in base64
    :raises Encrypted: for a PDF locked with a password
    :raises TooLarge: for a scan page that would render past
        :data:`MAX_PIXELS`
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
        scale = dpi / _POINTS_PER_INCH
        width, height = page.get_size()
        # As ``render`` sizes its bitmap, so the check is of what it makes.
        _check_pixels(math.ceil(width * scale), math.ceil(height * scale))
        image = page.render(scale=scale, grayscale=True).to_pil()
        _check_pixels(*image.size)
        return {"png": _png(image)}


def image_frame(data: bytes, number: int) -> dict:
    """Turn one image, or one frame of a TIFF, into an upright PNG.

    :param data: the file's bytes
    :param number: the frame, from 1
    :returns: ``{"png": ...}``, the PNG in base64
    :raises TooLarge: for a frame with more than :data:`MAX_PIXELS`
    :raises Unreadable: for a frame that does not exist or cannot be decoded
    """
    from PIL import ImageOps

    with _plain_failures():
        image = _open_image(data)
        if number != 1 and image.format != "TIFF":
            raise Unreadable
        image.seek(number - 1)
        # A frame of a TIFF has a size of its own.
        _check_pixels(*image.size)
        return {"png": _png(ImageOps.exif_transpose(image))}


def _check_png(png: bytes) -> None:
    """Refuse a PNG too large to read, from its header alone.

    :param png: the PNG
    :raises TooLarge: past :data:`MAX_PIXELS`
    :raises Unreadable: when the bytes do not start like a PNG
    """
    if not png.startswith(_PNG_SIGNATURE) or png[12:16] != b"IHDR":
        raise Unreadable
    _check_pixels(*struct.unpack_from(">II", png, 16))


def _words(tsv: str) -> list[tuple[tuple[int, int, int], float, str]]:
    """List the words in Tesseract's TSV output.

    :param tsv: the output, a header line first
    :returns: each word's block, paragraph and line, its confidence from 0
        to 100, and its text, in reading order
    """
    words: list[tuple[tuple[int, int, int], float, str]] = []
    for row in tsv.splitlines()[1:]:
        cells = row.split("\t")
        if len(cells) != 12 or cells[0] != "5":
            continue
        text, confidence = cells[11].strip(), float(cells[10])
        if text and confidence >= 0:
            line = (int(cells[2]), int(cells[3]), int(cells[4]))
            words.append((line, confidence, text))
    return words


def _layout(words: list[tuple[tuple[int, int, int], float, str]]) -> str:
    """Rebuild a page's text from its words.

    :param words: from :func:`_words`
    :returns: words on a line joined by a space, lines by a newline, and
        paragraphs by a blank line
    """
    lines: dict[tuple[int, int, int], list[str]] = {}
    for line, _, text in words:
        lines.setdefault(line, []).append(text)
    paragraphs: dict[tuple[int, int], list[str]] = {}
    for (block, paragraph, _), texts in lines.items():
        paragraphs.setdefault((block, paragraph), []).append(" ".join(texts))
    return "\n\n".join("\n".join(texts) for texts in paragraphs.values())


def _model(tesseract: str, languages: str) -> str:
    """Name the Tesseract that reads, and its languages.

    :param tesseract: the program's absolute path
    :param languages: the languages, joined by ``+``
    :returns: such as ``tesseract 5.5.4 nld+fra+eng``
    """
    run = subprocess.run(
        [tesseract, "--version"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    first = run.stdout.decode("ascii", "replace").partition("\n")[0]
    version = _VERSION.sub("", first).strip() or "tesseract"
    return f"{version} {languages}"[:MODEL_CHARS]


def ocr(png: bytes, tesseract: str, languages: str, dpi: int, page_chars: int) -> dict:
    """Read a scan page with Tesseract.

    Tesseract runs in this process's environment and under its limits: one
    thread, no secret. A page with fewer than :data:`NOISE_WORDS` words, or
    a mean word confidence under :data:`NOISE_CONFIDENCE`, gives empty text.

    :param png: the page, a PNG
    :param tesseract: the program's absolute path
    :param languages: the languages, joined by ``+``
    :param dpi: the resolution the page was rendered at
    :param page_chars: the most characters of text to give
    :returns: ``{"text": ..., "confidence": ..., "model": ...}``; the
        confidence is the mean word confidence from 0 to 1, None without
        words
    :raises TooLarge: for a PNG with more than :data:`MAX_PIXELS`
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
        words = _words(run.stdout.decode("utf-8", "replace"))
        model = _model(tesseract, languages)
    if not words:
        return {"text": "", "confidence": None, "model": model}
    confidence = sum(score for _, score, _ in words) / len(words)
    noise = len(words) < NOISE_WORDS or confidence < NOISE_CONFIDENCE
    return {
        "text": "" if noise else plain_text(_layout(words), page_chars),
        "confidence": round(confidence / 100, 4),
        "model": model,
    }
