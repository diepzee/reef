"""The text read from files: pages, chunk vectors and spend, under row security.

Every statement here goes through the suite's own connection, armed as a
person. That connection owns the tables, but ``FORCE ROW LEVEL SECURITY``
subjects it to the policies, so the rows it may see and write are the rows the
app may.
"""

from uuid import UUID

import asyncpg
import pytest
from conftest import Store

from reef.access import Principal, arm
from reef.attachments import add_attachment, delete_attachment
from reef.db import transaction_scope
from reef.models import Attachment, TextStatus


def principal_for(person) -> Principal:
    """Return the principal a signed-in person acts as.

    :param person: a seeded person
    :returns: the principal
    """
    return Principal(person_id=person.id, email=person.email)


class ForgettingStore(Store):
    """A store that also deletes, as ``forget`` needs."""

    async def delete(self, key: str) -> None:
        """Delete nothing: the stand-in keeps no bytes.

        :param key: the object key
        """


async def _file(principal: Principal, space: str) -> Attachment:
    """Store a described PDF in a space, as ``store_file`` would.

    :param principal: who stores it
    :param space: the person's name for the space
    :returns: the stored file
    """
    return await add_attachment(
        principal,
        space,
        b"%PDF",
        "application/pdf",
        filename="policy.pdf",
        description="Car insurance",
        store=Store(),
    )


async def _as(principal: Principal, query: str, *args) -> list[dict]:
    """Run one statement as a person, in a transaction of its own.

    :param principal: who runs it
    :param query: SQL with ``{}`` placeholders
    :param args: the values for the placeholders
    :returns: the rows it returns
    """
    async with transaction_scope():
        await arm(principal)
        return await Attachment.raw(query, *args)


async def _add_page(
    principal: Principal, file: UUID, number: int = 1, text: str = "Een pagina."
) -> None:
    """Store one page of a file, read by Tesseract.

    :param principal: who stores it
    :param file: the file's id
    :param number: the page number
    :param text: the page's text
    """
    await _as(
        principal,
        "INSERT INTO file_pages (id, attachment_id, number, text, reader) "
        "VALUES (gen_random_uuid(), {}, {}, {}, 'tesseract')",
        file,
        number,
        text,
    )


async def _add_vector(principal: Principal, file: UUID) -> None:
    """Store the whole-file chunk vector of a file.

    :param principal: who stores it
    :param file: the file's id
    """
    await _as(
        principal,
        "INSERT INTO file_embeddings "
        "(id, attachment_id, model_id, dims, content_hash, embedding) "
        "VALUES (gen_random_uuid(), {}, 'fake-1', 3, 'x', '[1,2,3]'::vector)",
        file,
    )


async def _visible(principal: Principal, table: str) -> set[UUID]:
    """Return the files a person sees rows of, in one table.

    :param principal: who looks
    :param table: ``file_pages`` or ``file_embeddings``
    :returns: the ids of the files those rows belong to
    """
    rows = await _as(principal, f"SELECT attachment_id FROM {table}")
    return {row["attachment_id"] for row in rows}


async def test_a_page_is_visible_exactly_when_its_file_is(household):
    """The partner reads no page of Wouter's personal file; both read a shared one."""
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    mine = await _file(me, "personal")
    ours = await _file(me, "household")
    await _add_page(me, mine.id, text="Mijn dagboek.")
    await _add_page(partner, ours.id, text="De ketel.")
    assert await _visible(me, "file_pages") == {mine.id, ours.id}
    assert await _visible(partner, "file_pages") == {ours.id}


async def test_a_viewer_cannot_write_a_page(household, graph):
    """A viewer reads a space's files but cannot add text to them."""
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    ours = await _file(me, "household")
    await graph.set_role(household["partner"], household["shared"], "viewer")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _add_page(partner, ours.id)


async def test_a_chunk_vector_is_visible_exactly_when_its_file_is(household):
    """A vector can give its text away, so it is guarded as its file is."""
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    mine = await _file(me, "personal")
    ours = await _file(me, "household")
    await _add_vector(me, mine.id)
    await _add_vector(me, ours.id)
    assert await _visible(me, "file_embeddings") == {mine.id, ours.id}
    assert await _visible(partner, "file_embeddings") == {ours.id}


async def test_a_vector_cannot_be_planted_on_somebody_elses_file(household):
    """Writing a vector needs write access to the file's space."""
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    hers = await _file(partner, "personal")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _add_vector(me, hers.id)


async def test_reading_spend_is_private_to_the_person(household):
    """Each person sees and writes only their own spend."""
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    add = (
        "INSERT INTO reading_spend (id, person_id, day, reader, pages) "
        "VALUES (gen_random_uuid(), {}, CURRENT_DATE, 'tesseract', 3)"
    )
    await _as(me, add, me.person_id)
    count = "SELECT count(*) AS n FROM reading_spend"
    assert (await _as(me, count))[0]["n"] == 1
    assert (await _as(partner, count))[0]["n"] == 0
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _as(partner, add, me.person_id)


async def test_one_row_per_page(household):
    """A file has one text per page number."""
    me = principal_for(household["wouter"])
    file = await _file(me, "personal")
    await _add_page(me, file.id, number=1)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _add_page(me, file.id, number=1)


@pytest.mark.parametrize(
    "query",
    [
        (
            "INSERT INTO file_pages (id, attachment_id, number, reader) "
            "VALUES (gen_random_uuid(), {}, 1, 'scanner')"
        ),
        "UPDATE attachments SET text_status = 'done' WHERE id = {}",
    ],
    ids=["reader", "status"],
)
async def test_an_unknown_reader_or_status_is_refused(household, query):
    """Only the readers and statuses Reef knows may be stored."""
    me = principal_for(household["wouter"])
    file = await _file(me, "personal")
    with pytest.raises(asyncpg.CheckViolationError):
        await _as(me, query, file.id)


@pytest.mark.parametrize(
    "query",
    [
        (
            "INSERT INTO file_pages (id, attachment_id, number, reader) "
            "VALUES (gen_random_uuid(), {}, 0, 'tesseract')"
        ),
        (
            "INSERT INTO file_pages (id, attachment_id, number, reader, confidence) "
            "VALUES (gen_random_uuid(), {}, 1, 'tesseract', 101)"
        ),
        (
            "INSERT INTO file_embeddings "
            "(id, attachment_id, model_id, dims, content_hash, embedding) "
            "VALUES (gen_random_uuid(), {}, 'fake-1', 4, 'x', '[1,2,3]'::vector)"
        ),
    ],
    ids=["page-zero", "confidence", "dims"],
)
async def test_a_value_out_of_range_is_refused(household, query):
    """Pages count from 1, confidence is 0 to 100, a vector has its stated length."""
    me = principal_for(household["wouter"])
    file = await _file(me, "personal")
    with pytest.raises(asyncpg.CheckViolationError):
        await _as(me, query, file.id)


async def test_negative_spend_is_refused(household):
    """Spend only counts up from zero."""
    me = principal_for(household["wouter"])
    with pytest.raises(asyncpg.CheckViolationError):
        await _as(
            me,
            "INSERT INTO reading_spend (id, person_id, day, reader, input_tokens) "
            "VALUES (gen_random_uuid(), {}, CURRENT_DATE, 'tesseract', -1)",
            me.person_id,
        )


@pytest.mark.parametrize("status", list(TextStatus))
async def test_every_known_status_is_accepted(household, status):
    """The database's list of statuses is the code's."""
    me = principal_for(household["wouter"])
    file = await _file(me, "personal")
    await _as(
        me,
        "UPDATE attachments SET text_status = {} WHERE id = {}",
        status.value,
        file.id,
    )
    rows = await _as(me, "SELECT text_status FROM attachments WHERE id = {}", file.id)
    assert rows[0]["text_status"] == status.value


async def test_a_page_is_found_by_its_words(household):
    """A Dutch plural finds the page that holds the singular."""
    me = principal_for(household["wouter"])
    file = await _file(me, "personal")
    await _add_page(me, file.id, text="Het polisnummer staat bovenaan.")
    rows = await _as(
        me,
        "SELECT count(*) AS n FROM file_pages "
        "WHERE search_vector @@ websearch_to_tsquery('dutch', 'polisnummers')",
    )
    assert rows[0]["n"] == 1


async def test_deleting_a_file_deletes_its_pages_and_vectors(household, seed):
    """``forget`` takes a file's pages and vectors with it."""
    me = principal_for(household["wouter"])
    file = await _file(me, "personal")
    await _add_page(me, file.id)
    await _add_vector(me, file.id)
    assert await seed.fetchval("SELECT count(*) FROM file_pages") == 1
    assert await seed.fetchval("SELECT count(*) FROM file_embeddings") == 1
    assert await delete_attachment(me, "personal", file.object_key, ForgettingStore())
    assert await seed.fetchval("SELECT count(*) FROM file_pages") == 0
    assert await seed.fetchval("SELECT count(*) FROM file_embeddings") == 0
