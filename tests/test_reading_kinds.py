"""A file's kind comes from its bytes; the filename only names a text format."""

import pytest
from filefixtures import (
    JPEG_HEAD,
    PDF_HEAD,
    PNG_HEAD,
    TIFF_BIG_HEAD,
    TIFF_LITTLE_HEAD,
    WEBP_HEAD,
    ZIP_HEAD,
)

from reef.reading import TEXT_FORMATS, FileKind
from reef.reading.kinds import likely_kind


@pytest.mark.parametrize(
    ("head", "filename", "kind"),
    [
        (PDF_HEAD, "x.txt", FileKind.PDF),
        (JPEG_HEAD, "scan.pdf", FileKind.IMAGE),
        (b"# Title\n", "notes.md", FileKind.MARKDOWN),
        (b"hello", "a.txt", FileKind.PLAIN),
        (b"<!doctype html><p>x", "page.txt", FileKind.HTML),
        (b"<svg", "logo.svg", FileKind.OTHER),
        (b"<?xml", "data.xml", FileKind.OTHER),
        (b"%!PS-Adobe", "x.ps", FileKind.OTHER),
        (b"8BPS", "x.psd", FileKind.OTHER),
        (b"SELECT 1;", "q.sql", FileKind.OTHER),
        (b"\x00\x01", "x.txt", FileKind.OTHER),
        (b"PK\x03\x04", "a.docx", FileKind.DOCX),
        (b"PK\x03\x04", "a.zip", FileKind.OTHER),
        # Beyond the brief: the other signatures and spellings.
        (PNG_HEAD, "photo.md", FileKind.IMAGE),
        (WEBP_HEAD, "photo.webp", FileKind.IMAGE),
        (TIFF_LITTLE_HEAD, "fax.tif", FileKind.IMAGE),
        (TIFF_BIG_HEAD, "fax.tiff", FileKind.IMAGE),
        (ZIP_HEAD, "Budget.XLSX", FileKind.XLSX),
        (ZIP_HEAD, "deck.pptx", FileKind.PPTX),
        (b"# Title\n", "README.MD", FileKind.MARKDOWN),
        (b"# Title\n", "notes.markdown", FileKind.MARKDOWN),
        (b"hello", "a.text", FileKind.PLAIN),
        (b"\xef\xbb\xbf  <!DOCTYPE HTML>", "page.txt", FileKind.HTML),
        (b"<HTML><body>x", "page.txt", FileKind.HTML),
        (b"<p>x</p>", "page.htm", FileKind.HTML),
        (b"<p>x</p>", "page.html", FileKind.HTML),
        (b"<svg xmlns='http://www.w3.org/2000/svg'>", "logo.html", FileKind.OTHER),
        (b"line\tone\r\nline two\x0c", "a.txt", FileKind.PLAIN),
        (b"caf\xe9", "a.txt", FileKind.PLAIN),
        (b"bell\x07", "a.txt", FileKind.OTHER),
        (b"escape\x1b[0m", "a.txt", FileKind.OTHER),
        (b"", "empty.md", FileKind.MARKDOWN),
        (b"hello", "no-suffix", FileKind.OTHER),
        (b"hello", "a.pdf", FileKind.OTHER),
    ],
)
def test_the_kind_comes_from_the_bytes(head: bytes, filename: str, kind: FileKind):
    """The signature wins over the name; text is named by its suffix.

    :param head: the first bytes of the file
    :param filename: the name the caller gave
    :param kind: the kind Reef should see
    """
    assert likely_kind(head, filename) == kind


def test_a_pdf_signature_counts_in_the_first_kilobyte_only():
    """Some PDFs start with junk; a signature further in is not a PDF."""
    assert likely_kind(b" " * 1_000 + PDF_HEAD, "x.bin") == FileKind.PDF
    assert likely_kind(b" " * 1_024 + PDF_HEAD, "x.txt") == FileKind.PLAIN


def test_only_the_first_eight_kilobytes_are_looked_at():
    """A control byte past the first 8,192 bytes does not change the kind."""
    assert likely_kind(b"a" * 8_192 + b"\x00", "notes.md") == FileKind.MARKDOWN
    assert likely_kind(b"a" * 8_191 + b"\x00", "notes.md") == FileKind.OTHER


def test_the_text_formats_are_the_six_reef_reads_itself():
    """PDF, images and everything else are not text formats."""
    assert {
        FileKind.MARKDOWN,
        FileKind.PLAIN,
        FileKind.HTML,
        FileKind.DOCX,
        FileKind.XLSX,
        FileKind.PPTX,
    } == TEXT_FORMATS
