"""Rehearse the log fixes over stored rows and unchanged function rights."""

import importlib
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from test_migration_chain import _admin_dsn, build_by_migrating, drop_database

MIGRATION = importlib.import_module(
    "reef.piccolo_migrations.reef_2026_11_26t09_23_00_000000"
)
DATABASE = "reef_log_purges_drill"
PERSON = UUID("00000000-0000-0000-0000-0000000000a1")
FUNCTIONS = [
    "reef_purge_search_log",
    "reef_purge_call_log",
    "reef_call_log_totals",
]


async def _rights(connection: asyncpg.Connection) -> list[asyncpg.Record]:
    """Read ownership, grants and execution settings of the three functions.

    :param connection: the database connection
    :returns: the function rights, ordered by name
    """
    return await connection.fetch(
        "SELECT proname, proowner, proacl, prosecdef, proconfig, provolatile "
        "FROM pg_proc WHERE proname = ANY($1::text[]) ORDER BY proname",
        FUNCTIONS,
    )


async def _apply(connection: asyncpg.Connection) -> None:
    """Apply the frozen statements in one transaction.

    :param connection: the migration connection
    """
    async with connection.transaction():
        for statement in MIGRATION.STATEMENTS:
            await connection.execute(statement)


@pytest_asyncio.fixture(scope="module")
async def migrated() -> AsyncIterator[str]:
    """Upgrade the preceding schema twice, preserving stored rows and rights.

    :returns: the migrated database DSN as the owning app role
    """
    dsn = await build_by_migrating(DATABASE, upto="2026-11-26T09:00:00:000000")
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    app = await asyncpg.connect(dsn)
    try:
        await admin.execute(
            "INSERT INTO persons (id, email, display_name) "
            "VALUES ($1, 'purge@example.test', 'Purge')",
            PERSON,
        )
        await admin.execute(
            "INSERT INTO search_log (id, person_id, query, searched_at) "
            "VALUES (gen_random_uuid(), $1, 'private search', "
            "date_trunc('day', now() AT TIME ZONE 'UTC') - interval '2 days')",
            PERSON,
        )
        await admin.execute(
            "INSERT INTO call_log (id, person_id, method, writer, outcome, called_at) "
            "VALUES (gen_random_uuid(), $1, 'find', 'assistant', 'ok', "
            "date_trunc('day', now() AT TIME ZONE 'UTC') - interval '2 days')",
            PERSON,
        )
        rights = await _rights(admin)
        rows = [
            await admin.fetch(f"SELECT * FROM {table} ORDER BY id")
            for table in ("search_log", "call_log")
        ]
        for _ in range(2):
            await _apply(app)
            assert await _rights(admin) == rights
            assert [
                await admin.fetch(f"SELECT * FROM {table} ORDER BY id")
                for table in ("search_log", "call_log")
            ] == rows
    finally:
        await app.close()
        await admin.close()
    yield dsn
    await drop_database(DATABASE)


@pytest_asyncio.fixture
async def connection(migrated: str) -> AsyncIterator[asyncpg.Connection]:
    """Keep each boundary check in a transaction which rolls back.

    :param migrated: the upgraded database
    :returns: a connection for seeding and switching to the app's role
    """
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    transaction = admin.transaction()
    await transaction.start()
    try:
        yield admin
    finally:
        await transaction.rollback()
        await admin.close()


@pytest.mark.parametrize("zone", ["UTC", "<+48>-48", "<-48>+48"])
@pytest.mark.parametrize(
    ("table", "column", "columns", "values"),
    [
        ("search_log", "searched_at", "query", "'private query'"),
        (
            "call_log",
            "called_at",
            "method, writer, outcome",
            "'find', 'assistant', 'ok'",
        ),
    ],
)
async def test_migrated_purges_use_utc(
    connection: asyncpg.Connection,
    zone: str,
    table: str,
    column: str,
    columns: str,
    values: str,
) -> None:
    """An app caller removes only the expired row in every session zone."""
    young, old = uuid4(), uuid4()
    for row_id, hours in ((young, 1), (old, -1)):
        await connection.execute(
            f"INSERT INTO {table} (id, person_id, {columns}, {column}) "
            f"VALUES ($1, $2, {values}, (now() AT TIME ZONE 'UTC') "
            "- interval '90 days' + make_interval(hours => $3))",
            row_id,
            PERSON,
            hours,
        )
    await connection.execute("SET LOCAL ROLE reef_probe")
    await connection.execute("SELECT set_config('TimeZone', $1, true)", zone)
    assert await connection.fetchval(f"SELECT reef_purge_{table}()") == 1
    await connection.execute("RESET ROLE")
    ids = {row["id"] for row in await connection.fetch(f"SELECT id FROM {table}")}
    assert young in ids
    assert old not in ids


async def test_migrated_call_totals_accept_89_and_refuse_90(
    connection: asyncpg.Connection,
) -> None:
    """The upgraded function leaves the partial retention day out."""
    await connection.execute("SET LOCAL ROLE reef_probe")
    assert [
        tuple(row)
        for row in await connection.fetch("SELECT * FROM reef_call_log_totals(89)")
    ] == [("find", "ok", 1, 1)]
    assert await connection.fetch("SELECT * FROM call_log") == []
    assert await connection.fetch("SELECT * FROM search_log") == []
    with pytest.raises(asyncpg.InvalidParameterValueError):
        await connection.fetch("SELECT * FROM reef_call_log_totals(90)")


async def test_migration_preserves_former_role_ownership_and_grants(
    connection: asyncpg.Connection,
) -> None:
    """The same fixed SQL works with rif_authz ownership and rif_app execution."""
    for role in ("rif_authz", "rif_app"):
        if not await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", role
        ):
            await connection.execute(f"CREATE ROLE {role} NOLOGIN")
    await connection.execute("GRANT USAGE, CREATE ON SCHEMA public TO rif_authz")
    await connection.execute("GRANT USAGE ON SCHEMA public TO rif_app")
    for signature in (
        "reef_purge_search_log()",
        "reef_purge_call_log()",
        "reef_call_log_totals(integer)",
    ):
        await connection.execute(f"ALTER FUNCTION {signature} OWNER TO rif_authz")
        await connection.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO rif_app")
    rights = await _rights(connection)
    await connection.execute("SET LOCAL ROLE rif_authz")
    for _ in range(2):
        await _apply(connection)
        assert await _rights(connection) == rights
    await connection.execute("SET LOCAL ROLE rif_app")
    for signature in (
        "reef_purge_search_log()",
        "reef_purge_call_log()",
        "reef_call_log_totals(integer)",
    ):
        assert await connection.fetchval(
            "SELECT has_function_privilege(current_user, $1, 'EXECUTE')", signature
        )
    with pytest.raises(asyncpg.InvalidParameterValueError):
        await connection.fetch("SELECT * FROM reef_call_log_totals(90)")
