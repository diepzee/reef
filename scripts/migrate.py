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
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "piccolo.main",
            "migrations",
            "forwards",
            "rif",
            env={**os.environ, "DATABASE_URL": dsn},
        )
        return await process.wait()
    finally:
        await connection.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
