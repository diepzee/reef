"""The migration that tags every existing log with ``log``.

Its SQL is fixed text naming the schema of its day. So, like the avatar
helpers' migration, it is tested on a database the migration chain builds up
to and including it, not on the suite's schema.
"""

import importlib
from collections.abc import AsyncIterator

import asyncpg
import pytest_asyncio
from test_migration_chain import _admin_dsn, build_by_migrating, drop_database

MIGRATION = importlib.import_module(
    "reef.piccolo_migrations.reef_2026_11_20t14_00_00_000000"
)

#: Built and dropped per run, like the drill's database.
DATABASE = "reef_log_tag_drill"

OWNER = "00000000-0000-0000-0000-0000000000a1"
SPACE = "00000000-0000-0000-0000-0000000000c1"

#: Each seeded engram's path, its tags before, and its tags after.
ENGRAMS = {
    "climbing-log.md": ([], ["log"]),
    "notes/run-log.md": (["core"], ["core", "log"]),
    "tagged-log.md": (["log"], ["log"]),
    "climbing-log-2.md": ([], []),
    "blog.md": ([], []),
    "catalog.md": (["x"], ["x"]),
}


@pytest_asyncio.fixture(scope="module")
async def as_of_the_tagging() -> AsyncIterator[str]:
    """Build the schema as it stood right after this migration ran, with engrams.

    One person owns one space holding engrams, some named like logs. Seeded
    as the superuser, which row security does not bind.

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
            "INSERT INTO spaces (id, slug, kind, owner_person_id, version) "
            "VALUES ($1, 'personal-a1', 'personal', $2, 7)",
            SPACE,
            OWNER,
        )
        await admin.execute(
            "INSERT INTO memberships (person_id, space_id, role, alias) "
            "VALUES ($1, $2, 'member', 'personal')",
            OWNER,
            SPACE,
        )
        for path, (tags, _) in ENGRAMS.items():
            await admin.execute(
                "INSERT INTO engrams (id, space_id, path, title, tags, body, "
                "version, created_at, updated_at) VALUES (gen_random_uuid(), $1, "
                "$2, $2, $3, 'Body.', 3, '2026-01-01', '2026-01-02')",
                SPACE,
                path,
                tags,
            )
    finally:
        await admin.close()
    yield dsn
    await drop_database(DATABASE)


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


async def _state(connection: asyncpg.Connection) -> dict:
    """Read what the migration may and may not change.

    :param connection: a superuser connection to the database under test
    :returns: each of the space's engrams' tags, version and update time;
        the number of revisions anywhere; the space's version; and whether
        row security is forced
    """
    rows = await connection.fetch(
        "SELECT path, tags, version, updated_at FROM engrams WHERE space_id = $1",
        SPACE,
    )
    return {
        "engrams": {
            row["path"]: (list(row["tags"]), row["version"], row["updated_at"])
            for row in rows
        },
        "revisions": await connection.fetchval("SELECT count(*) FROM revisions"),
        "space_version": await connection.fetchval(
            "SELECT version FROM spaces WHERE id = $1", SPACE
        ),
        "forced": await connection.fetchval(
            "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'engrams'"
        ),
    }


async def test_it_tags_the_logs_as_the_owning_role(as_of_the_tagging):
    """Under forced row security, as the owner, every ``*-log.md`` gets the tag.

    Nothing else changes: no other engram, no version, no history, and row
    security is forced again afterwards. Running it again changes nothing.
    """
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    try:
        before = await _state(admin)
        assert before["forced"] is True

        await _migrate(as_of_the_tagging)
        after = await _state(admin)

        assert {path: tags for path, (tags, _, _) in after["engrams"].items()} == {
            path: tags for path, (_, tags) in ENGRAMS.items()
        }
        assert {path: rest for path, (_, *rest) in after["engrams"].items()} == {
            path: rest for path, (_, *rest) in before["engrams"].items()
        }
        assert after["revisions"] == before["revisions"] == 0
        assert after["space_version"] == before["space_version"] == 7
        assert after["forced"] is True

        await _migrate(as_of_the_tagging)
        assert await _state(admin) == after
    finally:
        await admin.close()
