from rif.access import Principal
from rif.context import load_context
from rif.pages import save_page


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_context_contains_both_spaces_with_full_bodies(session, household):
    me = principal_for(household["wouter"])
    await save_page(session, me, "personal", "health.md", "sleeps badly", message="x")
    await save_page(session, me, "household", "house.md", "boiler", message="x")
    payload = await load_context(session, me, char_budget=100_000)
    assert {s.alias for s in payload.spaces} == {"personal", "household"}
    bodies = [p["body"] for s in payload.spaces for p in s.pages]
    assert "sleeps badly" in bodies and "boiler" in bodies
    assert payload.truncated is False
    assert payload.page_count == payload.included_count == 2


async def test_context_never_includes_the_other_persons_space(session, household):
    mine = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_page(session, theirs, "personal", "hers.md", "her secret", message="x")
    await save_page(session, mine, "personal", "mine.md", "my secret", message="x")
    bodies = [p["body"] for s in (await load_context(session, mine, char_budget=100_000)).spaces
              for p in s.pages]
    assert "my secret" in bodies and "her secret" not in bodies


async def test_truncation_prefers_meta_core_and_small_pages(session, household):
    me = principal_for(household["wouter"])
    await save_page(session, me, "personal", "meta/persona.md", "persona",
                    message="x", allow_protected=True)
    await save_page(session, me, "personal", "allergy.md", "allergic to penicillin",
                    message="x", tags=["core"])
    await save_page(session, me, "personal", "diary.md", "x" * 5_000, message="x")
    payload = await load_context(session, me, char_budget=100)
    included = {p["path"] for s in payload.spaces for p in s.pages if p["body"] is not None}
    omitted = {p["path"] for s in payload.spaces for p in s.pages if p["body"] is None}
    assert "meta/persona.md" in included and "allergy.md" in included
    assert "diary.md" in omitted
    assert payload.truncated is True and payload.note is not None
    assert payload.included_count < payload.page_count


async def test_version_reflects_every_space_write(session, household):
    me = principal_for(household["wouter"])
    await save_page(session, me, "personal", "a.md", "one", message="x")
    first = (await load_context(session, me, char_budget=100_000)).version
    await save_page(session, me, "household", "h.md", "two", message="x")
    assert (await load_context(session, me, char_budget=100_000)).version != first


async def test_index_lists_pages_without_bodies(session, household):
    from rif.context import build_index as load_index

    me = principal_for(household["wouter"])
    await save_page(session, me, "personal", "health.md",
                    "Sleep profile and open questions.\n\nLong detail here.",
                    message="x", tags=["person"])
    await save_page(session, me, "household", "house.md", "The family home.",
                    message="x")

    idx = await load_index(session, me)

    assert {s.alias for s in idx.spaces} == {"personal", "household"}
    entry = next(p for s in idx.spaces for p in s.pages if p["path"] == "health.md")
    assert entry["title"] == "health"
    assert entry["tags"] == ["person"]
    assert entry["description"] == "Sleep profile and open questions."
    assert entry["size"] > 0 and entry["version"] == 1
    assert "body" not in entry


async def test_index_description_skips_headings(session, household):
    from rif.context import build_index as load_index

    me = principal_for(household["wouter"])
    await save_page(session, me, "personal", "a.md",
                    "# Heading\n\nThe real summary line.\n\nMore.", message="x")
    idx = await load_index(session, me)
    entry = next(p for s in idx.spaces for p in s.pages if p["path"] == "a.md")
    assert entry["description"] == "The real summary line."


async def test_index_excludes_the_other_persons_space(session, household):
    from rif.context import build_index as load_index

    mine = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_page(session, theirs, "personal", "hers.md", "her secret",
                    message="x")
    idx = await load_index(session, mine)
    assert all(p["path"] != "hers.md" for s in idx.spaces for p in s.pages)


async def test_index_carries_attachment_descriptions(session, household):
    from rif.access import resolve_space
    from rif.context import build_index as load_index
    from rif.models import Attachment, AttachmentStatus

    me = principal_for(household["wouter"])
    shared = await resolve_space(session, me, "household")
    session.add(Attachment(space_id=shared.id, object_key="k1", mime="image/png",
                           byte_size=9, description="the boiler's model plate",
                           status=AttachmentStatus.READY))
    await session.flush()

    idx = await load_index(session, me)
    house = next(s for s in idx.spaces if s.alias == "household")
    assert house.attachments == [{"key": "k1", "mime": "image/png",
                                  "description": "the boiler's model plate"}]
