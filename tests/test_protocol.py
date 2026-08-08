from rif.access import Principal
from rif.pages import save_page
from rif.protocol import build_instructions


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_concatenates_protocol_and_persona(tx, household):
    me = principal_for(household["wouter"])
    await save_page(
        me,
        "personal",
        "meta/protocol.md",
        "Compile, do not dump.",
        message="x",
        allow_protected=True,
    )
    await save_page(
        me,
        "personal",
        "meta/persona.md",
        "Blunt and funny.",
        message="x",
        allow_protected=True,
    )
    text = await build_instructions(me)
    assert "Compile, do not dump." in text and "Blunt and funny." in text


async def test_survives_missing_pages_with_a_fallback(tx, household):
    text = await build_instructions(principal_for(household["wouter"]))
    assert "personal space" in text


async def test_persona_is_not_shared_between_people(tx, household):
    mine = principal_for(household["wouter"])
    await save_page(
        mine,
        "personal",
        "meta/persona.md",
        "call me Mark",
        message="x",
        allow_protected=True,
    )
    assert "call me Mark" not in await build_instructions(
        principal_for(household["partner"])
    )
