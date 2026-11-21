"""Files for the reading tests, made in code.

Nothing binary goes into git: every test file is generated here, from bytes
that say what they are. A signature is enough where only the kind matters;
later readers add whole files built with the same libraries Reef reads them
with.
"""

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
