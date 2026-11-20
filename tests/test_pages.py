from datetime import UTC, datetime

import pytest

from reef.access import Principal
from reef.config import get_settings
from reef.context import backfill_index_fields
from reef.links import backfill_links
from reef.models import Cove, Page, Revision
from reef.pages import (
    PageTooLarge,
    ProtectedPath,
    SectionNotFound,
    VersionConflict,
    edit_section,
    get_page,
    save_page,
)


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_save_then_get_round_trips(tx, household):
    me = principal_for(household["wouter"])
    await save_page(
        me,
        "personal",
        "health.md",
        "# Health\n\nSleeps badly.",
        message="initial",
        title="Health",
        tags=["person"],
    )
    page = await get_page(me, "personal", "health.md")
    assert page.title == "Health" and "Sleeps badly" in page.body


async def test_each_write_snapshots_full_state_and_bumps_versions(tx, household):
    me = principal_for(household["wouter"])
    await save_page(
        me, "personal", "a.md", "one", message="first", title="A", tags=["t"]
    )
    await save_page(me, "personal", "a.md", "two", message="second")
    revisions = await Revision.objects().order_by(Revision.created_at)
    assert [r.body for r in revisions] == ["one", "two"]
    assert revisions[1].title == "A" and revisions[1].tags == ["t"]
    page = await get_page(me, "personal", "a.md")
    assert page.version == 2
    cove = await Cove.objects().where(Cove.id == household["w_personal"].id).first()
    assert cove.version == 2


async def test_stale_expected_version_conflicts(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "a.md", "one", message="x")
    with pytest.raises(VersionConflict):
        await save_page(
            me, "personal", "a.md", "clobber", message="x", expected_version=0
        )


async def test_one_persons_page_is_invisible_to_the_other(tx, household):
    mine = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_page(mine, "personal", "secret.md", "mine", message="x")
    assert await get_page(theirs, "personal", "secret.md") is None


async def test_household_pages_are_visible_to_both(tx, household):
    mine = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_page(mine, "household", "house.md", "boiler", message="x")
    assert (await get_page(theirs, "household", "house.md")).body == "boiler"


async def test_edit_section_replaces_exactly_one_occurrence(tx, household):
    me = principal_for(household["wouter"])
    await save_page(
        me, "household", "house.md", "Boiler: Vaillant\nRoof: tiles", message="x"
    )
    page = await edit_section(
        me,
        "household",
        "house.md",
        "Boiler: Vaillant",
        "Boiler: Vaillant ecoTEC",
        message="model number",
    )
    assert page.body == "Boiler: Vaillant ecoTEC\nRoof: tiles"
    with pytest.raises(SectionNotFound):
        await edit_section(
            me, "household", "house.md", "Chimney", "Chimney: swept", message="x"
        )


async def test_meta_paths_are_protected_from_generic_writes(tx, household):
    me = principal_for(household["wouter"])
    with pytest.raises(ProtectedPath):
        await save_page(me, "personal", "meta/persona.md", "hijacked", message="x")
    page = await save_page(
        me, "personal", "meta/persona.md", "legit", message="x", allow_protected=True
    )
    assert page.body == "legit"


async def test_a_body_over_the_ceiling_is_refused(tx, household):
    """Pages had no size limit while files were capped at 25 MB, so prose was
    the cheapest way to bloat a cove -- and every body is parsed on save and
    loaded whole on read, so one oversized page taxes everyone who opens it."""
    me = principal_for(household["wouter"])
    ceiling = get_settings().pages.max_chars
    with pytest.raises(PageTooLarge):
        await save_page(me, "household", "bloat.md", "A" * (ceiling + 1), message="x")
    assert await get_page(me, "household", "bloat.md") is None


async def test_a_body_at_the_ceiling_is_allowed(tx, household):
    me = principal_for(household["wouter"])
    ceiling = get_settings().pages.max_chars
    page = await save_page(me, "household", "big.md", "A" * ceiling, message="x")
    assert len(page.body) == ceiling


# --- updated_at ----------------------------------------------------------------

LONG_AGO = datetime(2020, 1, 1, 12, 0, 0, tzinfo=UTC).replace(tzinfo=None)


async def _age(path: str) -> None:
    """Make a page look as if it was written long ago.

    :param path: the page to age
    """
    await Page.update({Page.created_at: LONG_AGO, Page.updated_at: LONG_AGO}).where(
        Page.path == path
    )


async def _stamps(path: str) -> tuple[datetime, datetime]:
    """Read a page's two timestamps, bypassing the code under test.

    :param path: the page to read
    :returns: its ``created_at`` and ``updated_at``
    """
    row = (
        await Page.select(Page.created_at, Page.updated_at)
        .where(Page.path == path)
        .first()
    )
    return row["created_at"], row["updated_at"]


async def test_a_new_page_is_updated_when_it_is_created(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "a.md", "one", message="x")
    created, updated = await _stamps("a.md")
    assert updated == created


@pytest.mark.parametrize(
    "change",
    [
        {"body": "two"},
        {"body": "one", "title": "Renamed"},
        {"body": "one", "tags": ["new"]},
    ],
    ids=["body", "title", "tags"],
)
async def test_editing_a_page_moves_updated_at_and_leaves_created_at(
    tx, household, change
):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "a.md", "one", message="x", title="A", tags=[])
    await _age("a.md")
    await save_page(me, "personal", "a.md", message="edit", **change)
    created, updated = await _stamps("a.md")
    assert created == LONG_AGO
    assert updated > LONG_AGO


async def test_editing_a_section_moves_updated_at_and_leaves_created_at(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "a.md", "Boiler: old", message="x")
    await _age("a.md")
    await edit_section(me, "personal", "a.md", "old", "new", message="fix")
    created, updated = await _stamps("a.md")
    assert created == LONG_AGO
    assert updated > LONG_AGO


async def test_background_bookkeeping_does_not_move_updated_at(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "a.md", "Boiler: [[b.md]]", message="x")
    await _age("a.md")
    await Page.raw(
        "UPDATE pages SET indexed_version = NULL, linked_version = NULL, "
        "reference_targets = NULL, description = NULL, size = NULL"
    )
    assert await backfill_index_fields(me) == 1
    await Page.raw("UPDATE pages SET linked_version = NULL")
    assert await backfill_links(me) == 1
    created, updated = await _stamps("a.md")
    assert (created, updated) == (LONG_AGO, LONG_AGO)
