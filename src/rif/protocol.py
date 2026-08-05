from sqlalchemy.ext.asyncio import AsyncSession

from rif.access import Principal
from rif.pages import get_page

PROTOCOL_PATH = "meta/protocol.md"
PERSONA_PATH = "meta/persona.md"

_FALLBACK = (
    "Load all context at the start of every conversation. Compile knowledge "
    "into pages rather than dumping transcripts. Make surgical edits. When a "
    "fact changes, supersede it and note the change. Record facts in the "
    "personal space unless they clearly concern the household. If the "
    "personal space is empty, this is a first meeting: introduce yourself, "
    "ask what the user would like to call you, and interview gently to seed "
    "meta/persona.md and a first few pages."
)


async def build_instructions(session: AsyncSession, principal: Principal) -> str:
    """Assemble the operating protocol plus persona for a principal.

    Both are ordinary pages — editable through update_meta_page, with the same
    revision history as everything else — which is how the protocol reaches a
    phone with no filesystem.

    :param session: database session
    :param principal: the authenticated person
    :returns: the combined instructions text
    """
    protocol = await get_page(session, principal, "household", PROTOCOL_PATH)
    persona = await get_page(session, principal, "personal", PERSONA_PATH)
    parts = [protocol.body if protocol else _FALLBACK]
    if persona:
        parts.append(persona.body)
    return "\n\n---\n\n".join(parts)
