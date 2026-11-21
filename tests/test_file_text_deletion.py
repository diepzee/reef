"""Deletion takes the text read from a file with it.

``file_pages`` and ``file_embeddings`` reference their file, and
``reading_spend`` its person, with ``ON DELETE CASCADE``. These tests pin that
the cascades reach the three ways a file goes: ``forget``, deleting a space and
deleting an account. Rows are seeded and counted through the policy-free
connection.
"""

from uuid import UUID

import pytest
from asyncpg import Connection
from conftest import MemoryStore, attach_file, caller_for

from reef.access import Principal
from reef.account import delete_account_rows
from reef.config import get_settings
from reef.db import transaction_scope
from reef.methods import maintain
from reef.spaces import delete_space


@pytest.fixture(autouse=True)
def secret(monkeypatch) -> None:
    """Give staged tokens a signing secret long enough to use.

    :param monkeypatch: pytest's monkeypatch fixture
    """
    monkeypatch.setattr(get_settings().web, "session_secret", "m" * 40)


async def _give_text(seed: Connection, file: UUID) -> None:
    """Give a file two pages and a vector.

    :param seed: the policy-free connection
    :param file: the file's id
    """
    for number in (1, 2):
        await seed.execute(
            "INSERT INTO file_pages (id, attachment_id, number, text, reader) "
            "VALUES (gen_random_uuid(), $1, $2, 'words', 'tesseract')",
            file,
            number,
        )
    await seed.execute(
        "INSERT INTO file_embeddings "
        "(id, attachment_id, model_id, dims, content_hash, embedding) "
        "VALUES (gen_random_uuid(), $1, 'fake-1', 3, 'x', '[1,2,3]'::vector)",
        file,
    )


async def _file_in(seed: Connection, space: UUID) -> UUID:
    """Store a ready file row in a space.

    :param seed: the policy-free connection
    :param space: the space's id
    :returns: the file's id
    """
    return await seed.fetchval(
        "INSERT INTO attachments (id, space_id, object_key, filename, mime, "
        "byte_size, description, status) VALUES (gen_random_uuid(), $1, "
        "'attachments/x', 'x.txt', 'text/plain', 1, 'x', 'ready') RETURNING id",
        space,
    )


async def _left(seed: Connection) -> tuple[int, int]:
    """Count the pages and vectors that remain.

    :param seed: the policy-free connection
    :returns: the number of page rows and of vector rows
    """
    pages = await seed.fetchval("SELECT count(*) FROM file_pages")
    vectors = await seed.fetchval("SELECT count(*) FROM file_embeddings")
    return pages, vectors


async def test_forget_takes_the_pages_and_vectors(household, seed, monkeypatch):
    """A confirmed ``forget`` of a file leaves no page or vector of it."""
    monkeypatch.setattr(maintain, "S3ObjectStore", MemoryStore)
    me = caller_for(household["wouter"])
    key = await attach_file(me, "personal", "A policy.")
    file = await seed.fetchval("SELECT id FROM attachments WHERE object_key = $1", key)
    await _give_text(seed, file)
    assert await _left(seed) == (2, 1)

    staged = await maintain.forget(me, "", "personal", file=key)
    await maintain.forget(me, "", "personal", file=key, confirm=staged["token"])

    assert await _left(seed) == (0, 0)


async def test_deleting_a_space_takes_its_files_pages_and_vectors(graph, seed):
    """Deleting a shared space leaves no text read from its files."""
    alice = await graph.person("alice@x.com", "Alice")
    await graph.personal_space(alice)
    solo = await graph.shared_space("solo", alice)
    principal = Principal(person_id=alice.id, email=alice.email)
    file = await _file_in(seed, solo.id)
    await _give_text(seed, file)

    async with transaction_scope():
        await delete_space(principal, "solo")

    assert await _left(seed) == (0, 0)


async def test_deleting_an_account_takes_pages_vectors_and_reading_spend(graph, seed):
    """Deleting an account leaves no text, vector or reading spend of the person."""
    alice = await graph.person("alice@x.com", "Alice")
    personal = await graph.personal_space(alice)
    principal = Principal(person_id=alice.id, email=alice.email)
    file = await _file_in(seed, personal.id)
    await _give_text(seed, file)
    await seed.execute(
        "INSERT INTO reading_spend (id, person_id, day, reader, pages) "
        "VALUES (gen_random_uuid(), $1, current_date, 'tesseract', 2)",
        alice.id,
    )

    async with transaction_scope():
        await delete_account_rows(principal)

    assert await _left(seed) == (0, 0)
    assert await seed.fetchval("SELECT count(*) FROM reading_spend") == 0
