from reef.access import Principal, arm
from reef.context import backfill_index_fields, build_index, load_context
from reef.db import transaction_scope
from reef.engrams import get_engram, save_engram
from reef.models import Engram, Revision


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_context_contains_both_spaces_with_full_bodies(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "health.md", "sleeps badly", message="x")
    await save_engram(me, "household", "house.md", "boiler", message="x")
    payload = await load_context(me, char_budget=100_000)
    assert {s.alias for s in payload.spaces} == {"personal", "household"}
    bodies = [p["body"] for s in payload.spaces for p in s.engrams]
    assert "sleeps badly" in bodies and "boiler" in bodies
    assert payload.truncated is False
    assert payload.engram_count == payload.included_count == 2


async def test_context_never_includes_the_other_persons_space(tx, household):
    mine = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_engram(theirs, "personal", "hers.md", "her secret", message="x")
    await save_engram(mine, "personal", "mine.md", "my secret", message="x")
    bodies = [
        p["body"]
        for s in (await load_context(mine, char_budget=100_000)).spaces
        for p in s.engrams
    ]
    assert "my secret" in bodies and "her secret" not in bodies


async def test_truncation_prefers_meta_core_and_small_engrams(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(
        me, "personal", "meta/persona.md", "persona", message="x", allow_protected=True
    )
    await save_engram(
        me,
        "personal",
        "allergy.md",
        "allergic to penicillin",
        message="x",
        tags=["core"],
    )
    await save_engram(me, "personal", "diary.md", "x" * 5_000, message="x")
    payload = await load_context(me, char_budget=100)
    included = {
        p["path"] for s in payload.spaces for p in s.engrams if p["body"] is not None
    }
    omitted = {
        p["path"] for s in payload.spaces for p in s.engrams if p["body"] is None
    }
    assert "meta/persona.md" in included and "allergy.md" in included
    assert "diary.md" in omitted
    assert payload.truncated is True and payload.note is not None
    assert payload.included_count < payload.engram_count


async def test_version_reflects_every_space_write(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "a.md", "one", message="x")
    first = (await load_context(me, char_budget=100_000)).version
    await save_engram(me, "household", "h.md", "two", message="x")
    assert (await load_context(me, char_budget=100_000)).version != first


async def test_index_lists_engrams_without_bodies(tx, household):
    from reef.context import build_index as load_index

    me = principal_for(household["wouter"])
    await save_engram(
        me,
        "personal",
        "health.md",
        "Sleep profile and open questions.\n\nLong detail here.",
        message="x",
        tags=["person"],
    )
    await save_engram(me, "household", "house.md", "The family home.", message="x")

    idx = await load_index(me)

    assert {s.alias for s in idx.spaces} == {"personal", "household"}
    entry = next(p for s in idx.spaces for p in s.engrams if p["path"] == "health.md")
    assert entry["title"] == "health"
    assert entry["tags"] == ["person"]
    assert entry["description"] == "Sleep profile and open questions."
    assert entry["size"] > 0 and entry["version"] == 1
    assert "body" not in entry


async def test_index_description_skips_headings(tx, household):
    from reef.context import build_index as load_index

    me = principal_for(household["wouter"])
    await save_engram(
        me,
        "personal",
        "a.md",
        "# Heading\n\nThe real summary line.\n\nMore.",
        message="x",
    )
    idx = await load_index(me)
    entry = next(p for s in idx.spaces for p in s.engrams if p["path"] == "a.md")
    assert entry["description"] == "The real summary line."


async def test_index_resolves_visible_wiki_references(tx, household):
    """Index references are resolved, distinct, and never code examples."""
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "notes.md", "Personal notes.", message="x")
    await save_engram(me, "household", "house.md", "The family home.", message="x")
    await save_engram(
        me,
        "personal",
        "map.md",
        """A map linking [[notes.md]] and [[household:house.md]].

The same target again: [[household:house.md|home]].
An absent target: [[household:missing.md]].
An inline example: `[[household:house.md]]`.

```md
[[household:house.md]]
```
""",
        message="x",
    )

    idx = await build_index(me)
    entry = next(p for s in idx.spaces for p in s.engrams if p["path"] == "map.md")
    assert entry["references"] == [
        {"space": "personal", "path": "notes.md"},
        {"space": "household", "path": "house.md"},
    ]


async def test_index_excludes_the_other_persons_space(tx, household):
    from reef.context import build_index as load_index

    mine = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_engram(theirs, "personal", "hers.md", "her secret", message="x")
    idx = await load_index(mine)
    assert all(p["path"] != "hers.md" for s in idx.spaces for p in s.engrams)


async def test_index_carries_attachment_descriptions(tx, household):
    from reef.access import resolve_space
    from reef.context import build_index as load_index
    from reef.models import Attachment, AttachmentStatus

    me = principal_for(household["wouter"])
    shared = await resolve_space(me, "household")
    await Attachment(
        space_id=shared.id,
        object_key="k1",
        filename="boiler.png",
        mime="image/png",
        byte_size=9,
        description="the boiler's model plate",
        status=AttachmentStatus.READY.value,
    ).save()

    idx = await load_index(me)
    house = next(s for s in idx.spaces if s.alias == "household")
    assert house.attachments == [
        {
            "key": "k1",
            "filename": "boiler.png",
            "mime": "image/png",
            "size": 9,
            "description": "the boiler's model plate",
            "engram_path": None,
        }
    ]


async def test_index_rows_carry_last_editor(graph):
    """Each index engram row names the newest revision's author."""
    alice = await graph.person("alice@x.com", "Alice")
    bob = await graph.person("bob@x.com", "Bob")
    await graph.personal_space(alice)
    await graph.shared_space("team", alice, bob)
    a = Principal(person_id=alice.id, email=alice.email)
    b = Principal(person_id=bob.id, email=bob.email)
    async with transaction_scope():
        await save_engram(a, "team", "n.md", "First line.\n", message="one")
    async with transaction_scope():
        await save_engram(
            b, "team", "n.md", "Second line.\n", message="two", expected_version=1
        )
    async with transaction_scope():
        payload = await build_index(a)
    team = next(s for s in payload.spaces if s.alias == "team")
    engram = next(p for p in team.engrams if p["path"] == "n.md")
    assert engram["last_editor"] == "Bob"


async def test_last_editor_none_when_author_erased(graph):
    """A vanished author row degrades to None, never an error.

    The update runs inside its own transaction and must ``arm`` RLS itself:
    ``set_config``'s binding is transaction-local (see ``reef.db``), so the
    prior ``save_engram`` transaction's arming does not carry over here -- an
    unarmed ``Revision.update`` would silently touch zero rows and leave the
    author un-erased, masking the very case this test exists to cover.
    """
    alice = await graph.person("alice@x.com", "Alice")
    await graph.personal_space(alice)
    a = Principal(person_id=alice.id, email=alice.email)
    async with transaction_scope():
        await save_engram(a, "personal", "n.md", "Line.\n", message="one")
    async with transaction_scope():
        await arm(a)
        await Revision.update({Revision.author_id: None}).where(
            Revision.author_id == alice.id
        )
        payload = await build_index(a)
    personal = next(s for s in payload.spaces if s.alias == "personal")
    row = next(p for p in personal.engrams if p["path"] == "n.md")
    assert row["last_editor"] is None


# --- stored index fields -------------------------------------------------------

_BODY = (
    "# House\n\nThe boiler is a Vaillant.\n\n"
    "See [[garden.md]] and [[household:roof.md|the roof]].\n"
)


def _entry(index, alias: str, path: str) -> dict:
    """Return one engram's index entry.

    :param index: an index payload
    :param alias: the space's alias for the reader
    :param path: the engram path
    :returns: the entry's fields
    """
    space = next(space for space in index.spaces if space.alias == alias)
    return next(engram for engram in space.engrams if engram["path"] == path)


async def _forget_index_fields(path: str) -> None:
    """Make an engram look as it did before the index fields existed.

    :param path: the engram to strip
    """
    await Engram.raw(
        "UPDATE engrams SET description = NULL, size = NULL, "
        "reference_targets = NULL, indexed_version = NULL WHERE path = {}",
        path,
    )


async def test_saving_an_engram_stores_its_index_fields(tx, household):
    me = principal_for(household["wouter"])
    engram = await save_engram(me, "personal", "house.md", _BODY, message="x")
    assert engram.description == "The boiler is a Vaillant."
    assert engram.size == len(_BODY)
    assert engram.reference_targets == ["garden.md", "household:roof.md"]
    assert engram.indexed_version == engram.version == 1


async def test_saving_again_refreshes_the_index_fields(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "house.md", _BODY, message="x")
    engram = await save_engram(me, "personal", "house.md", "Sold.", message="x")
    assert engram.description == "Sold."
    assert engram.size == 5
    assert engram.reference_targets == []
    assert engram.indexed_version == engram.version == 2


async def test_the_index_reads_stored_fields_rather_than_the_body(tx, household):
    """Proof by contradiction: change the body behind the fields' back.

    No writer does this. It shows which of the two the index trusts while
    ``indexed_version`` still equals ``version``.
    """
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "house.md", _BODY, message="x")
    await Engram.raw("UPDATE engrams SET body = 'Rewritten.' WHERE path = 'house.md'")
    entry = _entry(await build_index(me), "personal", "house.md")
    assert entry["description"] == "The boiler is a Vaillant."
    assert entry["size"] == len(_BODY)


async def test_an_engram_from_before_the_fields_existed_indexes_the_same(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "garden.md", "Roses.", message="x")
    await save_engram(me, "household", "roof.md", "Slate.", message="x")
    await save_engram(me, "personal", "house.md", _BODY, message="x")
    stored = _entry(await build_index(me), "personal", "house.md")
    await _forget_index_fields("house.md")
    derived = _entry(await build_index(me), "personal", "house.md")
    assert derived == stored
    assert derived["references"] == [
        {"space": "personal", "path": "garden.md"},
        {"space": "household", "path": "roof.md"},
    ]


async def test_a_writer_that_forgets_the_fields_cannot_leave_them_stale(tx, household):
    """The version check is the guard: fields for an older version are unused."""
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "house.md", _BODY, message="x")
    await Engram.raw(
        "UPDATE engrams SET body = 'Rewritten.', version = version + 1 "
        "WHERE path = 'house.md'"
    )
    entry = _entry(await build_index(me), "personal", "house.md")
    assert entry["description"] == "Rewritten."
    assert entry["size"] == len("Rewritten.")


async def test_backfill_stores_fields_on_older_engrams_and_changes_nothing_else(
    tx, household
):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "house.md", _BODY, message="x")
    await save_engram(me, "personal", "garden.md", "Roses.", message="x")
    before = await build_index(me)
    await _forget_index_fields("house.md")

    assert await backfill_index_fields(me) == 1
    assert await backfill_index_fields(me) == 0

    engram = await get_engram(me, "personal", "house.md")
    assert engram.description == "The boiler is a Vaillant."
    assert engram.indexed_version == engram.version == 1
    assert await build_index(me) == before
    assert await Revision.count().where(Revision.engram_id == engram.id) == 1


async def test_backfill_by_a_viewer_updates_nothing_and_does_not_fail(
    tx, household, graph
):
    """Row security decides: an update to a read-only engram matches no row."""
    owner = principal_for(household["wouter"])
    await save_engram(owner, "household", "house.md", _BODY, message="x")
    await _forget_index_fields("house.md")
    person = await graph.person("viewer@example.test", "Viewer")
    await graph.personal_space(person, slug="viewer")
    await graph.add_membership(person, household["shared"], "viewer", alias="household")
    viewer = principal_for(person)

    await arm(viewer)
    assert await backfill_index_fields(viewer) == 0
    entry = _entry(await build_index(viewer), "household", "house.md")
    assert entry["description"] == "The boiler is a Vaillant."
