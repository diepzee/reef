"""Seed a scratch page for manual context-limit measurement.

One-shot, run by hand against the personal space (via
``REEF_DEV_PRINCIPAL_EMAIL`` over stdio config). Grows or shrinks a single
scratch page so the personal space's total body size lands at the requested
target, then reports the resulting total and page count.

The measurement itself is a human step this script does not perform: after
each run, from the actual Claude mobile app, ask the assistant to
``load_all_context`` and record ``page_count``, ``included_count``, and
whether every listed non-null body arrived intact in
``docs/superpowers/plans/context-limits.md``. Once all three runs (roughly
60 KB, 200 KB, 500 KB) are recorded, delete the scratch page by running this
script with a target of ``0``.
"""

import asyncio
import sys

from reef.access import Principal
from reef.db import transaction_scope
from reef.models import Person
from reef.pages import list_pages, save_page

SCRATCH_PATH = "scratch-measurement.md"


async def main(target_kib: int, email: str) -> None:
    """Grow the scratch page so the personal space totals ``target_kib`` KiB.

    Sizes every other page already in the space and fills the remainder
    with the scratch page, so repeated runs with increasing targets build
    up the corpus without disturbing real content.

    :param target_kib: desired total corpus size for the personal space, in
        kibibytes (1024 bytes); ``0`` empties the scratch page
    :param email: the principal's email, to resolve them for save_page
    """
    async with transaction_scope():
        person = await Person.objects().where(Person.email == email).first()
        principal = Principal(person_id=person.id, email=person.email)
        pages = await list_pages(principal, "personal")
        existing = sum(len(page.body) for page in pages if page.path != SCRATCH_PATH)
        filler_bytes = max(0, target_kib * 1024 - existing)
        await save_page(
            principal,
            "personal",
            SCRATCH_PATH,
            "x" * filler_bytes,
            message=f"context-limit measurement scratch page (target {target_kib} KiB)",
            title="Context limit measurement scratch",
        )
        pages = await list_pages(principal, "personal")
        total = sum(len(page.body) for page in pages)
        print(
            f"personal space now totals {total} bytes (~{total / 1024:.1f} KiB) "
            f"across {len(pages)} page(s); scratch page {SCRATCH_PATH} is "
            f"{filler_bytes} bytes"
        )


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), sys.argv[2]))
