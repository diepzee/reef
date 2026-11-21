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
