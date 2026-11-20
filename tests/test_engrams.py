from datetime import UTC, datetime

import pytest

from reef.access import Principal
from reef.config import get_settings
from reef.context import backfill_index_fields
from reef.engrams import (
    EngramTooLarge,
    ProtectedPath,
    SectionNotFound,
    VersionConflict,
    edit_section,
    get_engram,
    save_engram,
)
from reef.links import backfill_links
from reef.models import Engram, Revision, Space


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_save_then_get_round_trips(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(
        me,
        "personal",
        "health.md",
        "# Health\n\nSleeps badly.",
        message="initial",
        title="Health",
        tags=["person"],
    )
    engram = await get_engram(me, "personal", "health.md")
    assert engram.title == "Health" and "Sleeps badly" in engram.body


async def test_each_write_snapshots_full_state_and_bumps_versions(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(
        me, "personal", "a.md", "one", message="first", title="A", tags=["t"]
    )
    await save_engram(me, "personal", "a.md", "two", message="second")
    revisions = await Revision.objects().order_by(Revision.created_at)
    assert [r.body for r in revisions] == ["one", "two"]
    assert revisions[1].title == "A" and revisions[1].tags == ["t"]
    engram = await get_engram(me, "personal", "a.md")
    assert engram.version == 2
    space = await Space.objects().where(Space.id == household["w_personal"].id).first()
    assert space.version == 2


async def test_stale_expected_version_conflicts(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "a.md", "one", message="x")
    with pytest.raises(VersionConflict):
        await save_engram(
            me, "personal", "a.md", "clobber", message="x", expected_version=0
        )


async def test_one_persons_engram_is_invisible_to_the_other(tx, household):
    mine = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_engram(mine, "personal", "secret.md", "mine", message="x")
    assert await get_engram(theirs, "personal", "secret.md") is None


async def test_household_engrams_are_visible_to_both(tx, household):
    mine = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_engram(mine, "household", "house.md", "boiler", message="x")
    assert (await get_engram(theirs, "household", "house.md")).body == "boiler"


async def test_edit_section_replaces_exactly_one_occurrence(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(
        me, "household", "house.md", "Boiler: Vaillant\nRoof: tiles", message="x"
    )
    engram = await edit_section(
        me,
        "household",
        "house.md",
        "Boiler: Vaillant",
        "Boiler: Vaillant ecoTEC",
        message="model number",
    )
    assert engram.body == "Boiler: Vaillant ecoTEC\nRoof: tiles"
    with pytest.raises(SectionNotFound):
        await edit_section(
            me, "household", "house.md", "Chimney", "Chimney: swept", message="x"
        )


async def test_meta_paths_are_protected_from_generic_writes(tx, household):
    me = principal_for(household["wouter"])
    with pytest.raises(ProtectedPath):
        await save_engram(me, "personal", "meta/persona.md", "hijacked", message="x")
    engram = await save_engram(
        me, "personal", "meta/persona.md", "legit", message="x", allow_protected=True
    )
    assert engram.body == "legit"


async def test_a_body_over_the_ceiling_is_refused(tx, household):
    """Engrams had no size limit while files were capped at 25 MB, so prose was
    the cheapest way to bloat a space -- and every body is parsed on save and
    loaded whole on read, so one oversized engram taxes everyone who opens it."""
    me = principal_for(household["wouter"])
    ceiling = get_settings().engrams.max_chars
    with pytest.raises(EngramTooLarge):
        await save_engram(me, "household", "bloat.md", "A" * (ceiling + 1), message="x")
    assert await get_engram(me, "household", "bloat.md") is None


async def test_a_body_at_the_ceiling_is_allowed(tx, household):
    me = principal_for(household["wouter"])
    ceiling = get_settings().engrams.max_chars
    engram = await save_engram(me, "household", "big.md", "A" * ceiling, message="x")
    assert len(engram.body) == ceiling


# --- updated_at ----------------------------------------------------------------

LONG_AGO = datetime(2020, 1, 1, 12, 0, 0, tzinfo=UTC).replace(tzinfo=None)


async def _age(path: str) -> None:
    """Make an engram look as if it was written long ago.

    :param path: the engram to age
    """
    await Engram.update(
        {Engram.created_at: LONG_AGO, Engram.updated_at: LONG_AGO}
    ).where(Engram.path == path)


async def _stamps(path: str) -> tuple[datetime, datetime]:
    """Read an engram's two timestamps, bypassing the code under test.

    :param path: the engram to read
    :returns: its ``created_at`` and ``updated_at``
    """
    row = (
        await Engram.select(Engram.created_at, Engram.updated_at)
        .where(Engram.path == path)
        .first()
    )
    return row["created_at"], row["updated_at"]


async def test_a_new_engram_is_updated_when_it_is_created(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "a.md", "one", message="x")
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
async def test_editing_an_engram_moves_updated_at_and_leaves_created_at(
    tx, household, change
):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "a.md", "one", message="x", title="A", tags=[])
    await _age("a.md")
    await save_engram(me, "personal", "a.md", message="edit", **change)
    created, updated = await _stamps("a.md")
    assert created == LONG_AGO
    assert updated > LONG_AGO


async def test_editing_a_section_moves_updated_at_and_leaves_created_at(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "a.md", "Boiler: old", message="x")
    await _age("a.md")
    await edit_section(me, "personal", "a.md", "old", "new", message="fix")
    created, updated = await _stamps("a.md")
    assert created == LONG_AGO
    assert updated > LONG_AGO


async def test_background_bookkeeping_does_not_move_updated_at(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "a.md", "Boiler: [[b.md]]", message="x")
    await _age("a.md")
    await Engram.raw(
        "UPDATE engrams SET indexed_version = NULL, linked_version = NULL, "
        "reference_targets = NULL, description = NULL, size = NULL"
    )
    assert await backfill_index_fields(me) == 1
    await Engram.raw("UPDATE engrams SET linked_version = NULL")
    assert await backfill_links(me) == 1
    created, updated = await _stamps("a.md")
    assert (created, updated) == (LONG_AGO, LONG_AGO)
