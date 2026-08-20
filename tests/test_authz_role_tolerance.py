"""The DDL works whether this cluster's authz role is named rif_ or reef_.

Renaming that role is an out-of-band operator step: the migration credential
cannot ALTER ROLE, as the 12 August migration records about creating it in
the first place. So the rename happens on the operator's schedule, not on a
deploy's, and the application has to work on both sides of it without
knowing which side it is on.

The role is NOLOGIN — nothing connects as it — so renaming it cannot break a
credential. That is what separates it from reef/reef_app/reef_probe, whose names
live in connection strings.
"""

import asyncpg
import pytest

from reef.config import get_settings
from reef.rls import AUTHZ_ROLE, FORMER_AUTHZ_ROLE, authz_role_expression


async def resolve(connection, present: str | None) -> str | None:
    """Ask the database which authz role the expression picks."""
    return await connection.fetchval(f"SELECT {authz_role_expression()}")


@pytest.fixture
async def connection():
    conn = await asyncpg.connect(get_settings().test_database_url)
    yield conn
    await conn.close()


def test_the_new_name_is_what_a_fresh_cluster_gets():
    assert AUTHZ_ROLE == "reef_authz"
    assert FORMER_AUTHZ_ROLE == "rif_authz"


async def test_the_expression_prefers_the_new_name(connection):
    """A cluster the operator has already renamed."""
    existing = await connection.fetch(
        "SELECT rolname FROM pg_roles WHERE rolname IN ($1, $2)",
        AUTHZ_ROLE,
        FORMER_AUTHZ_ROLE,
    )
    names = {row["rolname"] for row in existing}
    chosen = await resolve(connection, None)
    if AUTHZ_ROLE in names:
        assert chosen == AUTHZ_ROLE
    elif FORMER_AUTHZ_ROLE in names:
        assert chosen == FORMER_AUTHZ_ROLE
    else:
        # Neither exists: a cluster about to have one created.
        assert chosen == AUTHZ_ROLE


async def test_it_never_resolves_to_nothing(connection):
    """A NULL here would produce `OWNER TO ""`, which fails obscurely."""
    assert await resolve(connection, None) is not None
