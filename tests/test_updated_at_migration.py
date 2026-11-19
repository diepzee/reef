"""The migration that backfills ``pages.updated_at`` from the revisions."""

import importlib
from datetime import UTC, datetime
from uuid import uuid4

from reef.db import run_ddl_atomically

MIGRATION = importlib.import_module(
    "reef.piccolo_migrations.reef_2026_11_20t09_00_00_000000"
)

CREATED = datetime(2026, 8, 1, 9, 0, 0, tzinfo=UTC).replace(tzinfo=None)
EDITED = datetime(2026, 10, 5, 14, 30, 0, tzinfo=UTC).replace(tzinfo=None)
EDITED_AGAIN = datetime(2026, 11, 2, 8, 15, 0, tzinfo=UTC).replace(tzinfo=None)


async def _page(seed, cove, path: str, updated_at: datetime | None) -> object:
    """Insert a page row.

    :param seed: connection that bypasses row security
    :param cove: the cove that holds the page
    :param path: the page path
    :param updated_at: the stored value; ``None`` stores the creation time
    :returns: the page id
    """
    page_id = uuid4()
    await seed.execute(
        "INSERT INTO pages (id, cove_id, path, title, tags, body, version, "
        "created_at, updated_at) VALUES ($1, $2, $3, $3, '{}', 'x', 1, $4, $5)",
        page_id,
        cove.id,
        path,
        CREATED,
        updated_at or CREATED,
    )
    return page_id


async def _revision(seed, page_id, created_at: datetime) -> None:
    """Insert a revision row.

    :param seed: connection that bypasses row security
    :param page_id: the page it belongs to
    :param created_at: when it was written
    """
    await seed.execute(
        "INSERT INTO revisions (id, page_id, path, title, tags, body, message, "
        "created_at) VALUES ($1, $2, 'p', 'p', '{}', 'x', 'm', $3)",
        uuid4(),
        page_id,
        created_at,
    )


async def _updated(seed) -> dict[str, datetime]:
    """Read every page's ``updated_at`` by path.

    :param seed: connection that bypasses row security
    :returns: mapping of path to stored value
    """
    rows = await seed.fetch("SELECT path, updated_at FROM pages")
    return {row["path"]: row["updated_at"] for row in rows}


async def test_it_moves_updated_at_to_the_newest_revision(household, seed):
    cove = household["w_personal"]
    edited = await _page(seed, cove, "edited.md", None)
    for stamp in (CREATED, EDITED_AGAIN, EDITED):
        await _revision(seed, edited, stamp)
    untouched = await _page(seed, cove, "untouched.md", None)
    await _revision(seed, untouched, CREATED)
    newer = await _page(seed, cove, "newer.md", EDITED_AGAIN)
    await _revision(seed, newer, EDITED)
    await _page(seed, cove, "no-history.md", EDITED)

    await run_ddl_atomically(MIGRATION.STATEMENTS)

    assert await _updated(seed) == {
        "edited.md": EDITED_AGAIN,
        "untouched.md": CREATED,
        "newer.md": EDITED_AGAIN,
        "no-history.md": EDITED,
    }


async def test_it_is_harmless_to_run_twice(household, seed):
    page = await _page(seed, household["w_personal"], "a.md", None)
    await _revision(seed, page, EDITED)

    await run_ddl_atomically(MIGRATION.STATEMENTS)
    await run_ddl_atomically(MIGRATION.STATEMENTS)

    assert await _updated(seed) == {"a.md": EDITED}


async def test_it_leaves_row_security_forced(household, seed):
    await run_ddl_atomically(MIGRATION.STATEMENTS)

    rows = await seed.fetch(
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relname IN ('pages', 'revisions')"
    )
    assert {
        (r["relname"], r["relrowsecurity"], r["relforcerowsecurity"]) for r in rows
    } == {
        ("pages", True, True),
        ("revisions", True, True),
    }
