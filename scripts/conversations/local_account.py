"""Create, or reset, the local made-up person that test conversations run against.

Run it with ``just conversations-account``. It makes ``conversations@reef.local``
in the development database, with a personal space and a shared space nobody
else is in, flags the person as a test account, and loads the made-up person.
Running it again resets the account to the same state.

Never in production: see :func:`evalkit.refuse_unless_local`.
"""

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

import evalkit

from reef.access import AccessDenied, Principal, resolve_space
from reef.attachments import ObjectStore
from reef.config import get_settings
from reef.db import DB, transaction_scope
from reef.spaces import create_space
from reef.testaccount import ResetResult, configured_store, reset

DEFAULT_EMAIL = "conversations@reef.local"


def _superuser_dsn(app_dsn: str) -> str:
    """Return the connection string that creates and flags the person.

    Defaults to the superuser the local ``docker-compose.yml`` creates, on
    the same database as the app. Override with ``REEF_SEED_DATABASE_URL``.

    :param app_dsn: the app's own connection string
    :returns: a connection string for the superuser
    """
    override = os.environ.get("REEF_SEED_DATABASE_URL")
    if override:
        return override
    parts = urlsplit(app_dsn)
    host = parts.hostname or "localhost"
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit(parts._replace(netloc=f"postgres:postgres@{host}{port}"))


async def _flag(superuser_dsn: str, person_id: UUID) -> None:
    """Flag a person as a test account, as the database owner.

    :param superuser_dsn: a connection string for the owner role
    :param person_id: the person to flag
    """
    connection = await asyncpg.connect(superuser_dsn)
    try:
        await connection.execute(
            "INSERT INTO test_accounts (person_id) VALUES ($1) ON CONFLICT DO NOTHING",
            person_id,
        )
    finally:
        await connection.close()


async def _ensure_shared(principal: Principal, shared: str) -> None:
    """Create the shared space unless the person already has it.

    :param principal: the person
    :param shared: the space's name
    """
    async with transaction_scope():
        try:
            await resolve_space(principal, shared)
        except AccessDenied:
            await create_space(principal, shared)


async def prepare_local_account(
    email: str = DEFAULT_EMAIL,
    shared: str = "household",
    store: ObjectStore | None = None,
) -> tuple[UUID, ResetResult]:
    """Make the local test account ready and say what was loaded.

    :param email: the account's address
    :param shared: the name of the shared space nobody else joins
    :param store: where file bytes go; the configured store when None, which
        locally is no store, so files are skipped
    :raises evalkit.NotLocal: when the database or environment is not local
    :returns: the person's id and the result of the reset
    """
    app_dsn = get_settings().dsn
    superuser_dsn = _superuser_dsn(app_dsn)
    evalkit.refuse_unless_local(app_dsn, superuser_dsn)
    principal = await evalkit.ensure_eval_person(superuser_dsn, email, "Conversations")
    await _ensure_shared(principal, shared)
    await _flag(superuser_dsn, principal.person_id)
    result = await reset(principal, store or configured_store())
    return principal.person_id, result


async def ensure_local_account(
    email: str = DEFAULT_EMAIL,
    shared: str = "household",
    store: ObjectStore | None = None,
) -> UUID:
    """Create the local test account if needed, and reset it.

    Safe to run twice: the second run finds the person and their spaces and
    resets the account to the same state.

    :param email: the account's address
    :param shared: the name of the shared space nobody else joins
    :param store: where file bytes go; the configured store when None
    :raises evalkit.NotLocal: when the database or environment is not local
    :returns: the person's id
    """
    person_id, _ = await prepare_local_account(email, shared, store)
    return person_id


async def _run() -> str:
    """Run against the local database and describe the outcome in one line.

    :returns: the line to print
    """
    await DB.start_connection_pool()
    try:
        _, result = await prepare_local_account()
    finally:
        await DB.close_connection_pool()
    files = (
        f"{result.files} files"
        if result.services["files"]
        else "0 files (no file storage here)"
    )
    return f"{DEFAULT_EMAIL} is ready: {result.engrams} engrams, {files}"


def main() -> None:
    """Run, and exit with the reason when the database is not local."""
    try:
        print(asyncio.run(_run()))
    except evalkit.NotLocal as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
