"""Rehearse the token table upgrade and its private purge."""

import importlib
from collections.abc import AsyncIterator
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from test_migration_chain import _admin_dsn, build_by_migrating, drop_database

DATABASE = "reef_write_tokens_drill"


async def apply_migration(connection: asyncpg.Connection) -> None:
    """Apply the frozen migration inside one transaction.

    :param connection: the database being upgraded
    """
    migration = importlib.import_module(
        "reef.piccolo_migrations.reef_2026_11_27t08_00_00_000000"
    )
    async with connection.transaction():
        for statement in migration.STATEMENTS:
            await connection.execute(statement)


@pytest_asyncio.fixture(scope="module")
async def migrated() -> AsyncIterator[str]:
    """Upgrade the preceding database twice without changing existing people.

    :returns: the upgraded database DSN
    """
    dsn = await build_by_migrating(DATABASE, upto="2026-11-26T15:00:00:000000")
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    app = await asyncpg.connect(dsn)
    try:
        await admin.execute(
            "INSERT INTO persons (id, email, display_name) VALUES ($1, 'token@example.test', 'Token')",
            uuid4(),
        )
        people = await admin.fetch("SELECT * FROM persons")
        for _ in range(2):
            await apply_migration(app)
            assert await admin.fetch("SELECT * FROM persons") == people
        yield dsn
    finally:
        await app.close()
        await admin.close()
        await drop_database(DATABASE)


@pytest_asyncio.fixture
async def connection(migrated: str) -> AsyncIterator[asyncpg.Connection]:
    """Keep each migration boundary check inside a rolled-back transaction.

    :param migrated: the upgraded database
    :returns: an admin connection for seeding and role changes
    """
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    transaction = admin.transaction()
    await transaction.start()
    try:
        yield admin
    finally:
        await transaction.rollback()
        await admin.close()


async def test_migrated_table_is_private_and_purge_returns_void(connection) -> None:
    """RLS, grants, cascade and the purge hold on the upgraded database."""
    person = await connection.fetchval("SELECT id FROM persons LIMIT 1")
    await connection.execute(
        "INSERT INTO used_write_tokens (id, person_id, token_digest, used_at) "
        "VALUES (gen_random_uuid(), $1, 'old', now() - interval '25 hours'), "
        "(gen_random_uuid(), $1, 'young', now() - interval '23 hours')",
        person,
    )
    before = await connection.fetch("SELECT * FROM used_write_tokens ORDER BY id")
    await connection.execute("SET LOCAL ROLE reef")
    await apply_migration(connection)
    await connection.execute("RESET ROLE")
    assert (
        await connection.fetch("SELECT * FROM used_write_tokens ORDER BY id") == before
    )
    helper = await connection.fetchrow(
        "SELECT prosecdef, proconfig, pg_get_function_result(oid) AS result, "
        "has_function_privilege('public', oid, 'EXECUTE') AS public "
        "FROM pg_proc WHERE proname = 'reef_purge_write_tokens'"
    )
    assert helper["prosecdef"] and helper["result"] == "void" and not helper["public"]
    assert helper["proconfig"] == ["search_path=pg_catalog, public, pg_temp"]
    await connection.execute("SET LOCAL ROLE reef_probe")
    await connection.execute("SET LOCAL TIME ZONE 'Pacific/Kiritimati'")
    assert await connection.fetch("SELECT * FROM used_write_tokens") == []
    assert await connection.fetchval("SELECT reef_purge_write_tokens()") is None
    await connection.execute(
        "SELECT set_config('app.person_id', $1, true)", str(person)
    )
    assert [
        row["token_digest"]
        for row in await connection.fetch("SELECT * FROM used_write_tokens")
    ] == ["young"]
    for sql in (
        "UPDATE used_write_tokens SET token_digest = 'changed'",
        "DELETE FROM used_write_tokens",
    ):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await connection.execute(sql)
    await connection.execute("RESET ROLE")
    await connection.execute("DELETE FROM persons WHERE id = $1", person)
    assert await connection.fetchval("SELECT count(*) FROM used_write_tokens") == 0


async def test_migration_enforces_unique_tokens_and_row_security(connection) -> None:
    """The migrated table has a valid unique pair index and enabled, forced RLS."""
    flags = await connection.fetchrow(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid = 'used_write_tokens'::regclass"
    )
    assert flags["relrowsecurity"] and flags["relforcerowsecurity"]
    index = await connection.fetchrow(
        "SELECT i.indisunique, i.indisvalid, i.indpred IS NULL AS unfiltered, "
        "ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY AS k(num, pos) "
        "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.num "
        "ORDER BY k.pos) AS columns FROM pg_index i "
        "WHERE i.indexrelid = 'used_write_tokens_person_digest'::regclass "
        "AND i.indrelid = 'used_write_tokens'::regclass"
    )
    assert index is not None
    assert index["indisunique"] and index["indisvalid"] and index["unfiltered"]
    assert index["columns"] == ["person_id", "token_digest"]


async def test_former_app_role_receives_private_table_and_purge_rights(
    connection,
) -> None:
    """Existing rif_app callers get the same access as reef_app callers."""
    if not await connection.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app')"
    ):
        await connection.execute("CREATE ROLE rif_app NOLOGIN NOBYPASSRLS")
    await connection.execute("GRANT USAGE ON SCHEMA public TO rif_app")
    await apply_migration(connection)
    await connection.execute("SET LOCAL ROLE rif_app")
    assert await connection.fetch("SELECT * FROM used_write_tokens") == []
    assert await connection.fetchval("SELECT reef_purge_write_tokens()") is None
    assert await connection.fetchval(
        "SELECT has_table_privilege(current_user, 'used_write_tokens', 'INSERT')"
    )
    assert not await connection.fetchval(
        "SELECT has_table_privilege(current_user, 'used_write_tokens', 'DELETE')"
    )


async def test_migration_uses_former_authz_when_new_roles_are_absent(
    connection,
) -> None:
    """Exercise old-name lookup without renaming shared cluster roles.

    The dedicated drill database's view hides new names only on this
    connection; role creation and grants roll back with the test.
    """
    for role in ("rif_authz", "rif_app"):
        if not await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", role
        ):
            await connection.execute(f"CREATE ROLE {role} NOLOGIN")
    await connection.execute("ALTER ROLE rif_authz BYPASSRLS")
    await connection.execute("GRANT rif_authz TO reef")
    await connection.execute("GRANT USAGE, CREATE ON SCHEMA public TO rif_authz")
    await connection.execute("GRANT USAGE ON SCHEMA public TO rif_app")
    await connection.execute(
        "CREATE VIEW public.pg_roles AS SELECT * FROM pg_catalog.pg_roles "
        "WHERE rolname NOT IN ('reef_authz', 'reef_app', 'reef_probe')"
    )
    await connection.execute("GRANT SELECT ON public.pg_roles TO reef")
    await connection.execute("SET LOCAL search_path = public, pg_catalog, pg_temp")
    await connection.execute("SET LOCAL ROLE reef")
    for _ in range(2):
        await apply_migration(connection)
    assert (
        await connection.fetchval(
            "SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE proname = 'reef_purge_write_tokens'"
        )
        == "rif_authz"
    )
    await connection.execute("RESET ROLE")
    await connection.execute("SET LOCAL ROLE rif_app")
    assert await connection.fetchval("SELECT reef_purge_write_tokens()") is None
