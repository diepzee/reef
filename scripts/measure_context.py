"""Seed a scratch engram for manual context-limit measurement.

One-shot, run by hand against the personal space (via
``REEF_DEV_PRINCIPAL_EMAIL`` over stdio config). Grows or shrinks a single
scratch engram so the personal space's total body size lands at the requested
target, then reports the resulting total and engram count.

The measurement itself is a human step this script does not perform: after
each run, from the actual Claude mobile app, ask the assistant to
``load_all_context`` and record ``engram_count``, ``included_count``, and
whether every listed non-null body arrived intact in
``docs/superpowers/plans/context-limits.md``. Once all three runs (roughly
60 KB, 200 KB, 500 KB) are recorded, delete the scratch engram by running this
script with a target of ``0``.
"""

import asyncio
import sys

from reef.access import Principal
from reef.db import transaction_scope
from reef.engrams import list_engrams, save_engram
from reef.models import Person

SCRATCH_PATH = "scratch-measurement.md"


async def main(target_kib: int, email: str) -> None:
    """Grow the scratch engram so the personal space totals ``target_kib`` KiB.

    Sizes every other engram already in the space and fills the remainder
    with the scratch engram, so repeated runs with increasing targets build
    up the corpus without disturbing real content.

    :param target_kib: desired total corpus size for the personal space, in
        kibibytes (1024 bytes); ``0`` empties the scratch engram
    :param email: the principal's email, to resolve them for save_engram
    """
    async with transaction_scope():
        person = await Person.objects().where(Person.email == email).first()
        principal = Principal(person_id=person.id, email=person.email)
        engrams = await list_engrams(principal, "personal")
        existing = sum(
            len(engram.body) for engram in engrams if engram.path != SCRATCH_PATH
        )
        filler_bytes = max(0, target_kib * 1024 - existing)
        await save_engram(
            principal,
            "personal",
            SCRATCH_PATH,
            "x" * filler_bytes,
            message=f"context-limit measurement scratch engram (target {target_kib} KiB)",
            title="Context limit measurement scratch",
        )
        engrams = await list_engrams(principal, "personal")
        total = sum(len(engram.body) for engram in engrams)
        print(
            f"personal space now totals {total} bytes (~{total / 1024:.1f} KiB) "
            f"across {len(engrams)} engram(s); scratch engram {SCRATCH_PATH} is "
            f"{filler_bytes} bytes"
        )


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), sys.argv[2]))
