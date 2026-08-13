"""How each person has chosen to see each cove.

A cove's colour and creature are derived from its alias (see the frontend's
``spaceColor`` and ``organismFor``), which means every cove has a usable look
from the moment it exists and nobody has to choose one. This module stores the
overrides, per viewer, for the people who want to.

The stored value is a *name from a fixed set*, never a hex colour or an
arbitrary shape. The palette and the body plans are a designed system; letting
a person put ``#ff00ff`` on a cove would let one bad choice make the whole
sidebar unreadable, and there would be nothing to fall back to when the
palette is revised. A name can always be re-pointed.
"""

from uuid import UUID

from rif.access import Principal, arm, space_alias
from rif.models import Space, SpaceAppearance

#: Palette entries a person may pick, mirroring the frontend's
#: ``spaceColor`` palette. ``seafoam`` is the personal cove's pinned hue,
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
    (see :func:`rif.rls.appearance_statements`), and reporting a cove the
    caller cannot otherwise resolve would be a small disclosure for no gain.

    :param principal: the authenticated person
    :returns: alias to ``{"color": ..., "glyph": ...}``, absent when unset
    """
    await arm(principal)
    rows = await SpaceAppearance.objects().where(
        SpaceAppearance.person_id == principal.person_id
    )
    if not rows:
        return {}
    aliases = {
        space.id: space_alias(space)
        for space in await Space.objects().where(
            Space.id.is_in([row.space_id for row in rows])
        )
    }
    return {
        aliases[row.space_id]: {"color": row.color, "glyph": row.glyph}
        for row in rows
        if row.space_id in aliases
    }


async def set_appearance(
    principal: Principal,
    space_id: UUID,
    *,
    color: str | None,
    glyph: str | None,
) -> dict[str, str | None]:
    """Record how this person wants to see one cove.

    Both fields are absolute rather than partial: ``None`` means "go back to
    the colour the alias gives it", which is the only way to undo a choice.

    :param principal: the authenticated person
    :param space_id: the cove being restyled, already resolved for them
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
        await SpaceAppearance.objects()
        .where(
            SpaceAppearance.person_id == principal.person_id,
            SpaceAppearance.space_id == space_id,
        )
        .first()
    )
    if color is None and glyph is None:
        # Nothing chosen is the absence of a row, not a row of nulls, so
        # "reset to default" leaves no residue behind.
        if existing is not None:
            await SpaceAppearance.delete().where(SpaceAppearance.id == existing.id)
        return {"color": None, "glyph": None}
    if existing is None:
        await SpaceAppearance(
            person_id=principal.person_id,
            space_id=space_id,
            color=color,
            glyph=glyph,
        ).save()
    else:
        await SpaceAppearance.update(
            {SpaceAppearance.color: color, SpaceAppearance.glyph: glyph}
        ).where(SpaceAppearance.id == existing.id)
    return {"color": color, "glyph": glyph}
