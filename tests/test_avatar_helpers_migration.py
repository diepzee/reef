"""The migration that creates the avatar helpers production never got.

Its SQL is fixed text naming the schema of its day, before cove became space
and page became engram. So it is tested on a database the migration chain
builds up to and including it, not on the suite's schema, which is today's.
"""

import importlib

import asyncpg
import pytest_asyncio
from test_migration_chain import _admin_dsn, build_by_migrating, drop_database

MIGRATION = importlib.import_module(
    "reef.piccolo_migrations.reef_2026_11_19t16_00_00_000000"
)

HELPERS = ("reef_member_faces", "reef_member_avatar")

#: Built and dropped per run, like the drill's database.
DATABASE = "reef_avatar_helpers_drill"

OWNER = "00000000-0000-0000-0000-0000000000a1"
PARTNER = "00000000-0000-0000-0000-0000000000a2"
SHARED = "00000000-0000-0000-0000-0000000000c1"


@pytest_asyncio.fixture(scope="module")
async def as_of_the_fix() -> str:
    """Build the schema as it stood right after this migration ran.

    Two people share one cove, and one of them has a picture. Seeded as the
    superuser, which row security does not bind.

    :returns: the DSN of the database, as the owning role
    """
    dsn = await build_by_migrating(DATABASE, upto=MIGRATION.ID)
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    try:
        await admin.execute(
            "INSERT INTO persons (id, email, display_name, avatar_mime, avatar_bytes) "
            "VALUES ($1, 'owner@example.test', 'Owner', 'image/png', '\\x89504e47'), "
            "($2, 'partner@example.test', 'Partner', NULL, NULL)",
            OWNER,
            PARTNER,
        )
        await admin.execute(
            "INSERT INTO coves (id, slug, kind, owner_person_id, version) "
            "VALUES ($1, 'household', 'shared', $2, 0)",
            SHARED,
            OWNER,
        )
        await admin.execute(
            "INSERT INTO memberships (person_id, cove_id, role, alias) "
            "VALUES ($1, $3, 'member', 'household'), ($2, $3, 'member', 'household')",
            OWNER,
            PARTNER,
            SHARED,
        )
    finally:
        await admin.close()
    yield dsn
    await drop_database(DATABASE)


async def _present(connection: asyncpg.Connection) -> set[str]:
    """Return which of the two helpers exist.

    :param connection: a connection to the database under test
    :returns: the names found
    """
    rows = await connection.fetch(
        "SELECT proname FROM pg_proc WHERE proname = ANY($1::text[])", list(HELPERS)
    )
    return {row["proname"] for row in rows}


async def _migrate(dsn: str) -> None:
    """Run the migration's statements in one transaction, as it does.

    :param dsn: the database to run them on
    """
    connection = await asyncpg.connect(dsn)
    try:
        async with connection.transaction():
            for statement in MIGRATION.STATEMENTS:
                await connection.execute(statement)
    finally:
        await connection.close()


async def test_it_restores_helpers_missing_as_in_production(as_of_the_fix):
    """Production lacks both helpers; after the migration a roster works."""
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    try:
        await admin.execute("DROP FUNCTION IF EXISTS reef_member_faces(uuid)")
        await admin.execute("DROP FUNCTION IF EXISTS reef_member_avatar(uuid, uuid)")
        assert await _present(admin) == set()

        await _migrate(as_of_the_fix)

        assert await _present(admin) == set(HELPERS)
    finally:
        await admin.close()

    app = await asyncpg.connect(as_of_the_fix)
    try:
        async with app.transaction():
            await app.execute("SELECT set_config('app.person_id', $1, true)", PARTNER)
            faces = await app.fetch("SELECT * FROM reef_member_faces($1)", SHARED)
            avatar = await app.fetch(
                "SELECT * FROM reef_member_avatar($1, $2)", SHARED, OWNER
            )
    finally:
        await app.close()
    assert [(str(face["person_id"]), face["avatar_len"]) for face in faces] == [
        (OWNER, 4)
    ]
    assert [row["avatar_mime"] for row in avatar] == ["image/png"]


async def test_it_is_harmless_where_the_helpers_exist(as_of_the_fix):
    await _migrate(as_of_the_fix)
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    try:
        assert await _present(admin) == set(HELPERS)
    finally:
        await admin.close()
