"""Point-in-time reads: the revisions table answering "what did we know?".

The spec's data-model section promises this — "what did we know about the
boiler in March" as a WHERE clause. These tests hold it to that, and hold
the answer to the same RLS boundary as a present-day read.
"""

from datetime import UTC, datetime

from rif.access import Principal
from rif.pages import get_page_as_of, save_page
from rif.server import tool_read_page


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_returns_the_state_at_that_moment(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "house.md", "The boiler is broken.", message="x")
    between = datetime.now(UTC)
    await save_page(me, "personal", "house.md", "The boiler is fixed.", message="x")
    then = await get_page_as_of(me, "personal", "house.md", between)
    assert then["body"] == "The boiler is broken."
    now = await get_page_as_of(me, "personal", "house.md", datetime.now(UTC))
    assert now["body"] == "The boiler is fixed."


async def test_before_the_page_existed_is_none(tx, household):
    me = principal_for(household["wouter"])
    before = datetime.now(UTC)
    await save_page(me, "personal", "house.md", "The boiler is new.", message="x")
    assert await get_page_as_of(me, "personal", "house.md", before) is None


async def test_missing_page_is_none(tx, household):
    me = principal_for(household["wouter"])
    assert await get_page_as_of(me, "personal", "ghost.md", datetime.now(UTC)) is None


async def test_cannot_read_another_persons_past(tx, household):
    """History is guarded like the present; the positive control proves it."""
    partner = principal_for(household["partner"])
    await save_page(partner, "personal", "diary.md", "A private entry.", message="x")
    when = datetime.now(UTC)
    assert await get_page_as_of(partner, "personal", "diary.md", when) is not None
    me = principal_for(household["wouter"])
    assert await get_page_as_of(me, "personal", "diary.md", when) is None


async def test_tool_read_page_accepts_as_of(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "house.md", "The boiler is broken.", message="x")
    between = datetime.now(UTC)
    await save_page(me, "personal", "house.md", "The boiler is fixed.", message="x")
    result = await tool_read_page(me, "personal", "house.md", as_of=between.isoformat())
    assert result["body"] == "The boiler is broken."
    assert result["as_of"] == between.isoformat()
    assert "version" not in result


async def test_tool_read_page_as_of_not_found_names_the_moment(tx, household):
    me = principal_for(household["wouter"])
    before = datetime.now(UTC)
    await save_page(me, "personal", "house.md", "The boiler is new.", message="x")
    result = await tool_read_page(me, "personal", "house.md", as_of=before.isoformat())
    assert result["error"] == "not_found"
    assert result["as_of"] == before.isoformat()


async def test_tool_read_page_rejects_garbage_timestamps(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "house.md", "The boiler is new.", message="x")
    result = await tool_read_page(me, "personal", "house.md", as_of="last tuesday")
    assert result["error"] == "invalid_as_of"


async def test_tool_read_page_accepts_utc_timestamps(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "house.md", "The boiler is new.", message="x")
    utc_now = datetime.now(UTC).isoformat()
    result = await tool_read_page(me, "personal", "house.md", as_of=utc_now)
    assert result["body"] == "The boiler is new."
