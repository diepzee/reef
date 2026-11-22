"""Generate the made-up person's three files, once; the output is committed.

Run from the repository root: ``python scripts/conversations/make_files.py``.
The PDF has a text layer, the scan is a picture of printed text with no text
layer, and the photo is a simple generated image.
"""

import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tests.filefixtures import text_pdf

FILES = REPO / "src" / "reef" / "testaccount" / "person" / "files"

QUOTE_PAGES = [
    (
        "Offerte Fleetboard\nKuipke Logistics NV, Noorderlaan 118, Antwerpen\n"
        "Klant: Transport Van den Abeele\nOpgemaakt door: Anouk Verbeke\n"
        "Datum: 20 oktober 2026\nGeldig tot: 20 december 2026"
    ),
    (
        "1. Wat u krijgt\nEen dashboard met de positie en vertraging\nvan 85 vrachtwagens.\n"
        "Dagelijks verwerken we ongeveer\n2,4 miljoen GPS-punten.\nRitten sluiten na 20 minuten stilstand."
    ),
    (
        "2. Prijs\nInrichting: 18.500 euro\nMaandelijks abonnement: 1.250 euro\n"
        "Opleiding voor 6 medewerkers: 1.800 euro\nAlle bedragen zijn exclusief btw."
    ),
    (
        "3. Planning\nStart: 1 december 2026\nEerste oplevering: 18 januari 2027\n"
        "Contactpersoon bij Van den Abeele:\nSabine Leemans"
    ),
]

SCAN_LINES = [
    "Tandartspraktijk Bogaert",
    "Stationsstraat 41, Mechelen",
    "",
    "Beste Lode Vanhecke,",
    "Uw afspraak is verplaatst naar",
    "donderdag 3 december 2026",
    "om 09:15 uur.",
    "",
    "Graag 10 minuten vooraf aanwezig.",
    "Dr. Kristof Bogaert",
]


def make_scan() -> bytes:
    """Draw a printed letter on a slightly grey background.

    :returns: the PNG bytes
    """
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("L", (1240, 1000), 238)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=60)
    for number, line in enumerate(SCAN_LINES):
        draw.text((80, 70 + 85 * number), line, fill=15, font=font)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def make_photo() -> bytes:
    """Draw a simple picture of tomatoes in a crate.

    :returns: the JPEG bytes
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (800, 600), (196, 214, 170))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (120, 320, 680, 540), fill=(150, 104, 62), outline=(90, 60, 30), width=6
    )
    for x, y in [
        (200, 300),
        (300, 280),
        (400, 300),
        (500, 280),
        (590, 305),
        (350, 340),
        (460, 345),
    ]:
        draw.ellipse((x - 45, y - 45, x + 45, y + 45), fill=(200, 40, 36))
        draw.ellipse((x - 12, y - 48, x + 12, y - 30), fill=(40, 110, 50))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


def main() -> None:
    """Write the three files into the fixture's ``files`` folder."""
    FILES.mkdir(parents=True, exist_ok=True)
    (FILES / "fleetboard-offerte.pdf").write_bytes(text_pdf(QUOTE_PAGES))
    (FILES / "tandarts-brief.png").write_bytes(make_scan())
    (FILES / "moestuin-oogst.jpg").write_bytes(make_photo())


if __name__ == "__main__":
    main()
