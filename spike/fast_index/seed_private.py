"""Load the private export into a spike database as faithfully as the index needs.

``scripts/eval/seed.py --private`` flattens every space into one and puts the
space's alias in front of each path. That is right for search, but wrong for
the index: it drops the engrams' tags, breaks every same-space wiki link
(``[[x.md]]`` no longer finds ``personal/x.md``), leaves the files out and
turns the persona into an ordinary engram. All four change what
``load_index`` returns.

This loader keeps the original paths, tags, persona, dates and file
metadata. It only works for an export with one space, which is what the
private export holds; it refuses anything else. File bytes are not loaded:
the index shows a file's metadata only.

It prints counts only. The export is personal data.

Run as::

    REEF_DATABASE_URL=postgresql://reef:reef@localhost:5433/reef_spike_private \\
        .venv/bin/python spike/fast_index/seed_private.py
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import asyncpg

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts" / "eval"))

import evalkit
from seed import superuser_dsn

from reef.config import get_settings
from reef.db import DB, transaction_scope
from reef.engrams import PERSONA_PATH, get_engram, save_engram


def _moment(text: str | None) -> datetime | None:
    """Read an export timestamp as a naive UTC moment, as the tables store it.

    :param text: ISO 8601 text, or None
    :returns: the moment without its zone, or None
    """
    if not text:
        return None
    moment = datetime.fromisoformat(text)
    return moment.replace(tzinfo=None)


async def main() -> None:
    """Seed the private person's engrams, dates and file metadata."""
    app_dsn = get_settings().dsn
    if "reef_spike_" not in app_dsn:
        sys.exit("refused: REEF_DATABASE_URL must name a reef_spike_* database")
    seed_dsn = superuser_dsn(app_dsn)
    evalkit.refuse_unless_local(app_dsn, seed_dsn)
    export = json.loads(evalkit.PRIVATE_EXPORT.read_text())
    spaces = export.get("coves") or export.get("spaces")
    if len(spaces) != 1:
        sys.exit("this loader keeps original paths, so it needs a one-space export")
    space = spaces[0]
    engrams = space.get("pages") or space.get("engrams")
    await DB.start_connection_pool()
    try:
        principal = await evalkit.ensure_eval_person(
            seed_dsn, evalkit.PRIVATE_EMAIL, "Private eval"
        )
        for engram in engrams:
            async with transaction_scope():
                is_persona = engram["path"] == PERSONA_PATH
                current = (
                    await get_engram(principal, "personal", PERSONA_PATH)
                    if is_persona
                    else None
                )
                await save_engram(
                    principal,
                    "personal",
                    engram["path"],
                    engram["body"],
                    message="private seed",
                    title=engram["title"] or None,
                    tags=list(engram.get("tags") or []),
                    expected_version=current.version if current else None,
                    allow_protected=is_persona,
                    via=None,
                )
    finally:
        await DB.close_connection_pool()

    connection = await asyncpg.connect(seed_dsn)
    try:
        space_id = await connection.fetchval(
            "SELECT s.id FROM spaces s JOIN persons p ON p.id = s.owner_person_id "
            "WHERE p.email = $1 AND s.kind = 'personal'",
            evalkit.PRIVATE_EMAIL,
        )
        ids = {
            row["path"]: row["id"]
            for row in await connection.fetch(
                "SELECT id, path FROM engrams WHERE space_id = $1", space_id
            )
        }
        for engram in engrams:
            updated = _moment(engram.get("updated"))
            created = _moment(engram.get("created")) or updated
            if updated:
                await connection.execute(
                    "UPDATE engrams SET updated_at = $1, created_at = $2 WHERE id = $3",
                    updated,
                    created,
                    ids[engram["path"]],
                )
        files = 0
        for item in space.get("files") or []:
            if item.get("status", "ready") != "ready":
                continue
            await connection.execute(
                "INSERT INTO attachments (id, space_id, engram_id, object_key, filename, "
                "mime, byte_size, description, status, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'ready', $9)",
                uuid4(),
                space_id,
                ids.get(item.get("page_path") or item.get("engram_path") or ""),
                item["key"],
                item.get("filename") or "",
                item.get("mime") or "application/octet-stream",
                int(item.get("size") or 0),
                item.get("description") or "",
                _moment(item.get("created")) or datetime.now(),
            )
            files += 1
    finally:
        await connection.close()
    print(f"{len(engrams)} engrams and {files} file descriptions loaded")


if __name__ == "__main__":
    asyncio.run(main())
