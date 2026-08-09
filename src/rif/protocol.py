"""Protocol and persona delivery: the protocol ships with the product, the
persona is a per-person page.

The protocol used to be a page too, seeded at first sign-in — which froze
every person's copy at whatever the template said that day and let product
improvements reach nobody. It now lives in ``protocol.md`` beside this
module, versioned with the code and served fresh on every call.
"""

from pathlib import Path

from rif.access import Principal
from rif.pages import get_page

PERSONA_PATH = "meta/persona.md"

PROTOCOL = (Path(__file__).parent / "protocol.md").read_text()

PERSONA_STUB = (
    "# Persona\n\nNot yet written. This is a first meeting: introduce "
    "yourself, ask what the user would like to call you, and interview "
    "gently to fill this page in."
)


async def build_instructions(principal: Principal) -> str:
    """Assemble the operating protocol plus persona for a principal.

    The protocol is the packaged product text — any ``meta/protocol.md``
    page left over from the old design is ignored. The persona is an
    ordinary page (editable through ``update_meta_page``, with revision
    history), read from the caller's own personal space so it is never
    shared between people.

    :param principal: the authenticated person
    :returns: the combined instructions text
    """
    persona = await get_page(principal, "personal", PERSONA_PATH)
    parts = [PROTOCOL]
    if persona:
        parts.append(persona.body)
    return "\n\n---\n\n".join(parts)
