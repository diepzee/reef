"""Protocol and persona delivery: the protocol ships with the product, the
persona is a per-person engram.

The protocol used to be an engram too, seeded at first sign-in — which froze
every person's copy at whatever the template said that day and let product
improvements reach nobody. It now lives in ``protocol.md`` beside this
module, versioned with the code and served fresh on every call.
"""

from pathlib import Path

from reef.access import Principal
from reef.engrams import PERSONA_PATH, get_engram

__all__ = ["PERSONA_PATH", "PERSONA_STUB", "PROTOCOL", "build_instructions"]

PROTOCOL = (Path(__file__).parent / "protocol.md").read_text()

PERSONA_STUB = (
    "# Persona\n\nNot yet written. This is a first meeting: introduce "
    "yourself, ask what the person would like to call you, and interview "
    "gently. Once they agree to the wording, save it with `instruct`."
)


async def build_instructions(principal: Principal) -> str:
    """Assemble the operating protocol plus persona for a principal.

    The protocol is the packaged product text — any ``meta/protocol.md``
    engram left over from the old design is ignored. The persona is an
    ordinary engram (changed through ``instruct``, with revision
    history), read from the caller's own personal space so it is never
    shared between people.

    :param principal: the authenticated person
    :returns: the combined instructions text
    """
    persona = await get_engram(principal, "personal", PERSONA_PATH)
    parts = [PROTOCOL]
    if persona:
        parts.append(persona.body)
    return "\n\n---\n\n".join(parts)
