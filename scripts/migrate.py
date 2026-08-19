"""Run migrations under a Postgres advisory lock.

The Alembic setup this replaced took a session-level advisory lock in
``migrations/env.py`` so two containers booting at once could not run the
same migration concurrently. Piccolo's CLI has no equivalent, and dropping
the property silently would be a regression: Railway restarts and rolling
deploys can overlap two instances, and a half-applied DDL migration is the
kind of failure that needs a human and a backup.

The lock is session-level and released when this process's connection
closes, so a crashed migration cannot wedge the next boot permanently.

Run as: ``python scripts/migrate.py``
"""

import asyncio
import os
import sys

import asyncpg

from reef.config import get_settings

# Arbitrary but fixed: any constant works as long as every deployment of
# this application uses the same one.
LOCK_KEY = 0x5249_4620


#: The app name Piccolo files migrations under. It was "rif" until the module
#: was renamed; see :func:`adopt_app_name` for why both names appear here.
APP_NAME = "reef"
FORMER_APP_NAME = "rif"


async def adopt_app_name(connection: asyncpg.Connection) -> int:
    """Move migration records from the old app name to the new one.

    Piccolo files every applied migration under an app name, and on the next
    boot asks which migrations have run *for that app*. Rename the app in
    code without moving the records and it asks about an app that has never
    run anything -- so it replays the whole chain against a populated
    database.

    A migration cannot fix this. By the time one runs, Piccolo has already
    decided the chain is unapplied and is part-way through replaying it. It
    has to happen before Piccolo reads anything, which is why it lives here.

    Idempotent, and a no-op on a database whose ``migration`` table Piccolo
    has not created yet -- the first boot of a fresh deployment.

    :param connection: a connection under the migration credential; the app
        role cannot read this table, by design
    :returns: how many records were moved
    """
    if not await connection.fetchval("SELECT to_regclass('migration')"):
        return 0
    moved = await connection.fetch(
        "UPDATE migration SET app_name = $1 WHERE app_name = $2 RETURNING name",
        APP_NAME,
        FORMER_APP_NAME,
    )
    return len(moved)


async def _main() -> int:
    """Take the lock, run migrations forwards, release by disconnecting.

    Runs under ``migration_dsn`` rather than the app's own connection. The
    app role is RLS-constrained and deliberately has no DDL rights, since a
    role that can ``ALTER TABLE`` can also ``DROP POLICY`` -- which would put
    the privacy boundary inside the blast radius of an application bug.

    ``piccolo_conf`` builds its engine from ``reef.db.DB``, which reads
    ``DATABASE_URL``, so the subprocess is handed an overridden environment.
    Without that the CLI would connect as the app role and the DDL would
    fail, which is the whole reason this indirection exists.

    :returns: the migration process's exit code
    """
    dsn = get_settings().migration_dsn
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute("SELECT pg_advisory_lock($1)", LOCK_KEY)
        # Under the lock, so two booting containers cannot both decide the
        # records need moving and race each other.
        moved = await adopt_app_name(connection)
        if moved:
            print(f"moved {moved} migration records to app {APP_NAME!r}")
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "piccolo.main",
            "migrations",
            "forwards",
            APP_NAME,
            env={**os.environ, "DATABASE_URL": dsn},
        )
        return await process.wait()
    finally:
        await connection.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
