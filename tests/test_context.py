from reef.access import Principal, arm
from reef.context import build_index, load_context
from reef.db import transaction_scope
from reef.models import Revision
from reef.pages import save_page


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_context_contains_both_coves_with_full_bodies(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "health.md", "sleeps badly", message="x")
    await save_page(me, "household", "house.md", "boiler", message="x")
    payload = await load_context(me, char_budget=100_000)
    assert {s.alias for s in payload.coves} == {"personal", "household"}
    bodies = [p["body"] for s in payload.coves for p in s.pages]
    assert "sleeps badly" in bodies and "boiler" in bodies
    assert payload.truncated is False
    assert payload.page_count == payload.included_count == 2


async def test_context_never_includes_the_other_persons_cove(tx, household):
    mine = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_page(theirs, "personal", "hers.md", "her secret", message="x")
    await save_page(mine, "personal", "mine.md", "my secret", message="x")
    bodies = [
        p["body"]
        for s in (await load_context(mine, char_budget=100_000)).coves
        for p in s.pages
    ]
    assert "my secret" in bodies and "her secret" not in bodies


async def test_truncation_prefers_meta_core_and_small_pages(tx, household):
    me = principal_for(household["wouter"])
    await save_page(
        me, "personal", "meta/persona.md", "persona", message="x", allow_protected=True
    )
    await save_page(
        me,
        "personal",
        "allergy.md",
        "allergic to penicillin",
        message="x",
        tags=["core"],
    )
    await save_page(me, "personal", "diary.md", "x" * 5_000, message="x")
    payload = await load_context(me, char_budget=100)
    included = {
        p["path"] for s in payload.coves for p in s.pages if p["body"] is not None
    }
    omitted = {p["path"] for s in payload.coves for p in s.pages if p["body"] is None}
    assert "meta/persona.md" in included and "allergy.md" in included
    assert "diary.md" in omitted
    assert payload.truncated is True and payload.note is not None
    assert payload.included_count < payload.page_count


async def test_version_reflects_every_cove_write(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "a.md", "one", message="x")
    first = (await load_context(me, char_budget=100_000)).version
    await save_page(me, "household", "h.md", "two", message="x")
    assert (await load_context(me, char_budget=100_000)).version != first


async def test_index_lists_pages_without_bodies(tx, household):
    from reef.context import build_index as load_index

    me = principal_for(household["wouter"])
    await save_page(
        me,
        "personal",
        "health.md",
        "Sleep profile and open questions.\n\nLong detail here.",
        message="x",
        tags=["person"],
    )
    await save_page(me, "household", "house.md", "The family home.", message="x")

    idx = await load_index(me)

    assert {s.alias for s in idx.coves} == {"personal", "household"}
    entry = next(p for s in idx.coves for p in s.pages if p["path"] == "health.md")
    assert entry["title"] == "health"
    assert entry["tags"] == ["person"]
    assert entry["description"] == "Sleep profile and open questions."
    assert entry["size"] > 0 and entry["version"] == 1
    assert "body" not in entry


async def test_index_description_skips_headings(tx, household):
    from reef.context import build_index as load_index

    me = principal_for(household["wouter"])
    await save_page(
        me,
        "personal",
        "a.md",
        "# Heading\n\nThe real summary line.\n\nMore.",
        message="x",
    )
    idx = await load_index(me)
    entry = next(p for s in idx.coves for p in s.pages if p["path"] == "a.md")
    assert entry["description"] == "The real summary line."


async def test_index_resolves_visible_wiki_references(tx, household):
    """Index references are resolved, distinct, and never code examples."""
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "notes.md", "Personal notes.", message="x")
    await save_page(me, "household", "house.md", "The family home.", message="x")
    await save_page(
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
    entry = next(p for s in idx.coves for p in s.pages if p["path"] == "map.md")
    assert entry["references"] == [
        {"cove": "personal", "path": "notes.md"},
        {"cove": "household", "path": "house.md"},
    ]


async def test_index_excludes_the_other_persons_cove(tx, household):
    from reef.context import build_index as load_index

    mine = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_page(theirs, "personal", "hers.md", "her secret", message="x")
    idx = await load_index(mine)
    assert all(p["path"] != "hers.md" for s in idx.coves for p in s.pages)


async def test_index_carries_attachment_descriptions(tx, household):
    from reef.access import resolve_cove
    from reef.context import build_index as load_index
    from reef.models import Attachment, AttachmentStatus

    me = principal_for(household["wouter"])
    shared = await resolve_cove(me, "household")
    await Attachment(
        cove_id=shared.id,
        object_key="k1",
        filename="boiler.png",
        mime="image/png",
        byte_size=9,
        description="the boiler's model plate",
        status=AttachmentStatus.READY.value,
    ).save()

    idx = await load_index(me)
    house = next(s for s in idx.coves if s.alias == "household")
    assert house.attachments == [
        {
            "key": "k1",
            "filename": "boiler.png",
            "mime": "image/png",
            "size": 9,
            "description": "the boiler's model plate",
            "page_path": None,
        }
    ]


async def test_index_rows_carry_last_editor(graph):
    """Each index page row names the newest revision's author."""
    alice = await graph.person("alice@x.com", "Alice")
    bob = await graph.person("bob@x.com", "Bob")
    await graph.personal_cove(alice)
    await graph.shared_cove("team", alice, bob)
    a = Principal(person_id=alice.id, email=alice.email)
    b = Principal(person_id=bob.id, email=bob.email)
    async with transaction_scope():
        await save_page(a, "team", "n.md", "First line.\n", message="one")
    async with transaction_scope():
        await save_page(
            b, "team", "n.md", "Second line.\n", message="two", expected_version=1
        )
    async with transaction_scope():
        payload = await build_index(a)
    team = next(s for s in payload.coves if s.alias == "team")
    page = next(p for p in team.pages if p["path"] == "n.md")
    assert page["last_editor"] == "Bob"


async def test_last_editor_none_when_author_erased(graph):
    """A vanished author row degrades to None, never an error.

    The update runs inside its own transaction and must ``arm`` RLS itself:
    ``set_config``'s binding is transaction-local (see ``reef.db``), so the
    prior ``save_page`` transaction's arming does not carry over here -- an
    unarmed ``Revision.update`` would silently touch zero rows and leave the
    author un-erased, masking the very case this test exists to cover.
    """
    alice = await graph.person("alice@x.com", "Alice")
    await graph.personal_cove(alice)
    a = Principal(person_id=alice.id, email=alice.email)
    async with transaction_scope():
        await save_page(a, "personal", "n.md", "Line.\n", message="one")
    async with transaction_scope():
        await arm(a)
        await Revision.update({Revision.author_id: None}).where(
            Revision.author_id == alice.id
        )
        payload = await build_index(a)
    personal = next(s for s in payload.coves if s.alias == "personal")
    row = next(p for p in personal.pages if p["path"] == "n.md")
    assert row["last_editor"] is None
