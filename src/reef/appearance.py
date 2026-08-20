"""How each person has chosen to see each cove.

A cove's colour and creature are derived from its alias (see the frontend's
``coveColor`` and ``organismFor``), which means every cove has a usable look
from the moment it exists and nobody has to choose one. This module stores the
overrides, per viewer, for the people who want to.

The stored value is a *name from a fixed set*, never a hex colour or an
arbitrary shape. The palette and the body plans are a designed system; letting
a person put ``#ff00ff`` on a cove would let one bad choice make the whole
sidebar unreadable, and there would be nothing to fall back to when the
palette is revised. A name can always be re-pointed.
"""

from uuid import UUID

from reef.access import Principal, alias_map, arm
from reef.models import CoveAppearance

#: Palette entries a person may pick, mirroring the frontend's
#: ``coveColor`` palette. ``seafoam`` is the personal cove's pinned hue,
#: offered to every cove because somebody will want it.
COLORS = (
    "seafoam",
    "amber",
    "indigo",
    "pink",
    "sky",
    "lime",
    "violet",
    "orange",
)

#: Body plans a person may pick, mirroring the frontend's
#: ``LIVING_FAMILIES``. The retired plans are deliberately absent: they are
#: still grown for aliases that hash to them, but they are not offered as a
#: choice, because they do not sit at the same visual weight as the rest.
GLYPHS = (
    "sunAnemone",
    "tubes",
    "staghorn",
    "flower",
    "scallop",
    "spiral",
    "bubbles",
    "seagrass",
)


class AppearanceError(Exception):
    """Raised when a requested look is not one of the offered choices."""


async def get_appearances(principal: Principal) -> dict[str, dict[str, str | None]]:
    """Return this person's chosen looks, keyed by cove alias.

    Keyed by alias rather than slug because the alias is what every caller
    already holds: a personal cove's slug is ``personal-<hex>`` and is never
    seen outside the database, so keying by it would leave the one cove
    everybody has permanently unmatched.

    Only coves they can still see are included: a row can outlive access
    (see :func:`reef.rls.appearance_statements`), and reporting a cove the
    caller cannot otherwise resolve would be a small disclosure for no gain.

    :param principal: the authenticated person
    :returns: alias to ``{"color": ..., "glyph": ...}``, absent when unset
    """
    await arm(principal)
    rows = await CoveAppearance.objects().where(
        CoveAppearance.person_id == principal.person_id
    )
    if not rows:
        return {}
    # Cove names live on the membership, so this reader's own names are the
    # only correct ones -- and a cove they have left has no name for them at
    # all, which the comprehension below drops.
    aliases = await alias_map(principal)
    return {
        aliases[row.cove_id]: {"color": row.color, "glyph": row.glyph}
        for row in rows
        if row.cove_id in aliases
    }


async def set_appearance(
    principal: Principal,
    cove_id: UUID,
    *,
    color: str | None,
    glyph: str | None,
) -> dict[str, str | None]:
    """Record how this person wants to see one cove.

    Both fields are absolute rather than partial: ``None`` means "go back to
    the colour the alias gives it", which is the only way to undo a choice.

    :param principal: the authenticated person
    :param cove_id: the cove being restyled, already resolved for them
    :param color: a name from :data:`COLORS`, or None to derive it
    :param glyph: a name from :data:`GLYPHS`, or None to derive it
    :raises AppearanceError: if either name is not on offer
    :returns: the stored choice
    """
    if color is not None and color not in COLORS:
        raise AppearanceError(f"{color!r} is not one of {', '.join(COLORS)}")
    if glyph is not None and glyph not in GLYPHS:
        raise AppearanceError(f"{glyph!r} is not one of {', '.join(GLYPHS)}")
    await arm(principal)
    existing = (
        await CoveAppearance.objects()
        .where(
            CoveAppearance.person_id == principal.person_id,
            CoveAppearance.cove_id == cove_id,
        )
        .first()
    )
    if color is None and glyph is None:
        # Nothing chosen is the absence of a row, not a row of nulls, so
        # "reset to default" leaves no residue behind.
        if existing is not None:
            await CoveAppearance.delete().where(CoveAppearance.id == existing.id)
        return {"color": None, "glyph": None}
    if existing is None:
        await CoveAppearance(
            person_id=principal.person_id,
            cove_id=cove_id,
            color=color,
            glyph=glyph,
        ).save()
    else:
        await CoveAppearance.update(
            {CoveAppearance.color: color, CoveAppearance.glyph: glyph}
        ).where(CoveAppearance.id == existing.id)
    return {"color": color, "glyph": glyph}
