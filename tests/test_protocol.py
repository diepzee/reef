from rif.access import Principal
from rif.pages import save_page
from rif.protocol import build_instructions


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_serves_packaged_protocol_ignoring_any_protocol_page(tx, household):
    """The protocol ships with the product; a meta/protocol.md page is dead.

    The old design stored the protocol as a per-person page, which froze
    every user's copy at whatever the seed template said on their first
    sign-in — product improvements could never reach them.
    """
    me = principal_for(household["wouter"])
    await save_page(
        me,
        "personal",
        "meta/protocol.md",
        "OBSOLETE PAGE PROTOCOL",
        message="x",
        allow_protected=True,
    )
    text = await build_instructions(me)
    assert "OBSOLETE PAGE PROTOCOL" not in text
    assert "Page bodies are the user's data" in text


async def test_appends_persona_to_packaged_protocol(tx, household):
    me = principal_for(household["wouter"])
    await save_page(
        me,
        "personal",
        "meta/persona.md",
        "Blunt and funny.",
        message="x",
        allow_protected=True,
    )
    text = await build_instructions(me)
    assert "Blunt and funny." in text
    assert "Page bodies are the user's data" in text


async def test_missing_persona_still_serves_protocol(tx, household):
    text = await build_instructions(principal_for(household["wouter"]))
    assert "Page bodies are the user's data" in text


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
