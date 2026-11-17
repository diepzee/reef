"""pgvector is present and usable by the roles that will use it.

Nothing stores an embedding yet. This pins the ground the first one will
stand on: the extension is not trusted, so it has to be created by a
superuser at provisioning time, and a cluster where that step was skipped
must fail here rather than at the first migration that adds a vector column.
"""

import asyncpg
from conftest import probe_dsn

from reef.config import get_settings

_NEAREST = """
    SELECT label
    FROM (VALUES
        ('near', '[1, 0, 0]'::vector),
        ('far', '[0, 1, 0]'::vector)
    ) AS candidates (label, embedding)
    ORDER BY embedding <=> '[0.9, 0.1, 0]'::vector
"""


async def _nearest_first(dsn: str) -> list[str]:
    """Rank two fixed vectors by cosine distance to a query vector.

    :param dsn: connection string for the role under test
    :returns: the candidate labels, nearest first
    """
    connection = await asyncpg.connect(dsn)
    try:
        return [row["label"] for row in await connection.fetch(_NEAREST)]
    finally:
        await connection.close()


async def test_owner_role_ranks_by_cosine_distance():
    """The role that owns the tables, and runs migrations locally, has it."""
    assert await _nearest_first(get_settings().test_database_url) == ["near", "far"]


async def test_ordinary_role_ranks_by_cosine_distance():
    """So does a non-owner role, which is the shape of production's app role."""
    assert await _nearest_first(probe_dsn()) == ["near", "far"]


async def test_owner_role_can_build_an_hnsw_index():
    """The index type the search design relies on can be created."""
    connection = await asyncpg.connect(get_settings().test_database_url)
    try:
        async with connection.transaction():
            await connection.execute(
                "CREATE TEMP TABLE probe_embeddings (embedding vector(3)) "
                "ON COMMIT DROP"
            )
            await connection.execute(
                "CREATE INDEX ON probe_embeddings "
                "USING hnsw (embedding vector_cosine_ops)"
            )
    finally:
        await connection.close()
