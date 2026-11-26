"""Check the jobs index through the real migration chain and on existing rows."""

import importlib
from collections.abc import AsyncIterator

import asyncpg
import pytest_asyncio
from test_migration_chain import _admin_dsn, build_by_migrating, drop_database

from reef.rls import job_statements

MIGRATION = importlib.import_module(
    "reef.piccolo_migrations.reef_2026_11_26t10_00_00_000000"
)
DATABASE = "reef_jobs_index_drill"
INDEX = "jobs_person_kind_latest"


@pytest_asyncio.fixture(scope="module")
async def migrated() -> AsyncIterator[str]:
    """Build a separate database through the index migration.

    :returns: the database DSN as the owning role
    """
    dsn = await build_by_migrating(DATABASE, upto=MIGRATION.ID)
    try:
        yield dsn
    finally:
        await drop_database(DATABASE)


async def _index(connection: asyncpg.Connection) -> asyncpg.Record:
    """Read the index definition and identity from PostgreSQL.

    :param connection: a connection to the migrated database
    :returns: the index's identity, SQL definition and validity
    """
    row = await connection.fetchrow(
        "SELECT i.indexrelid, pg_get_indexdef(i.indexrelid) AS definition, "
        "i.indisvalid FROM pg_index i WHERE i.indexrelid = $1::regclass",
        INDEX,
    )
    assert row is not None
    return row


async def _state(connection: asyncpg.Connection) -> dict:
    """Read the rows and security settings the migration must leave alone.

    :param connection: a connection that can see every job
    :returns: job rows, table rights, row security and policies
    """
    return {
        "rows": await connection.fetch("SELECT * FROM jobs ORDER BY id"),
        "rights": await connection.fetchrow(
            "SELECT relowner, relacl, relrowsecurity, relforcerowsecurity "
            "FROM pg_class WHERE oid = 'jobs'::regclass"
        ),
        "policies": await connection.fetch(
            "SELECT * FROM pg_policies WHERE tablename = 'jobs' ORDER BY policyname"
        ),
    }


async def test_chain_creates_the_declared_jobs_index(migrated: str) -> None:
    """The real chain and current declarations must build the same index.

    :param migrated: the database built by the migration chain
    """
    connection = await asyncpg.connect(migrated)
    try:
        index = await _index(connection)
        assert index["indisvalid"]
        assert index["definition"] == (
            "CREATE INDEX jobs_person_kind_latest ON public.jobs "
            "USING btree (person_id, kind, created_at DESC, id DESC)"
        )
        assert MIGRATION.STATEMENTS == [
            statement for statement in job_statements() if INDEX in statement
        ]
    finally:
        await connection.close()


async def test_index_can_be_added_twice_over_existing_jobs(migrated: str) -> None:
    """Replaying the migration preserves rows and rights, and keeps the index.

    :param migrated: the database built by the migration chain
    """
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    app = await asyncpg.connect(migrated)
    try:
        await admin.execute(
            "WITH person AS (INSERT INTO persons (id, email, display_name) "
            "VALUES (gen_random_uuid(), 'jobs@example.test', 'Jobs') RETURNING id) "
            "INSERT INTO jobs (id, person_id, kind, status) "
            "SELECT gen_random_uuid(), id, 'read_file', 'failed' FROM person"
        )
        before = await _state(admin)
        await app.execute("DROP INDEX jobs_person_kind_latest")
        first = None
        for _ in range(2):
            async with app.transaction():
                for statement in MIGRATION.STATEMENTS:
                    await app.execute(statement)
            index = await _index(app)
            assert index["indisvalid"]
            if first is None:
                first = index
            assert index == first
            assert await _state(admin) == before
    finally:
        await app.close()
        await admin.close()
