"""Create the RLS-constrained role the application connects as.

Railway's Postgres hands every service the same bootstrap superuser. A
superuser carries ``BYPASSRLS``, and ``FORCE ROW LEVEL SECURITY`` does not
change that -- it extends policies to the table *owner*, which is a weaker
guarantee and does not reach a superuser at all. An app running on that
credential ignores every policy in ``rif.rls``, so the privacy boundary the
design depends on is not enforced, and the adversarial tests prove something
production does not do.

This script creates an ordinary role -- no ``SUPERUSER``, no ``BYPASSRLS``,
no DDL -- and grants it exactly the data access the application needs. Point
``DATABASE_URL`` at it and give the admin credential to
``RIF_MIGRATION_DATABASE_URL`` instead, so schema changes and request-time
queries no longer share a privilege level.

Idempotent: safe to re-run. Verifies its own work before exiting.

Run as::

    RIF_APP_ROLE_PASSWORD=... python scripts/provision_app_role.py <admin-dsn>
"""

import asyncio
import os
import sys

import asyncpg

ROLE = "rif_app"

# The application's own tables. Granted explicitly rather than via ALL TABLES
# so a future table is a deliberate decision, not an automatic grant.
TABLES = (
    "persons",
    "spaces",
    "memberships",
    "pages",
    "revisions",
    "attachments",
    "promotions",
)

# Tables carrying RLS policies (see rif.rls). Used only to verify enforcement.
PROTECTED = ("pages", "revisions", "attachments")


def _quote_literal(value: str) -> str:
    """Return ``value`` as a Postgres string literal.

    Needed because ``CREATE ROLE`` and ``ALTER ROLE`` are utility statements
    and reject bind parameters, so the password cannot be passed as ``$1``.

    :param value: the raw string to quote
    :returns: a single-quoted literal with embedded quotes doubled
    """
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


async def provision(admin_dsn: str, password: str) -> None:
    """Create or update the app role and its grants.

    :param admin_dsn: connection string for a role that can create roles
    :param password: password to set on the app role
    """
    conn = await asyncpg.connect(admin_dsn)
    try:
        database = await conn.fetchval("SELECT current_database()")
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", ROLE)
        # CREATE/ALTER ROLE are utility statements: Postgres rejects bind
        # parameters in them, so the password has to be inlined as a quoted
        # literal rather than passed as $1.
        verb = "ALTER" if exists else "CREATE"
        print(f"{'updating' if exists else 'creating'} role {ROLE}")
        await conn.execute(
            f"{verb} ROLE {ROLE} WITH LOGIN PASSWORD {_quote_literal(password)} "
            f"NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION"
        )

        await conn.execute(f'GRANT CONNECT ON DATABASE "{database}" TO {ROLE}')
        await conn.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
        # No CREATE: a role that can create objects in the schema can shadow
        # tables the policies are attached to.
        await conn.execute(f"REVOKE CREATE ON SCHEMA public FROM {ROLE}")

        for table in TABLES:
            await conn.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {ROLE}"
            )
        await conn.execute(
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {ROLE}"
        )
        await conn.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {ROLE}"
        )
        print(f"granted DML on {len(TABLES)} tables in {database}")
    finally:
        await conn.close()


async def verify(admin_dsn: str, password: str) -> bool:
    """Connect as the new role and prove RLS actually bites.

    :param admin_dsn: admin connection string, used to derive the app DSN
    :param password: the app role's password
    :returns: True when the role is constrained as intended
    """
    app_dsn = _swap_credentials(admin_dsn, ROLE, password)
    conn = await asyncpg.connect(app_dsn)
    ok = True
    try:
        row = await conn.fetchrow(
            "SELECT current_user AS role,"
            " (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS is_super,"
            " (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS bypass"
        )
        print(
            f"\nconnected as {row['role']} "
            f"(superuser={row['is_super']}, bypassrls={row['bypass']})"
        )
        if row["is_super"] or row["bypass"]:
            print("FAIL: the app role still bypasses row security")
            return False

        for table in PROTECTED:
            visible = await conn.fetchval(f"SELECT count(*) FROM {table}")
            verdict = "OK" if visible == 0 else "FAIL"
            if visible:
                ok = False
            print(
                f"  {table:<12} rows visible with no principal: {visible}  [{verdict}]"
            )

        try:
            await conn.execute("ALTER TABLE pages DISABLE ROW LEVEL SECURITY")
            print("  FAIL: the app role was able to disable RLS")
            ok = False
        except asyncpg.InsufficientPrivilegeError:
            print("  DDL refused, as intended                       [OK]")
    finally:
        await conn.close()
    return ok


def _swap_credentials(dsn: str, user: str, password: str) -> str:
    """Return ``dsn`` with its userinfo replaced.

    :param dsn: a ``postgresql://user:pass@host/db`` connection string
    :param user: replacement username
    :param password: replacement password
    :returns: the rewritten DSN
    """
    scheme, _, rest = dsn.partition("://")
    _, _, hostpart = rest.rpartition("@")
    return f"{scheme}://{user}:{password}@{hostpart}"


async def main() -> int:
    """Provision then verify.

    :returns: process exit code, 0 when the role is correctly constrained
    """
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    password = os.environ.get("RIF_APP_ROLE_PASSWORD")
    if not password:
        print("RIF_APP_ROLE_PASSWORD is not set")
        return 2

    await provision(sys.argv[1], password)
    if not await verify(sys.argv[1], password):
        print("\nVERIFICATION FAILED -- do not point DATABASE_URL at this role")
        return 1
    print("\nRole is constrained and RLS is enforced against it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
