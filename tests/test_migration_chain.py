"""The drill: can a database still be built from nothing?

Every other test in this suite runs against a schema ``conftest`` assembles
from the *current* table definitions and the *current* policy DDL. That is
the right shape for testing behaviour and it is structurally incapable of
catching one class of bug: a migration that cannot run.

Historical migrations run against the schema as it was when they were
written. Anything they reach for that arrived later -- a column, a table --
is refused at the moment they run, and only on a database that has not
already passed them. Production, long past those migrations, never notices.
A fresh deploy notices immediately, and so does the restore drill in
``docs/restore.md``, which is the one procedure nobody wants to discover is
broken.

That is not hypothetical. ``session_epoch`` and ``memberships.alias`` both
landed inside ``enable_statements`` -- which three August migrations call --
and no database could be built from scratch for a day, with every test still
green.

So this runs the real chain, in a real subprocess, against a database created
empty, and then checks that what comes out matches what the fixture builds.
It is the slowest test here by an order of magnitude. It earns it.
"""

import asyncio
import os
import sys

import asyncpg
import pytest
import pytest_asyncio
from conftest import seed_dsn

from reef.config import get_settings

#: Built and dropped per run. Named so a leftover after a hard kill is
#: obviously debris rather than something anybody's data lives in.
DRILL_DATABASE = "reef_migration_drill"

#: What ``docker/initdb`` gives the real databases at cluster bootstrap, and
#: what ``scripts/provision_authz_role.sql`` gives production. The chain is
#: entitled to assume this much and no more; anything else it needs, it must
#: create for itself.
_PROVISION = (
    "ALTER SCHEMA public OWNER TO reef",
    "GRANT CREATE ON SCHEMA public TO reef_authz",
    "GRANT USAGE ON SCHEMA public TO reef_probe",
    "CREATE EXTENSION IF NOT EXISTS pgcrypto",
)


def _admin_dsn(database: str) -> str:
    """Return the superuser DSN pointed at ``database``.

    :param database: the database name to connect to
    :returns: a DSN usable by asyncpg
    """
    base, _, _ = seed_dsn().rpartition("/")
    return f"{base}/{database}"


async def _can_create_databases() -> bool:
    """Report whether the seeding credential may create a database.

    :returns: True if the drill can run here
    """
    try:
        connection = await asyncpg.connect(_admin_dsn("postgres"))
    except (OSError, asyncpg.PostgresError):
        return False
    try:
        return bool(
            await connection.fetchval(
                "SELECT rolcreatedb OR rolsuper FROM pg_roles "
                "WHERE rolname = current_user"
            )
        )
    finally:
        await connection.close()


@pytest_asyncio.fixture(scope="module")
async def drilled() -> str:
    """Build a database from nothing by running the real migration chain.

    :returns: the DSN of the migrated database
    :raises AssertionError: if the chain does not complete
    """
    if not await _can_create_databases():
        pytest.skip("the seeding credential cannot create databases")

    admin = await asyncpg.connect(_admin_dsn("postgres"))
    try:
        # Terminate first: a previous run killed mid-drill leaves a
        # connection holding the database, and DROP would fail on it.
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            DRILL_DATABASE,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{DRILL_DATABASE}"')
        await admin.execute(f'CREATE DATABASE "{DRILL_DATABASE}" OWNER reef')
    finally:
        await admin.close()

    provisioner = await asyncpg.connect(_admin_dsn(DRILL_DATABASE))
    try:
        for statement in _PROVISION:
            await provisioner.execute(statement)
    finally:
        await provisioner.close()

    app = _swap_to_app_role(_admin_dsn(DRILL_DATABASE))
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "piccolo.main",
        "migrations",
        "forwards",
        "reef",
        env={**os.environ, "DATABASE_URL": app},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=_repo_root(),
    )
    stdout, _ = await process.communicate()
    output = stdout.decode()
    # Piccolo exits 0 even when a migration fails -- it catches the error,
    # prints "The command failed", and falls through to printing its own
    # usage. So the exit code cannot be trusted on its own.
    assert process.returncode == 0 and "The command failed" not in output, (
        f"the migration chain does not run against an empty database:\n{output}"
    )

    yield app

    admin = await asyncpg.connect(_admin_dsn("postgres"))
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            DRILL_DATABASE,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{DRILL_DATABASE}"')
    finally:
        await admin.close()


def _repo_root() -> str:
    """Return the directory holding ``piccolo_conf.py``.

    The Piccolo CLI resolves its app registry relative to the working
    directory, so the subprocess cannot inherit pytest's.

    :returns: an absolute path
    """
    import pathlib

    return str(pathlib.Path(__file__).resolve().parent.parent)


def _swap_to_app_role(dsn: str) -> str:
    """Return ``dsn`` with the owning role's credentials.

    Migrations run as ``reef`` locally -- the role that owns the databases --
    not as the superuser that created this one, so the drill exercises the
    privileges the chain actually has.

    :param dsn: a superuser DSN
    :returns: the same target as ``reef``
    """
    scheme, _, rest = dsn.partition("://")
    _, _, hostpart = rest.rpartition("@")
    return f"{scheme}://reef:reef@{hostpart}"


async def _shape(dsn: str) -> dict[str, set]:
    """Return the parts of a schema two builds of it must agree on.

    Grants are included, not just structure: the column-level narrowing on
    ``persons`` and ``coves`` is a security boundary, and a build that
    reproduced every table and forgot every ``REVOKE`` would look identical
    without it.

    :param dsn: which database to describe
    :returns: columns, functions, policies and update grants, as sets
    """
    connection = await asyncpg.connect(dsn)
    try:
        # Piccolo's own bookkeeping table is excluded throughout: it records
        # which migrations have run, so it exists only where migrations ran.
        # Its absence from the fixture's build is correct, not a divergence.
        columns = await connection.fetch(
            "SELECT table_name, column_name, data_type, is_nullable "
            "FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name <> 'migration'"
        )
        functions = await connection.fetch(
            "SELECT p.proname, pg_get_function_identity_arguments(p.oid) AS args "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.proname LIKE 'reef\\_%'"
        )
        # qual and with_check, not just the name. A predicate rewritten under
        # an unchanged policy name is the failure a name comparison cannot
        # see, and it is the one that matters -- the content policies are now
        # created only by a frozen snapshot, so an edit to _table_policies
        # that nothing re-applies would leave production enforcing the old
        # rule while every test enforced the new one.
        policies = await connection.fetch(
            "SELECT tablename, policyname, cmd, qual, with_check FROM pg_policies "
            "WHERE schemaname = 'public'"
        )
        grants = await connection.fetch(
            "SELECT table_name, column_name, grantee FROM "
            "information_schema.column_privileges WHERE table_schema = 'public' "
            "AND privilege_type = 'UPDATE' AND grantee IN ('reef', 'reef_app') "
            "AND table_name <> 'migration'"
        )
        return {
            "columns": {tuple(row) for row in columns},
            "functions": {tuple(row) for row in functions},
            "policies": {tuple(row) for row in policies},
            "update_grants": {tuple(row) for row in grants},
        }
    finally:
        await connection.close()


async def test_the_chain_runs_against_a_database_created_empty(drilled):
    """The property that was silently false for a day.

    The fixture asserts it; this names it, so a failure reads as what it is
    rather than as an error setting something up.
    """
    assert drilled


@pytest.mark.parametrize("part", ["columns", "policies", "update_grants"])
async def test_the_migrated_schema_matches_the_one_the_suite_builds(drilled, part):
    """Migrations and the fixture must converge, or the suite proves nothing.

    Every other test runs against the fixture's assembly of the current DDL.
    If the migration chain produces something else, then production -- which
    only ever gets there by migrating -- is not the thing under test.

    Everything but functions. Those are checked against ``reef.rls`` directly
    by the test below instead, because this database is long-lived and
    accumulates functions from branches that have since been rewritten --
    which would fail here as a divergence while saying nothing about the
    migrations. Policies have no such problem: they are dropped with their
    tables at the start of every session, so what is here was put here by
    today's DDL.

    Split per part so a mismatch says *what* diverged in the failure line.
    """
    fresh = await _shape(drilled)
    built = await _shape(get_settings().test_database_url)
    assert fresh[part] == built[part]


def _declared(pattern: str) -> set[str]:
    """Return the names the current DDL creates, matching ``pattern``.

    Read out of :func:`reef.rls`' own statements rather than out of any
    database, so this is the source of truth the migrations are judged
    against -- not a second copy of them.

    :param pattern: a regex with one capture group naming the object
    :returns: the set of captured names
    """
    import re

    from reef.rls import (
        alias_statements,
        appearance_statements,
        enable_statements,
        open_door_statements,
        person_column_grant_statements,
        session_epoch_statements,
    )

    everything = " ".join(
        enable_statements()
        + appearance_statements()
        + session_epoch_statements()
        + alias_statements()
        + open_door_statements()
        + person_column_grant_statements()
    )
    return set(re.findall(pattern, everything))


async def test_the_chain_creates_every_function_reef_rls_declares(drilled):
    """A helper the DDL declares but no migration applies does not exist.

    This is the failure that hides best. The suite builds its schema from
    ``reef.rls`` directly, so a function added there works in every test
    immediately; production only ever gets one by migrating. Add a function
    to a group no migration calls and the tests stay green while the feature
    is a 500 in production.
    """
    declared = _declared(r"CREATE OR REPLACE FUNCTION (rif_\w+)\(")
    connection = await asyncpg.connect(drilled)
    try:
        present = {
            row["proname"]
            for row in await connection.fetch(
                "SELECT p.proname FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.proname LIKE 'reef%'"
            )
        }
    finally:
        await connection.close()
    assert declared - present == set(), (
        "declared in reef.rls but no migration creates them: "
        f"{sorted(declared - present)}"
    )


async def test_the_chain_creates_every_policy_reef_rls_declares(drilled):
    """Same failure mode as the functions, on the boundary that matters more.

    A policy that exists in the fixture and not in production is not a
    missing feature -- it is a table with row security enabled and nothing
    constraining who reads it, or nothing at all constraining who writes it.
    """
    declared = _declared(r"CREATE POLICY (\w+) ON")
    connection = await asyncpg.connect(drilled)
    try:
        present = {
            row["policyname"]
            for row in await connection.fetch(
                "SELECT policyname FROM pg_policies WHERE schemaname = 'public'"
            )
        }
    finally:
        await connection.close()
    assert declared - present == set(), (
        f"declared in reef.rls but no migration creates them: "
        f"{sorted(declared - present)}"
    )
