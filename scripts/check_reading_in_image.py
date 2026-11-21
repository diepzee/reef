"""Prove, inside the built image, that files are read in the sandbox.

Run as the image's own user, root, as the worker runs::

    docker run --rm reef:ci uv run --frozen --no-dev python \
        scripts/check_reading_in_image.py

It checks what a laptop cannot: that the image has a ``reader`` user, that a
root worker hands each file to it, that the child gets none of the worker's
variables and cannot read the worker's environment, and that Tesseract reads
Dutch, French and English there. It reads a Markdown file and a scan
through :mod:`reef.reading`, as the worker will. The files are made here;
nothing is fetched. It prints counts and names only, and exits non-zero on
the first thing that is wrong.
"""

import asyncio
import io
import os
import sys

from reef.reading import FileKind, ScanPage, local, sandbox

LINES = (
    "Verzekering van de woning en de inboedel",
    "Assurance habitation et contenu du logement",
    "Insurance policy for the house and contents",
)
"""One Belgian page, in three languages."""

CANARY = "REEF_IMAGE_CHECK_CANARY"
"""A variable set in the worker that must not reach the child."""

PROBE = """
import json, os, pwd
try:
    with open(f"/proc/{os.getppid()}/environ", "rb") as environ:
        environ.read()
    readable = "yes"
except OSError:
    readable = "no"
names = ",".join(sorted(os.environ))
user = pwd.getpwuid(os.geteuid()).pw_name
print(json.dumps({"parts": [user, readable, names], "pages": 3}))
"""
"""Runs in the sandbox in place of the reader: says who it is, whether it can
read its parent's environment, and which variables it got."""


def _scan() -> bytes:
    """Draw a scanned page of text, as a phone or scanner would give it.

    :returns: a PNG
    """
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("L", (1654, 900), 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=48)
    for number, line in enumerate(LINES):
        draw.text((120, 150 + 90 * number), line, fill=0, font=font)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


async def _probe() -> list[str]:
    """Start the probe through the sandbox, as a file's reader is started.

    :returns: the child's user, whether it read the worker's environment,
        and the names of its variables
    """
    real = sandbox.child_command
    sandbox.child_command = lambda: [sys.executable, "-I", "-B", "-c", PROBE]
    try:
        answer = await sandbox.run_child(
            ["parts", "plain", "1000", "10"], b"", seconds=30
        )
    finally:
        sandbox.child_command = real
    return answer["parts"]


def _check(ok: bool, what: str) -> None:
    """Stop at the first thing that is wrong.

    :param ok: whether it holds
    :param what: what should hold
    """
    print(f"{'ok  ' if ok else 'FAIL'} {what}")
    if not ok:
        sys.exit(1)


async def main() -> None:
    """Run every check, in order."""
    _check(os.geteuid() == 0, "the worker runs as root")
    _check(sandbox.child_user() == "reader", "a root worker reads as reader")

    os.environ[CANARY] = "canary"
    user, readable, names = await _probe()
    _check(user == "reader", f"the child runs as {user}")
    _check(readable == "no", "the child cannot read the worker's environment")
    _check(CANARY not in names, f"the child's variables are {names}")

    parts, pages = await local.read_parts(
        "# Notes\n\nCafé at nine.".encode(), FileKind.MARKDOWN
    )
    _check(
        (parts, pages) == (["# Notes\n\nCafé at nine."], 1),
        "the child reads a Markdown file",
    )

    page = await local.open_page(_scan(), FileKind.IMAGE, 1, dpi=200)
    _check(isinstance(page, ScanPage), "the child turns an image into a page")
    assert isinstance(page, ScanPage)
    reading = await local.TesseractReader().read(page)
    found = [line.split()[0] for line in LINES if line.split()[0] in reading.text]
    _check(len(found) == len(LINES), f"Tesseract read {', '.join(found)}")
    confidence = reading.confidence or 0
    _check(confidence > 0.6, f"with a mean confidence of {confidence:.2f}")
    print(f"     model: {reading.model}")


if __name__ == "__main__":
    asyncio.run(main())
