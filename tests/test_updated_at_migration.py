"""The migration that backfills ``pages.updated_at`` from the revisions.

Its SQL is fixed text naming the schema of its day, before cove became space
and page became engram, and its ID sorts before the rename, so that is the
schema it runs on everywhere. So it is tested on a database the migration
chain builds up to and including it, not on the suite's schema, which is
today's. Its statements run as the owning role, which the forced row security
binds, as they do in the chain.
"""

import importlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest_asyncio
from test_migration_chain import _admin_dsn, build_by_migrating, drop_database

MIGRATION = importlib.import_module(
    "reef.piccolo_migrations.reef_2026_11_20t09_00_00_000000"
)

#: Built and dropped per run, like the drill's database.
DATABASE = "reef_updated_at_drill"

OWNER = "00000000-0000-0000-0000-0000000000a1"
COVE = "00000000-0000-0000-0000-0000000000c1"

CREATED = datetime(2026, 8, 1, 9, 0, 0, tzinfo=UTC).replace(tzinfo=None)
EDITED = datetime(2026, 10, 5, 14, 30, 0, tzinfo=UTC).replace(tzinfo=None)
EDITED_AGAIN = datetime(2026, 11, 2, 8, 15, 0, tzinfo=UTC).replace(tzinfo=None)


@pytest_asyncio.fixture(scope="module")
async def as_of_the_fix() -> AsyncIterator[str]:
    """Build the schema as it stood right after this migration ran.

    One person owns one cove. Seeded as the superuser, which row security
    does not bind.

    :returns: the DSN of the database, as the owning role
    """
    dsn = await build_by_migrating(DATABASE, upto=MIGRATION.ID)
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    try:
        await admin.execute(
            "INSERT INTO persons (id, email, display_name) "
            "VALUES ($1, 'owner@example.test', 'Owner')",
            OWNER,
        )
        await admin.execute(
            "INSERT INTO coves (id, slug, kind, owner_person_id, version) "
            "VALUES ($1, 'personal', 'personal', $2, 0)",
            COVE,
            OWNER,
        )
    finally:
        await admin.close()
    yield dsn
    await drop_database(DATABASE)


@pytest_asyncio.fixture
async def admin(as_of_the_fix) -> AsyncIterator[asyncpg.Connection]:
    """Yield a superuser connection to the database, with no pages in it.

    The database is shared by the module, so each test starts by emptying
    the two tables the migration reads and writes.

    :param as_of_the_fix: the database under test
    :returns: an open connection, closed on teardown
    """
    connection = await asyncpg.connect(_admin_dsn(DATABASE))
    try:
        await connection.execute("TRUNCATE pages, revisions CASCADE")
        yield connection
    finally:
        await connection.close()


async def _page(admin, path: str, updated_at: datetime | None) -> UUID:
    """Insert a page row.

    :param admin: connection that bypasses row security
    :param path: the page path
    :param updated_at: the stored value; ``None`` stores the creation time
    :returns: the page id
    """
    page_id = uuid4()
    await admin.execute(
        "INSERT INTO pages (id, cove_id, path, title, tags, body, version, "
        "created_at, updated_at) VALUES ($1, $2, $3, $3, '{}', 'x', 1, $4, $5)",
        page_id,
        COVE,
        path,
        CREATED,
        updated_at or CREATED,
    )
    return page_id


async def _revision(admin, page_id: UUID, created_at: datetime) -> None:
    """Insert a revision row.

    :param admin: connection that bypasses row security
    :param page_id: the page it belongs to
    :param created_at: when it was written
    """
    await admin.execute(
        "INSERT INTO revisions (id, page_id, path, title, tags, body, message, "
        "created_at) VALUES ($1, $2, 'p', 'p', '{}', 'x', 'm', $3)",
        uuid4(),
        page_id,
        created_at,
    )


async def _updated(admin) -> dict[str, datetime]:
    """Read every page's ``updated_at`` by path.

    :param admin: connection that bypasses row security
    :returns: mapping of path to stored value
    """
    rows = await admin.fetch("SELECT path, updated_at FROM pages")
    return {row["path"]: row["updated_at"] for row in rows}


async def _migrate(dsn: str) -> None:
    """Run the migration's statements in one transaction, as it does.

    :param dsn: the database to run them on, as the owning role
    """
    connection = await asyncpg.connect(dsn)
    try:
        async with connection.transaction():
            for statement in MIGRATION.STATEMENTS:
                await connection.execute(statement)
    finally:
        await connection.close()


async def test_it_moves_updated_at_to_the_newest_revision(as_of_the_fix, admin):
    edited = await _page(admin, "edited.md", None)
    for stamp in (CREATED, EDITED_AGAIN, EDITED):
        await _revision(admin, edited, stamp)
    untouched = await _page(admin, "untouched.md", None)
    await _revision(admin, untouched, CREATED)
    newer = await _page(admin, "newer.md", EDITED_AGAIN)
    await _revision(admin, newer, EDITED)
    await _page(admin, "no-history.md", EDITED)

    await _migrate(as_of_the_fix)

    assert await _updated(admin) == {
        "edited.md": EDITED_AGAIN,
        "untouched.md": CREATED,
        "newer.md": EDITED_AGAIN,
        "no-history.md": EDITED,
    }


async def test_it_is_harmless_to_run_twice(as_of_the_fix, admin):
    page = await _page(admin, "a.md", None)
    await _revision(admin, page, EDITED)

    await _migrate(as_of_the_fix)
    await _migrate(as_of_the_fix)

    assert await _updated(admin) == {"a.md": EDITED}


async def test_it_leaves_row_security_forced(as_of_the_fix, admin):
    await _migrate(as_of_the_fix)

    rows = await admin.fetch(
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relname IN ('pages', 'revisions')"
    )
    assert {
        (r["relname"], r["relrowsecurity"], r["relforcerowsecurity"]) for r in rows
    } == {
        ("pages", True, True),
        ("revisions", True, True),
    }
