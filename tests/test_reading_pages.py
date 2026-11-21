"""PDF pages and images, read in the child, and scans read by Tesseract."""

import io
import json
import math
import shutil
import subprocess
import sys
import time

import pytest
from filefixtures import (
    A0_POINTS,
    A4_PIXELS,
    combined_pdf,
    encrypted_pdf,
    long_text,
    png_chunks,
    png_header,
    scan_image,
    scan_pdf,
    text_pdf,
    tiff_frames,
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


@pytest.fixture(scope="module")
def tesseract_version(tesseract: str) -> str:
    """Ask Tesseract its version, as the worker names its readings.

    :param tesseract: the program's path
    :returns: the first line it prints, such as ``tesseract 5.5.4``
    """
    run = subprocess.run(
        [tesseract, "--version"], capture_output=True, text=True, check=True
    )
    return run.stdout.partition("\n")[0]


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
    assert (page.number, page.source, page.dpi) == (1, "pdf", 200)
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
    assert (page.number, page.source, page.dpi) == (1, "image", None)
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


async def test_reefs_own_check_refuses_what_pillow_would_open():
    """49 million pixels is under Pillow's own limit, and over Reef's."""
    data = png_header(7_000, 7_000)
    with pytest.raises(TooLarge):
        await local.survey(data, FileKind.IMAGE)
    with pytest.raises(TooLarge):
        await local.open_page(data, FileKind.IMAGE, 1, dpi=200)


async def test_a_tiff_frame_is_checked_after_the_seek_that_reaches_it():
    """Each frame of a TIFF has a size of its own; the second is too large."""
    data = tiff_frames([(1_200, 900), (7_000, 7_000)])
    assert await local.survey(data, FileKind.IMAGE) == 2
    assert isinstance(await local.open_page(data, FileKind.IMAGE, 1, dpi=200), ScanPage)
    with pytest.raises(TooLarge):
        await local.open_page(data, FileKind.IMAGE, 2, dpi=200)


async def test_a_jpeg_too_large_is_decoded_smaller():
    """A JPEG just over the cap is decoded at half its size, not refused."""
    data = scan_image(["Verzekering"], "JPEG", size=(6_400, 6_400))
    assert 6_400 * 6_400 > pages.MAX_PIXELS
    assert await local.survey(data, FileKind.IMAGE) == 1
    page = await local.open_page(data, FileKind.IMAGE, 1, dpi=200)
    assert isinstance(page, ScanPage)
    assert _size(page.image) == (3_200, 3_200)


async def test_a_png_too_large_is_not_decoded_smaller():
    """Only a JPEG can be decoded smaller; a PNG over the cap is refused."""
    with pytest.raises(TooLarge):
        await local.open_page(png_header(6_400, 6_400), FileKind.IMAGE, 1, dpi=200)


async def test_a_plan_too_large_at_200_dpi_is_rendered_lower():
    """An A0 plan is 62 million pixels at 200 dpi: it is rendered at 160."""
    width, height = A0_POINTS
    page = await local.open_page(
        text_pdf([""], size=A0_POINTS), FileKind.PDF, 1, dpi=200
    )
    assert isinstance(page, ScanPage)
    assert page.dpi is not None and 100 <= page.dpi <= 200
    assert page.dpi == 160
    pixels = _size(page.image)
    assert pixels == (math.ceil(width * 160 / 72), math.ceil(height * 160 / 72))
    assert pixels[0] * pixels[1] <= pages.MAX_PIXELS


async def test_a_page_too_large_even_at_100_dpi_is_refused():
    """A page that declares 200 × 200 inches fits only at 31 dpi: too fine."""
    data = text_pdf([""], size=(14_400, 14_400))
    started = time.monotonic()
    with pytest.raises(TooLarge):
        await local.open_page(data, FileKind.PDF, 1, dpi=200)
    assert time.monotonic() - started < 10
    assert (pages.MAX_PIXELS, pages.MIN_SCAN_DPI) == (40_000_000, 100)


def test_the_resolution_chosen_is_the_highest_that_fits():
    """Each side rounds up, so the resolution steps down until the page fits."""
    for width in range(600, 15_000, 377):
        for height in (842, 1_191, 2_384, 3_370, 4_768):
            try:
                dpi = pages.fitting_dpi(width, height, 200)
            except TooLarge:
                floor = math.ceil(width * 100 / 72) * math.ceil(height * 100 / 72)
                assert floor > pages.MAX_PIXELS
                continue
            assert 100 <= dpi <= 200
            pixels = math.ceil(width * dpi / 72) * math.ceil(height * dpi / 72)
            assert pixels <= pages.MAX_PIXELS
            if dpi < 200:
                over = dpi + 1
                bigger = math.ceil(width * over / 72) * math.ceil(height * over / 72)
                assert bigger > pages.MAX_PIXELS
    assert pages.fitting_dpi(595, 842, 200) == 200
    # A configured resolution under the floor is never lowered further.
    assert pages.fitting_dpi(2_384, 3_370, 90) == 90
    with pytest.raises(TooLarge):
        pages.fitting_dpi(14_400, 14_400, 200)
    with pytest.raises(TooLarge):
        pages.fitting_dpi(9_000, 9_000, 90)
    with pytest.raises(Unreadable):
        pages.fitting_dpi(0, 842, 200)


async def test_a_page_too_thin_to_hold_a_line_is_unreadable():
    """A side under 64 pixels holds no text, and would inflate its PNG."""
    with pytest.raises(Unreadable):
        await local.open_page(text_pdf([""], size=(10, 842)), FileKind.PDF, 1, dpi=200)
    data = scan_image([], "PNG", size=(40, 900))
    with pytest.raises(Unreadable):
        await local.survey(data, FileKind.IMAGE)
    with pytest.raises(Unreadable):
        await local.open_page(data, FileKind.IMAGE, 1, dpi=200)
    assert pages.MIN_SIDE == 64


async def test_a_tiff_is_counted_only_as_far_as_it_is_read(monkeypatch):
    """Past ``pages_per_file``, a TIFF's frames are not counted: one more says so."""
    data = scan_image(["Verzekering"], "TIFF", frames=3)
    assert await local.survey(data, FileKind.IMAGE) == 3
    monkeypatch.setattr(get_settings().reading, "pages_per_file", 1)
    assert await local.survey(data, FileKind.IMAGE) == 2


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

    _child_says(monkeypatch, {"text": "Polis\u0000nummer", "confidence": 0.9})
    page = ScanPage(number=1, image=b"\x89PNG", source="image")
    reading = await local.TesseractReader().read(page)
    assert reading.text == "Polisnummer"

    _child_says(monkeypatch, {"parts": ["Polis\u0000nummer"], "pages": 1})
    assert await local.read_parts(b"x", FileKind.PLAIN) == (["Polisnummer"], 1)
    assert pages.plain_text("Polis\x00nummer\r\n\x01", 100) == "Polisnummer"


async def test_tesseract_reads_dutch_french_and_english(tesseract_version: str):
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
    assert reading.model == f"{tesseract_version} nld+fra+eng"
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


async def test_tesseract_is_told_the_resolution_the_page_was_rendered_at(
    monkeypatch, tesseract: str
):
    """A plan rendered at 160 dpi is read as 160 dpi; an image, as configured."""
    asked: list[list[str]] = []

    async def run(args: list[str], data: bytes, *, seconds: float) -> dict:
        """Note the operands, and answer as a blank page would.

        :param args: the operation and its operands
        :param data: the page
        :param seconds: the time allowed
        :returns: no words
        """
        asked.append(args)
        return {"text": "", "confidence": None}

    monkeypatch.setattr(local, "run_child", run)
    reader = local.TesseractReader()
    await reader.read(ScanPage(number=1, image=b"", source="pdf", dpi=160))
    await reader.read(ScanPage(number=1, image=b"", source="image"))
    assert [args[3] for args in asked] == ["160", str(get_settings().reading.dpi)]


_TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num"
    "\tleft\ttop\twidth\theight\tconf\ttext"
)


def _tsv(*rows: str) -> str:
    """Write Tesseract's TSV output.

    :param rows: the rows after the header
    :returns: the output
    """
    return "\n".join([_TSV_HEADER, *rows]) + "\n"


def _word(place: tuple[int, int, int], confidence: object, text: str) -> str:
    """Write one word's row.

    :param place: its block, paragraph and line
    :param confidence: its confidence, as Tesseract writes it
    :param text: the word
    :returns: the row
    """
    block, paragraph, line = place
    return f"5\t1\t{block}\t{paragraph}\t{line}\t1\t0\t0\t10\t10\t{confidence}\t{text}"


def test_tesseracts_words_are_laid_out_by_block_paragraph_and_line():
    """Words share a line, lines a paragraph; paragraphs are set apart."""
    tsv = _tsv(
        "1\t1\t0\t0\t0\t0\t0\t0\t1654\t2339\t-1\t",
        "4\t1\t1\t1\t1\t0\t0\t0\t100\t10\t-1\t",
        _word((1, 1, 1), 96.5, "Polis"),
        _word((1, 1, 1), 93.5, "4711"),
        _word((1, 1, 2), 95, "Brand"),
        _word((1, 2, 1), 91, "Gent"),
        _word((2, 1, 1), -1, " "),
    )
    assert pages.tsv_reading(tsv, 20_000) == ("Polis 4711\nBrand\n\nGent", 0.94)


def test_a_bad_row_of_tesseracts_output_is_skipped():
    """A row that does not parse costs that row, not the page."""
    tsv = _tsv(
        _word((1, 1, 1), 96, "Polis"),
        _word((1, 1, 1), "abc", "Kapot"),
        _word((1, 1, 1), "nan", "Leeg"),
        "5\t1\tx\t1\t1\t1\t0\t0\t10\t10\t96\tFout",
        "5\t1\t1\t1",
        _word((1, 1, 1), 94, "nummer"),
        _word((1, 1, 1), 95, "4711"),
    )
    assert [text for _, _, text in pages.words(tsv)] == ["Polis", "nummer", "4711"]
    assert pages.tsv_reading(tsv, 20_000) == ("Polis nummer 4711", 0.95)


@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        ([], ("", None)),
        ([95, 95], ("", 0.95)),
        ([30, 35, 40], ("", 0.35)),
        ([40, 40, 40], ("een twee drie", 0.4)),
    ],
)
def test_noise_is_stored_as_empty_text(scores: list[int], expected: tuple):
    """Too few words, or a mean confidence under 40, is noise.

    :param scores: each word's confidence
    :param expected: the text and confidence kept
    """
    names = ["een", "twee", "drie"]
    tsv = _tsv(*(_word((1, 1, 1), s, names[n]) for n, s in enumerate(scores)))
    assert pages.tsv_reading(tsv, 20_000) == expected
    assert (pages.NOISE_WORDS, pages.NOISE_CONFIDENCE) == (3, 40.0)
