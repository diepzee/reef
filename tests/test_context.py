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
