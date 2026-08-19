"""Database engine and the transaction scope that RLS depends on.

Piccolo queries are ambient: a table is bound to an engine, and a query
carries no session object. That changes the shape of this project's safety
argument compared to the SQLAlchemy original, in one way that matters and
one that does not.

**Does not matter:** there is no session to thread through call signatures,
so the "one accessor, no raw queries" convention loses its most visible
enforcement point. That convention was never the real boundary -- RLS is.

**Does matter, and improves things:** because the principal is bound with
``set_config(..., is_local=true)`` inside a transaction, a query issued
*outside* :func:`transaction_scope` runs on an unarmed connection, and an
unarmed connection reads ``app.person_id`` as empty. ``reef.rls``'s policies
fold that to NULL, so the query returns **no rows**. Forgetting to arm
therefore fails closed -- the failure mode is a missing answer, never a
leaked one.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from piccolo.engine.postgres import PostgresEngine

from reef.config import get_settings

DB = PostgresEngine(config={"dsn": get_settings().dsn})


@asynccontextmanager
async def transaction_scope() -> AsyncIterator[None]:
    """Open a transaction pinned to one pooled connection.

    Every content query for a request must run inside this scope: it is what
    guarantees the ``set_config`` that arms RLS and the queries it protects
    share a connection. Piccolo commits on clean exit and rolls back if the
    body raises.

    :returns: an async context manager over the transaction
    """
    async with DB.transaction():
        yield


async def run_ddl_atomically(statements: list[str]) -> None:
    """Execute DDL on one connection, inside one transaction.

    Every statement or none. ``PostgresEngine._run_in_new_connection`` opens
    and closes a fresh connection per statement, so a sequence run through it
    commits piecemeal -- and a failure part-way through a policy migration
    can leave a table with row security *enabled* and no policy on it, which
    denies every row to everyone. On a branch that auto-deploys, that is an
    outage that persists until an operator repairs it by hand.

    PostgreSQL supports transactional DDL, so the whole set rolls back
    cleanly on any error.

    :param statements: SQL to execute in order
    """
    connection = await DB.get_new_connection()
    try:
        async with connection.transaction():
            for statement in statements:
                await connection.execute(statement)
    finally:
        await connection.close()
