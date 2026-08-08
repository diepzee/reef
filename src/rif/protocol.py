"""Protocol and persona delivery: both are ordinary pages, read per principal."""

from rif.access import Principal
from rif.pages import get_page

PROTOCOL_PATH = "meta/protocol.md"
PERSONA_PATH = "meta/persona.md"

PROTOCOL_TEMPLATE = (
    "Start every conversation by loading the index; fetch the entries the "
    "conversation needs with read_pages, and fetch again as topics come up. "
    "Compile knowledge into pages rather than dumping transcripts. Make "
    "surgical edits. When a fact changes, supersede it and note the change. "
    "Record facts in the personal space unless they clearly concern one of "
    "your shared spaces — then use that space's name from list_spaces. If "
    "the personal space is empty, this is a first meeting: "
    "introduce yourself, ask what the user would like to call you, and "
    "interview gently to seed meta/persona.md and a first few pages."
)

PERSONA_STUB = (
    "# Persona\n\nNot yet written. This is a first meeting: introduce "
    "yourself, ask what the user would like to call you, and interview "
    "gently to fill this page in."
)


async def build_instructions(principal: Principal) -> str:
    """Assemble the operating protocol plus persona for a principal.

    Both are ordinary pages — editable through update_meta_page, with the same
    revision history as everything else — which is how the protocol reaches a
    phone with no filesystem. The protocol is per-person, so it lives in the
    personal space beside the persona rather than in any shared space.

    :param principal: the authenticated person
    :returns: the combined instructions text
    """
    protocol = await get_page(principal, "personal", PROTOCOL_PATH)
    persona = await get_page(principal, "personal", PERSONA_PATH)
    parts = [protocol.body if protocol else PROTOCOL_TEMPLATE]
    if persona:
        parts.append(persona.body)
    return "\n\n---\n\n".join(parts)
