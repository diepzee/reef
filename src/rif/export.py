"""Manual markdown export — the exit hatch.

One-way, run by hand (``python -m rif.export <alias> <target-dir>``). Nothing
reads from the output; automation and changelog-style commits are deferred
until the write path has earned trust.
"""

import asyncio
import sys
from pathlib import Path

import yaml

from rif.access import Principal
from rif.models import Page, Person
from rif.pages import list_pages


def render_page(page: Page) -> str:
    """Render a page as markdown with YAML frontmatter, import-compatible.

    :param page: the page to render
    :returns: markdown text
    """
    meta = {
        "title": page.title,
        "tags": list(page.tags),
        "updated": page.updated_at.date().isoformat(),
    }
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{front}\n---\n\n{page.body}\n"


async def export_space(principal: Principal, alias: str, target: Path) -> int:
    """Write every page in a space to a directory as markdown.

    :param principal: the authenticated person
    :param alias: ``personal`` or a shared-space slug
    :param target: directory to write into
    :returns: number of files written
    """
    target.mkdir(parents=True, exist_ok=True)
    pages = await list_pages(principal, alias)
    for page in pages:
        destination = target / page.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(render_page(page))
    return len(pages)


async def _main(alias: str, target: str) -> None:
    """CLI entrypoint: export one space as the dev principal.

    :param alias: ``personal`` or a shared-space slug
    :param target: output directory
    """
    import os

    from rif.db import DB, transaction_scope

    await DB.start_connection_pool()
    async with transaction_scope():
        person = (
            await Person.objects()
            .where(Person.email == os.environ["RIF_DEV_PRINCIPAL_EMAIL"])
            .first()
        )
        principal = Principal(person_id=person.id, email=person.email)
        count = await export_space(principal, alias, Path(target))
    print(f"exported {count} page(s) to {target}")


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1], sys.argv[2]))
