"""Files for the reading tests, made in code.

Nothing binary goes into git: every test file is generated here, from bytes
that say what they are. A signature is enough where only the kind matters;
later readers add whole files built with the same libraries Reef reads them
with.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

PDF_HEAD = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n"
"""The start of a PDF: its version line and a binary comment."""

JPEG_HEAD = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
"""The start of a JPEG: start of image, then a JFIF header."""

PNG_HEAD = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
"""The start of a PNG: its signature and the first chunk's header."""

WEBP_HEAD = b"RIFF\x24\x00\x00\x00WEBPVP8 "
"""The start of a WebP: a RIFF container of type WEBP."""

TIFF_LITTLE_HEAD = b"II*\x00\x08\x00\x00\x00"
"""The start of a little-endian TIFF."""

TIFF_BIG_HEAD = b"MM\x00*\x00\x00\x00\x08"
"""The start of a big-endian TIFF."""

ZIP_HEAD = b"PK\x03\x04\x14\x00\x06\x00"
"""The start of a ZIP archive, which every Office file is."""


def paragraphs(count: int, chars: int) -> list[str]:
    """Make paragraphs that can be told apart.

    :param count: how many paragraphs
    :param chars: the length of each one
    :returns: the paragraphs, each starting with its own number
    """
    return [f"{number:05d}".ljust(chars, "x") for number in range(count)]


def long_text(count: int, chars: int) -> str:
    """Make a text of numbered paragraphs, separated by blank lines.

    :param count: how many paragraphs
    :param chars: the length of each one
    :returns: the text
    """
    return "\n\n".join(paragraphs(count, chars))


def html_page(body: str) -> bytes:
    """Wrap a body in an HTML page with a script and a style.

    :param body: the HTML inside ``<body>``
    :returns: the page as UTF-8 bytes
    """
    return (
        "<!doctype html>\n<html><head><title>Plan</title>\n"
        "<style>p { color: red } /* ZEBRAQUILT */</style>\n"
        '<script>var hidden = "ZEBRAQUILT";</script>\n'
        f"</head><body>{body}</body></html>\n"
    ).encode()


def zip_bytes(parts: dict[str, bytes]) -> bytes:
    """Pack named parts into a ZIP archive, compressed.

    :param parts: each part's name and bytes
    :returns: the archive
    """
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def replace_part(data: bytes, name: str, content: bytes) -> bytes:
    """Swap one part of an Office file for other bytes.

    :param data: the Office file
    :param name: the part's name inside the archive
    :param content: its new bytes
    :returns: the file with the part replaced
    """
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        parts = {info.filename: archive.read(info) for info in archive.infolist()}
    parts[name] = content
    return zip_bytes(parts)


def docx_bytes(blocks: list[str | list[list[str]]]) -> bytes:
    """Build a Word file with python-docx.

    :param blocks: in body order, a paragraph as a string, or a table as a
        list of rows, each a list of cell texts
    :returns: the ``.docx`` file
    """
    import io

    from docx import Document

    document = Document()
    for block in blocks:
        if isinstance(block, str):
            document.add_paragraph(block)
            continue
        table = document.add_table(rows=len(block), cols=len(block[0]))
        for row, texts in zip(table.rows, block, strict=True):
            for cell, text in zip(row.cells, texts, strict=True):
                cell.text = text
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def xlsx_bytes(sheets: dict[str, list[list[object]]]) -> bytes:
    """Build an Excel file with openpyxl.

    A string that starts with ``=`` is stored as a formula, with no cached
    result, as openpyxl writes it.

    :param sheets: each sheet's name and its rows of cell values
    :returns: the ``.xlsx`` file
    """
    import io

    from openpyxl import Workbook

    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def pptx_bytes(slides: list[list[str]]) -> bytes:
    """Build a PowerPoint file with python-pptx.

    :param slides: for each slide, the texts of its text boxes, in order
    :returns: the ``.pptx`` file
    """
    import io

    from pptx import Presentation
    from pptx.util import Inches

    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    for texts in slides:
        slide = presentation.slides.add_slide(blank)
        for number, text in enumerate(texts):
            box = slide.shapes.add_textbox(Inches(1), Inches(1 + number), Inches(5), 1)
            box.text_frame.text = text
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def forged_directory_docx() -> bytes:
    """Build a Word file whose end record hides entries from a count-based walk.

    The end record says the directory has one entry. A second one, which
    claims to unpack to 300 MB, follows it in the directory: ``zipfile`` walks
    the directory by its byte size and reads both. Only headers are forged;
    the file stays small.

    :returns: the archive
    """
    import struct

    data = bytearray(zip_bytes({"word/document.xml": b"<x/>", "big.xml": b"<x/>"}))
    second = data.rfind(b"PK\x01\x02")
    struct.pack_into("<L", data, second + 24, 300_000_000)
    end = data.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", data, end + 8, 1, 1)
    return bytes(data)


A4_POINTS = (595, 842)
"""An A4 page, in PDF points of 1/72 inch."""

SCAN_DPI = 200
"""The resolution scans are drawn at, as Reef renders them."""

A4_PIXELS = (1654, 2339)
"""An A4 page at 200 dpi."""


def _pdf_string(text: str) -> bytes:
    """Write a text as a PDF string, in Helvetica's own encoding.

    :param text: the text
    :returns: the string, parentheses and backslashes escaped
    """
    raw = text.encode("cp1252")
    for special in (b"\\", b"(", b")"):
        raw = raw.replace(special, b"\\" + special)
    return b"(" + raw + b")"


def text_pdf(pages: list[str], *, size: tuple[float, float] = A4_POINTS) -> bytes:
    """Write a PDF by hand whose pages carry a text layer in Helvetica.

    :param pages: each page's text; a newline starts a new line, and an empty
        text gives a page with no text at all
    :param size: every page's width and height, in points
    :returns: the PDF
    """
    width, height = size
    font = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )
    first_page = 4
    kids = " ".join(f"{first_page + 2 * n} 0 R" for n in range(len(pages)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode(),
        font,
    ]
    for number, text in enumerate(pages):
        lines = [_pdf_string(line) + b" Tj T*" for line in text.split("\n") if line]
        stream = b"BT /F1 24 Tf 72 " + str(height - 72).encode() + b" Td 30 TL "
        stream += b" ".join(lines) + b" ET"
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 {width} {height}] "
                "/Resources << /Font << /F1 3 0 R >> >> "
                f"/Contents {first_page + 2 * number + 1} 0 R >>"
            ).encode()
        )
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    table = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{table}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _drawn(lines: list[str], size: tuple[int, int]) -> "Image.Image":
    """Draw lines of black text on a white page, as a scanner would see them.

    :param lines: the lines, top to bottom
    :param size: the page's width and height, in pixels
    :returns: a greyscale image
    """
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("L", size, 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=48)
    for number, line in enumerate(lines):
        draw.text((120, 150 + 90 * number), line, fill=0, font=font)
    return image


def scan_pdf(lines: list[str]) -> bytes:
    """Make a scanned A4 page: a picture of text, with no text layer.

    :param lines: the lines on the page, drawn at 48 px
    :returns: a one-page PDF, written by Pillow at 200 dpi
    """
    import io

    buffer = io.BytesIO()
    _drawn(lines, A4_PIXELS).save(buffer, "PDF", resolution=SCAN_DPI)
    return buffer.getvalue()


def scan_image(
    lines: list[str],
    fmt: str,
    exif_orientation: int | None = None,
    *,
    size: tuple[int, int] = (1200, 900),
    frames: int = 1,
    mode: str = "L",
) -> bytes:
    """Make a photo or scan of text, as a camera or scanner writes it.

    With an orientation, the EXIF data also holds a GPS position, as a
    phone's photo does.

    :param lines: the lines, drawn at 48 px
    :param fmt: ``JPEG``, ``PNG``, ``WEBP`` or ``TIFF``
    :param exif_orientation: the EXIF orientation tag, if any
    :param size: the image's stored width and height
    :param frames: how many frames, for a TIFF; each repeats the lines
    :param mode: the image's colour mode, such as ``L``, ``RGBA`` or ``I;16``
    :returns: the file
    """
    import io

    from PIL import ExifTags, Image

    image = _drawn(lines, size)
    if mode == "I;16":
        image = image.point(lambda value: value * 256, "I").convert("I;16")
    elif mode == "RGBA":
        # Black text on a see-through page, as a screenshot may be.
        image = Image.merge(
            "RGBA", (image, image, image, image.point(lambda v: 255 - v))
        )
    elif mode != "L":
        image = image.convert(mode)
    options: dict = {}
    if exif_orientation is not None:
        exif = Image.Exif()
        exif[ExifTags.Base.Orientation] = exif_orientation
        exif[ExifTags.IFD.GPSInfo] = {
            ExifTags.GPS.GPSLatitudeRef: "N",
            ExifTags.GPS.GPSLatitude: (51.0, 3.0, 0.0),
        }
        options["exif"] = exif
    if frames > 1:
        options["save_all"] = True
        options["append_images"] = [image.copy() for _ in range(frames - 1)]
    buffer = io.BytesIO()
    image.save(buffer, fmt, **options)
    return buffer.getvalue()


def png_header(width: int, height: int) -> bytes:
    """Write a PNG that declares a size and holds no pixels.

    Reading it whole would take ``width × height`` bytes and more; its
    header alone says so.

    :param width: the declared width
    :param height: the declared height
    :returns: a signature, an IHDR for an 8-bit greyscale image, an empty
        IDAT and an IEND, each with a valid checksum
    """
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        """Write one chunk.

        :param kind: its four-letter type
        :param body: its data
        :returns: length, type, data and checksum
        """
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b""))
        + chunk(b"IEND", b"")
    )


def png_chunks(png: bytes) -> list[bytes]:
    """List a PNG's chunk types, in order.

    :param png: the PNG
    :returns: each chunk's four-letter type
    """
    import struct

    kinds: list[bytes] = []
    position = 8
    while position < len(png):
        (length,) = struct.unpack_from(">I", png, position)
        kinds.append(png[position + 4 : position + 8])
        position += 12 + length
    return kinds


def combined_pdf(*pdfs: bytes) -> bytes:
    """Join PDFs into one, page after page, with pypdf.

    :param pdfs: the PDFs, in order
    :returns: one PDF holding all their pages
    """
    import io

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for pdf in pdfs:
        writer.append(PdfReader(io.BytesIO(pdf)))
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def encrypted_pdf() -> bytes:
    """Lock a text PDF with a password, with pypdf.

    :returns: a PDF that opens only with the password
    """
    import io

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(text_pdf(["Polisnummer 4711-2026"]))))
    writer.encrypt(user_password="geheim", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
