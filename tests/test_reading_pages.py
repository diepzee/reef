"""PDF pages and images, read in the child, and scans read by Tesseract."""

import io
import json
import shutil
import subprocess
import sys
import time

import pytest
from filefixtures import (
    A4_PIXELS,
    combined_pdf,
    encrypted_pdf,
    long_text,
    png_chunks,
    png_header,
    scan_image,
    scan_pdf,
    text_pdf,
)
from PIL import Image

from reef.config import get_settings
from reef.reading import (
    Encrypted,
    FileKind,
    Reading,
    SandboxUnavailable,
    ScanPage,
    TooLarge,
    Unreadable,
    local,
    pages,
    sandbox,
)

LANGUAGES = ("nld", "fra", "eng")
INSTALL = (
    "Install Tesseract and its languages: `brew install tesseract tesseract-lang` "
    "on a Mac, or `apt-get install tesseract-ocr tesseract-ocr-nld "
    "tesseract-ocr-fra` on Debian"
)


@pytest.fixture(scope="module")
def tesseract() -> str:
    """Find Tesseract with Dutch, French and English, or fail the test.

    Not a skip: CI installs Tesseract, and a scan reader that silently goes
    untested is how one ships broken.

    :returns: the program's path
    """
    found = shutil.which("tesseract")
    if found is None:
        pytest.fail(f"Tesseract is missing. {INSTALL}.")
    listed = subprocess.run(
        [found, "--list-langs"], capture_output=True, text=True, check=False
    ).stdout.split()
    missing = [language for language in LANGUAGES if language not in listed]
    if missing:
        pytest.fail(f"Tesseract lacks {', '.join(missing)}. {INSTALL}.")
    return found


def _size(png: bytes) -> tuple[int, int]:
    """Tell a PNG's width and height.

    :param png: the PNG
    :returns: its size, in pixels
    """
    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG"
        return image.size


def _child_says(monkeypatch, answer: dict) -> None:
    """Make every child print one answer, whatever it is asked.

    :param monkeypatch: pytest's patcher
    :param answer: what the child prints
    """
    code = f"print({json.dumps(json.dumps(answer))})"
    monkeypatch.setattr(sandbox, "child_command", lambda: [sys.executable, "-c", code])


async def test_a_text_page_is_read_from_its_layer_without_ocr(monkeypatch):
    """A page with a text layer is that layer; Tesseract never sees it."""

    async def never(self, page: ScanPage) -> Reading:
        """Stand in for Tesseract, which must not run.

        :param page: the page it was given
        :raises AssertionError: always
        """
        raise AssertionError("Tesseract read a text page")

    monkeypatch.setattr(local.TesseractReader, "read", never)
    data = text_pdf(["Polisnummer 4711-2026"])
    assert await local.survey(data, FileKind.PDF) == 1
    reading = await local.open_page(data, FileKind.PDF, 1, dpi=200)
    assert isinstance(reading, Reading)
    assert reading.text == "Polisnummer 4711-2026"
    assert (reading.reader, reading.model) == ("pdf-text", "pdfium")


async def test_a_page_without_a_layer_is_a_scan_page():
    """A page with no text is rendered: an A4 page at 200 dpi."""
    page = await local.open_page(scan_pdf(["Verzekering"]), FileKind.PDF, 1, dpi=200)
    assert isinstance(page, ScanPage)
    assert (page.number, page.source) == (1, "pdf")
    width, height = _size(page.image)
    assert abs(width - A4_PIXELS[0]) <= 2
    assert abs(height - A4_PIXELS[1]) <= 2


async def test_a_few_characters_of_text_make_a_scan_page():
    """A page number on a scan is not the page's text."""
    page = await local.open_page(text_pdf(["p. 3"]), FileKind.PDF, 1, dpi=200)
    assert isinstance(page, ScanPage)
    assert pages.SCAN_TEXT_CHARS == 20


async def test_a_mixed_pdf_is_handled_page_by_page():
    """Text, scan, text: each page is what it is."""
    data = combined_pdf(
        text_pdf(["Polisnummer 4711-2026"]),
        scan_pdf(["Verzekering"]),
        text_pdf(["Schadeaangifte ingediend op 3 mei"]),
    )
    assert await local.survey(data, FileKind.PDF) == 3
    first, second, third = [
        await local.open_page(data, FileKind.PDF, number, dpi=200)
        for number in (1, 2, 3)
    ]
    assert isinstance(first, Reading) and "4711-2026" in first.text
    assert isinstance(second, ScanPage) and second.number == 2
    assert isinstance(third, Reading) and "3 mei" in third.text


async def test_an_encrypted_pdf_is_refused():
    """A PDF locked with a password is ``Encrypted``, counted or opened."""
    data = encrypted_pdf()
    with pytest.raises(Encrypted):
        await local.survey(data, FileKind.PDF)
    with pytest.raises(Encrypted):
        await local.open_page(data, FileKind.PDF, 1, dpi=200)


async def test_a_page_past_the_last_is_unreadable():
    """Asking for a page the file lacks is a failed read, not a crash."""
    with pytest.raises(Unreadable):
        await local.open_page(
            text_pdf(["Polisnummer 4711-2026"]), FileKind.PDF, 2, dpi=200
        )
    with pytest.raises(Unreadable):
        await local.open_page(
            scan_image(["x"], "TIFF", frames=2), FileKind.IMAGE, 3, dpi=200
        )


async def test_a_text_layer_is_cut_to_the_page_size(monkeypatch):
    """A page holds at most ``page_chars`` characters of text."""
    monkeypatch.setattr(get_settings().reading, "page_chars", 1_000)
    data = text_pdf(["\n".join(["Polisnummer 4711-2026 en nog wat meer"] * 60)])
    reading = await local.open_page(data, FileKind.PDF, 1, dpi=200)
    assert isinstance(reading, Reading)
    assert len(reading.text) == 1_000
    assert reading.text.startswith("Polisnummer 4711-2026")


async def test_an_image_is_turned_upright_and_loses_its_metadata():
    """EXIF orientation 6 swaps width and height; no EXIF or GPS survives."""
    data = scan_image(["Verzekering"], "JPEG", exif_orientation=6, size=(1200, 900))
    with Image.open(io.BytesIO(data)) as original:
        assert original.getexif()[274] == 6
        assert original.getexif().get_ifd(0x8825)
    page = await local.open_page(data, FileKind.IMAGE, 1, dpi=200)
    assert isinstance(page, ScanPage)
    assert (page.number, page.source) == (1, "image")
    assert _size(page.image) == (900, 1200)
    assert set(png_chunks(page.image)) == {b"IHDR", b"IDAT", b"IEND"}
    assert b"Exif" not in page.image
    with Image.open(io.BytesIO(page.image)) as clean:
        assert clean.mode == "L"
        assert not clean.getexif()


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP", "TIFF"])
async def test_each_image_format_gives_a_page(fmt: str):
    """JPEG, PNG, WebP and TIFF are each one upright page.

    :param fmt: the image format
    """
    data = scan_image(["Verzekering"], fmt)
    assert await local.survey(data, FileKind.IMAGE) == 1
    page = await local.open_page(data, FileKind.IMAGE, 1, dpi=200)
    assert isinstance(page, ScanPage)
    assert _size(page.image) == (1200, 900)


async def test_each_tiff_frame_is_a_page():
    """A TIFF of two frames is two pages."""
    data = scan_image(["Verzekering"], "TIFF", frames=2)
    assert await local.survey(data, FileKind.IMAGE) == 2
    second = await local.open_page(data, FileKind.IMAGE, 2, dpi=200)
    assert isinstance(second, ScanPage) and second.number == 2


@pytest.mark.parametrize(("fmt", "mode"), [("PNG", "RGBA"), ("TIFF", "I;16")])
async def test_see_through_and_16_bit_images_stay_legible(fmt: str, mode: str):
    """A see-through page is read on white; 16-bit values are scaled, not clipped.

    :param fmt: the image format
    :param mode: the image's colour mode
    """
    page = await local.open_page(
        scan_image(["Verzekering"], fmt, mode=mode), FileKind.IMAGE, 1, dpi=200
    )
    assert isinstance(page, ScanPage)
    with Image.open(io.BytesIO(page.image)) as image:
        darkest, lightest = image.getextrema()
    assert lightest == 255
    assert darkest < 64


async def test_only_the_image_formats_reef_reads_are_opened():
    """A GIF, or anything else Pillow knows, is not opened at all."""
    buffer = io.BytesIO()
    Image.new("L", (40, 40), 255).save(buffer, "GIF")
    with pytest.raises(Unreadable):
        await local.survey(buffer.getvalue(), FileKind.IMAGE)
    with pytest.raises(Unreadable):
        await local.open_page(buffer.getvalue(), FileKind.IMAGE, 1, dpi=200)


async def test_an_image_too_large_is_refused_before_decoding():
    """A PNG that declares 20,000 × 20,000 pixels is refused from its header."""
    data = png_header(20_000, 20_000)
    assert len(data) < 100
    with pytest.raises(TooLarge):
        await local.survey(data, FileKind.IMAGE)
    with pytest.raises(TooLarge):
        await local.open_page(data, FileKind.IMAGE, 1, dpi=200)


async def test_a_pdf_page_too_large_is_refused_before_rendering():
    """A page that declares 200 × 200 inches would be 1.6 billion pixels."""
    data = text_pdf([""], size=(14_400, 14_400))
    started = time.monotonic()
    with pytest.raises(TooLarge):
        await local.open_page(data, FileKind.PDF, 1, dpi=200)
    assert time.monotonic() - started < 10
    assert pages.MAX_PIXELS == 40_000_000


async def test_read_parts_gives_the_parts_and_how_many_there_are(monkeypatch):
    """At most ``pages_per_file`` parts come back, with the true count."""
    data = long_text(3, 19_000).encode()
    parts, total = await local.read_parts(data, FileKind.MARKDOWN)
    assert (len(parts), total) == (3, 3)
    monkeypatch.setattr(get_settings().reading, "pages_per_file", 2)
    parts, total = await local.read_parts(data, FileKind.MARKDOWN)
    assert (len(parts), total) == (2, 3)
    assert "\n\n".join(parts) == "\n\n".join(long_text(3, 19_000).split("\n\n")[:2])


async def test_no_reading_holds_a_nul_character(monkeypatch, tesseract: str):
    """Postgres cannot store NUL, so none passes, whatever the child says."""
    _child_says(monkeypatch, {"text": "Polis\u0000nummer"})
    reading = await local.open_page(b"%PDF-", FileKind.PDF, 1, dpi=200)
    assert isinstance(reading, Reading) and reading.text == "Polisnummer"

    _child_says(
        monkeypatch, {"text": "Polis\u0000nummer", "confidence": 0.9, "model": "t"}
    )
    page = ScanPage(number=1, image=b"\x89PNG", source="image")
    reading = await local.TesseractReader().read(page)
    assert reading.text == "Polisnummer"

    _child_says(monkeypatch, {"parts": ["Polis\u0000nummer"], "pages": 1})
    assert await local.read_parts(b"x", FileKind.PLAIN) == (["Polisnummer"], 1)
    assert pages.plain_text("Polis\x00nummer\r\n\x01", 100) == "Polisnummer"


async def test_tesseract_reads_dutch_french_and_english(tesseract: str):
    """One Belgian page in three languages is read whole, and surely."""
    data = scan_pdf(
        [
            "Verzekering van de woning en de inboedel",
            "Assurance habitation et contenu du logement",
            "Insurance policy for the house and contents",
        ]
    )
    page = await local.open_page(data, FileKind.PDF, 1, dpi=200)
    assert isinstance(page, ScanPage)
    reader = local.TesseractReader()
    assert (reader.name, reader.leaves_reef) == ("tesseract", False)
    reading = await reader.read(page)
    for word in ("Verzekering", "Assurance", "Insurance"):
        assert word in reading.text
    assert reading.reader == "tesseract"
    assert reading.model.startswith("tesseract 5")
    assert reading.model.endswith("nld+fra+eng")
    assert reading.confidence is not None and reading.confidence > 0.6


@pytest.mark.parametrize("lines", [[], ["Verzekering"]])
async def test_a_page_with_no_words_is_empty_text(tesseract: str, lines: list[str]):
    """A white page, or one word alone, is stored with empty text.

    :param lines: what the page holds
    """
    page = await local.open_page(scan_pdf(lines), FileKind.PDF, 1, dpi=200)
    assert isinstance(page, ScanPage)
    reading = await local.TesseractReader().read(page)
    assert reading.text == ""
    assert (reading.confidence is None) == (not lines)


async def test_a_missing_language_is_not_the_files_fault(monkeypatch, tesseract: str):
    """Without a language, no scan is read, and none is marked unreadable."""
    monkeypatch.setattr(get_settings().reading, "languages", "nld+xx")
    page = ScanPage(number=1, image=png_header(10, 10), source="image")
    with pytest.raises(SandboxUnavailable):
        await local.TesseractReader().read(page)
