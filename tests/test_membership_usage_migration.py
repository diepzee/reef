"""Exercise membership cleanup through the real migration chain."""

import importlib
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from test_migration_chain import _admin_dsn, build_by_migrating, drop_database

from reef.rls import mutation_statements

MIGRATION = importlib.import_module(
    "reef.piccolo_migrations.reef_2026_11_26t13_00_00_000000"
)
DATABASE = "reef_membership_usage_drill"


async def apply_migration(connection: asyncpg.Connection) -> None:
    """Apply the fixed SQL in a single transaction.

    :param connection: the migration connection
    """
    async with connection.transaction():
        for statement in MIGRATION.STATEMENTS:
            await connection.execute(statement)


async def function_rights(connection: asyncpg.Connection) -> asyncpg.Record:
    """Read the membership helper's owner, rights and settings.

    :param connection: the database connection
    :returns: its security settings
    """
    return await connection.fetchrow(
        "SELECT proowner, proacl, prosecdef, proconfig, provolatile "
        "FROM pg_proc WHERE oid = 'reef_remove_member(uuid,uuid)'::regprocedure"
    )


@pytest_asyncio.fixture(scope="module")
async def migrated() -> AsyncIterator[str]:
    """Upgrade the preceding schema twice as the migration role.

    :returns: the database DSN
    """
    dsn = await build_by_migrating(DATABASE, upto="2026-11-26T11:00:00:000000")
    app = await asyncpg.connect(dsn)
    try:
        before = await function_rights(app)
        for _ in range(2):
            await apply_migration(app)
            assert await function_rights(app) == before
        yield dsn
    finally:
        await app.close()
        await drop_database(DATABASE)


@pytest_asyncio.fixture
async def connection(migrated: str) -> AsyncIterator[asyncpg.Connection]:
    """Seed and inspect hidden rows in a transaction which rolls back.

    :param migrated: the upgraded database
    :returns: a connection able to switch to the app role
    """
    admin = await asyncpg.connect(_admin_dsn(DATABASE))
    transaction = admin.transaction()
    await transaction.start()
    try:
        yield admin
    finally:
        await transaction.rollback()
        await admin.close()


async def seed_usage(connection: asyncpg.Connection) -> tuple[UUID, UUID, UUID]:
    """Seed two members using a shared space and each one's other space.

    :param connection: the seeding connection
    :returns: owner, member and shared space identifiers
    """
    owner, member, shared = uuid4(), uuid4(), uuid4()
    for person in (owner, member):
        await connection.execute(
            "INSERT INTO persons (id, email, display_name, subject) "
            "VALUES ($1, $2, 'Member', $2)",
            person,
            f"{person}@example.test",
        )
    for space, holder, members in (
        (shared, owner, (owner, member)),
        (uuid4(), owner, (owner,)),
        (uuid4(), member, (member,)),
    ):
        await connection.execute(
            "INSERT INTO spaces (id, slug, kind, owner_person_id) "
            "VALUES ($1, $2, 'shared', $3)",
            space,
            str(space),
            holder,
        )
        for person in members:
            await connection.execute(
                "INSERT INTO memberships (person_id, space_id, role, alias) "
                "VALUES ($1, $2, 'member', $3)",
                person,
                space,
                str(space),
            )
        for path in ("first.md", "second.md"):
            engram = uuid4()
            await connection.execute(
                "INSERT INTO engrams (id, space_id, path, title, body) "
                "VALUES ($1, $2, $3, 'Used', 'Private text')",
                engram,
                space,
                path,
            )
            for person in members:
                await connection.execute(
                    "INSERT INTO engram_access (id, person_id, engram_id) "
                    "VALUES (gen_random_uuid(), $1, $2)",
                    person,
                    engram,
                )
                await connection.execute(
                    "INSERT INTO engram_heat (id, person_id, engram_id, "
                    "activation, accesses, last_access) "
                    "VALUES (gen_random_uuid(), $1, $2, 1, 1, LOCALTIMESTAMP)",
                    person,
                    engram,
                )
    return owner, member, shared


async def stored_usage(
    connection: asyncpg.Connection,
) -> dict[str, list[asyncpg.Record]]:
    """Read both complete tables, including each engram's space.

    :param connection: the connection that bypasses row security
    :returns: rows ordered by identifier
    """
    return {
        table: await connection.fetch(
            f"SELECT u.*, e.space_id FROM {table} u "
            "JOIN engrams e ON e.id = u.engram_id ORDER BY u.id"
        )
        for table in ("engram_access", "engram_heat")
    }


def test_fixed_helper_matches_current_declaration() -> None:
    """The deployed helper and the helper used by behaviour tests agree."""
    assert MIGRATION.STATEMENTS[0] == next(
        statement
        for statement in mutation_statements()
        if statement.startswith("CREATE OR REPLACE FUNCTION reef_remove_member(")
    )


@pytest.mark.parametrize("way", ["removed", "member_leaves", "owner_leaves"])
async def test_migrated_helper_erases_only_departing_usage(
    connection: asyncpg.Connection, way: str
) -> None:
    """A restricted caller invokes the upgraded helper for every departure."""
    owner, member, space = await seed_usage(connection)
    before = await stored_usage(connection)
    departing = owner if way == "owner_leaves" else member
    caller = member if way == "member_leaves" else owner
    await connection.execute("SET LOCAL ROLE reef_probe")
    await connection.execute(
        "SELECT set_config('app.person_id', $1, true)", str(caller)
    )
    if way == "owner_leaves":
        assert await connection.fetchval(
            "SELECT reef_transfer_space_ownership($1, $2)", space, member
        )
    result = await connection.fetchrow(
        "SELECT * FROM reef_remove_member($1, $2)", space, departing
    )
    assert tuple(result) == (True, False)
    await connection.execute("RESET ROLE")
    assert await stored_usage(connection) == {
        table: [
            row
            for row in rows
            if not (row["person_id"] == departing and row["space_id"] == space)
        ]
        for table, rows in before.items()
    }


@pytest.mark.parametrize(
    "caller", ["unarmed", "owner_self", "member_other", "outsider"]
)
async def test_helper_refuses_unauthorized_departure(
    connection: asyncpg.Connection, caller: str
) -> None:
    """The broader self-leave permission gives no rights over other members."""
    owner, member, space = await seed_usage(connection)
    before = await stored_usage(connection)
    principal = {"unarmed": "", "owner_self": str(owner), "member_other": str(member)}
    await connection.execute("SET LOCAL ROLE reef_probe")
    await connection.execute(
        "SELECT set_config('app.person_id', $1, true)",
        principal.get(caller, str(uuid4())),
    )
    target = owner if caller in ("owner_self", "member_other") else member
    assert tuple(
        await connection.fetchrow(
            "SELECT * FROM reef_remove_member($1, $2)", space, target
        )
    ) == (False, False)
    await connection.execute("RESET ROLE")
    assert await stored_usage(connection) == before


async def test_migration_preserves_former_role_ownership_and_grants(
    connection: asyncpg.Connection,
) -> None:
    """Resolve the existing function owner even when both role spellings exist."""
    for role, attributes in (
        ("rif_authz", "NOLOGIN BYPASSRLS"),
        ("rif_app", "NOLOGIN"),
    ):
        if not await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", role
        ):
            await connection.execute(f"CREATE ROLE {role} {attributes}")
    await connection.execute("GRANT rif_authz TO reef")
    await connection.execute("GRANT USAGE, CREATE ON SCHEMA public TO rif_authz")
    await connection.execute("GRANT USAGE ON SCHEMA public TO rif_app")
    # The ownership-transfer helper already gives this role UPDATE on spaces.
    await connection.execute("GRANT SELECT, UPDATE ON spaces TO rif_authz")
    await connection.execute(
        "GRANT SELECT, DELETE ON memberships, persons TO rif_authz"
    )
    await connection.execute(
        "ALTER FUNCTION reef_remove_member(uuid,uuid) OWNER TO rif_authz"
    )
    await connection.execute(
        "GRANT EXECUTE ON FUNCTION reef_remove_member(uuid,uuid) TO rif_app"
    )
    before = await function_rights(connection)
    owner, member, space = await seed_usage(connection)
    await connection.execute("SET LOCAL ROLE reef")
    for _ in range(2):
        await apply_migration(connection)
        assert await function_rights(connection) == before
    await connection.execute("SET LOCAL ROLE rif_app")
    await connection.execute("SELECT set_config('app.person_id', $1, true)", str(owner))
    assert tuple(
        await connection.fetchrow(
            "SELECT * FROM reef_remove_member($1, $2)", space, member
        )
    ) == (True, False)
    await connection.execute("RESET ROLE")
    assert all(
        not (row["person_id"] == member and row["space_id"] == space)
        for rows in (await stored_usage(connection)).values()
        for row in rows
    )


async def test_migration_erases_old_orphans_and_keeps_members_and_owners(
    connection: asyncpg.Connection,
) -> None:
    """Repair stored hidden rows, preserving owners without membership rows."""
    owner, member, space = await seed_usage(connection)
    await connection.execute("DELETE FROM memberships WHERE space_id = $1", space)
    await connection.execute(
        "INSERT INTO search_log (id, person_id, query, engram_ids) "
        "VALUES (gen_random_uuid(), $1, 'private search', "
        "ARRAY(SELECT id FROM engrams WHERE space_id = $2))",
        member,
        space,
    )
    before = await stored_usage(connection)
    untouched = {
        table: await connection.fetch(f"SELECT * FROM {table} ORDER BY id")
        for table in ("persons", "spaces", "memberships", "engrams", "search_log")
    }
    expected = {
        table: [
            row
            for row in rows
            if not (row["person_id"] == member and row["space_id"] == space)
        ]
        for table, rows in before.items()
    }
    for _ in range(2):
        await connection.execute("SET LOCAL ROLE reef")
        await apply_migration(connection)
        assert await connection.fetchval("SELECT current_user") == "reef"
        await connection.execute("RESET ROLE")
        assert await stored_usage(connection) == expected
        for table, rows in untouched.items():
            assert await connection.fetch(f"SELECT * FROM {table} ORDER BY id") == rows
    assert any(row["person_id"] == owner for row in expected["engram_access"])
