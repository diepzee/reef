"""One-shot import of the mark corpus, per the disposition table.

Run locally over stdio config with RIF_DEV_PRINCIPAL_EMAIL set to Wouter.
Review HOUSEHOLD and PERSONAL lists against mark/meta/architecture.md before
running; nothing is imported by inference.
"""

import asyncio
import sys
from pathlib import Path

import yaml
from sqlalchemy import select

from rif.access import Principal
from rif.db import session_scope
from rif.models import Person
from rif.pages import save_page

HOUSEHOLD = {"house.md", "money.md", "family-film.md"}
PERSONAL = {"health.md", "character.md", "wouter.md", "mark.md", "ringtime.md",
            "haai.md", "work.md", "ghent-ai-market.md", "music-taste.md",
            "film-taste.md", "finances.md", "archives.md"}


def parse_markdown(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from a markdown body.

    :param text: file contents
    :returns: frontmatter mapping and body
    """
    if not text.startswith("---\n"):
        return {}, text
    _, _, remainder = text.partition("---\n")
    raw_meta, separator, body = remainder.partition("\n---\n")
    if not separator:
        return {}, text
    return yaml.safe_load(raw_meta) or {}, body


async def main(source: Path, email: str) -> None:
    """Import the listed files into their assigned spaces.

    :param source: the mark wiki directory
    :param email: Wouter's email, to resolve the principal
    """
    async with session_scope() as session:
        person = await session.scalar(select(Person).where(Person.email == email))
        principal = Principal(person_id=person.id, email=person.email)
        for name, alias in [(n, "household") for n in sorted(HOUSEHOLD)] + [
                (n, "personal") for n in sorted(PERSONAL)]:
            path = source / name
            if not path.exists():
                print(f"SKIP {name} (not present)")
                continue
            meta, body = parse_markdown(path.read_text())
            await save_page(session, principal, alias, name, body.strip(),
                            message=f"imported from mark/{name}",
                            title=meta.get("title", path.stem),
                            tags=list(meta.get("tags", [])))
            print(f"{alias:9} <- {name}")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
