"""Corrections are accepted by both the migrated and freshly built usage log."""

import importlib
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio
from test_migration_chain import _admin_dsn, build_by_migrating, drop_database

from reef.rls import usage_statements

DATABASE = "reef_correction_usage_drill"
CONSTRAINT = "engram_access_via_known"
PERSON = "00000000-0000-0000-0000-0000000000a1"
SPACE = "00000000-0000-0000-0000-0000000000c1"
ENGRAM = "00000000-0000-0000-0000-0000000000e1"
INSERT = (
    "INSERT INTO engram_access (id, person_id, engram_id, via, weight) "
    "VALUES (gen_random_uuid(), $1, $2, $3, $4)"
)


@pytest_asyncio.fixture(scope="module")
async def migrated() -> AsyncIterator[str]:
    """Build the real migration chain, then seed an engram and a routine use.

    :returns: the migrated database DSN as the owning role
    """
    dsn = await build_by_migrating(DATABASE)
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    try:
        await admin.execute(
            "INSERT INTO persons (id, email, display_name) "
            "VALUES ($1, 'correction@example.test', 'Correction')",
            PERSON,
        )
        await admin.execute(
            "INSERT INTO spaces (id, slug, kind, owner_person_id) "
            "VALUES ($1, 'personal-correction', 'personal', $2)",
            SPACE,
            PERSON,
        )
        await admin.execute(
            "INSERT INTO engrams (id, space_id, path, title, body) "
            "VALUES ($1, $2, 'house.md', 'House', 'blue')",
            ENGRAM,
            SPACE,
        )
        await admin.execute(INSERT, PERSON, ENGRAM, "write", 1.0)
    finally:
        await admin.close()
    try:
        yield dsn
    finally:
        await drop_database(DATABASE)


async def _state(connection: asyncpg.Connection) -> dict:
    """Read the rows and security settings that must remain unchanged.

    :param connection: a connection allowed to read all usage rows
    :returns: usage rows, table rights, row security and policies
    """
    return {
        "rows": await connection.fetch("SELECT * FROM engram_access ORDER BY id"),
        "rights": await connection.fetchrow(
            "SELECT relowner, relacl, relrowsecurity, relforcerowsecurity "
            "FROM pg_class WHERE oid = 'engram_access'::regclass"
        ),
        "policies": await connection.fetch(
            "SELECT * FROM pg_policies WHERE tablename = 'engram_access' "
            "ORDER BY policyname"
        ),
    }


async def test_the_chain_accepts_corrections_but_rejects_unknown_kinds(
    migrated: str,
) -> None:
    """The actual database accepts every known kind and still checks weights.

    :param migrated: the database built by the migration chain
    """
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    try:
        async with admin.transaction():
            for kind in ("read", "write", "remember", "web", "correct"):
                await admin.execute(INSERT, PERSON, ENGRAM, kind, 3.0)
            for kind, weight in (("unknown", 1.0), ("correct", 0.0), ("correct", 11.0)):
                with pytest.raises(asyncpg.CheckViolationError):
                    async with admin.transaction():
                        await admin.execute(INSERT, PERSON, ENGRAM, kind, weight)
    finally:
        await admin.close()


async def test_migrating_existing_usage_twice_preserves_rows_and_rights(
    migrated: str,
) -> None:
    """Upgrade the old constraint twice without rewriting usage or permissions.

    :param migrated: the database built by the migration chain
    """
    migration = importlib.import_module(
        "reef.piccolo_migrations.reef_2026_11_26t12_00_00_000000"
    )
    app = await asyncpg.connect(migrated)
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    try:
        await admin.execute("DELETE FROM engram_access WHERE via <> 'write'")
        await app.execute(f"ALTER TABLE engram_access DROP CONSTRAINT {CONSTRAINT}")
        await app.execute(
            f"ALTER TABLE engram_access ADD CONSTRAINT {CONSTRAINT} "
            "CHECK (via IN ('read', 'write', 'remember', 'web'))"
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await admin.execute(INSERT, PERSON, ENGRAM, "correct", 3.0)
        before = await _state(admin)
        for _ in range(2):
            async with app.transaction():
                for statement in migration.STATEMENTS:
                    await app.execute(statement)
            assert await _state(admin) == before
            async with admin.transaction():
                await admin.execute(INSERT, PERSON, ENGRAM, "correct", 3.0)
                await admin.execute("DELETE FROM engram_access WHERE via = 'correct'")
        assert migration.STATEMENTS == [
            statement for statement in usage_statements() if CONSTRAINT in statement
        ]
    finally:
        await app.close()
        await admin.close()
