"""Share and instruct: the two staged methods that reach beyond one engram.

- :func:`share` moves a personal engram, or one section of it, into a shared
  space. Every member of that space can then read it for good, so it takes
  two calls: the first describes the effect and gives the promotion's nonce
  as the token; the second brings it back with the same arguments.
- :func:`instruct` replaces the person's persona, which steers every
  assistant they use. Two calls, too: the token carries a digest of the new
  text, so only that text can be written.

**The persona is personal.** :func:`write_persona` is the one sanctioned
bypass of the ``meta/`` write guard, and it only ever writes the caller's own
persona. ``build_instructions`` reads the persona from the caller's personal
space alone, so a ``meta/`` engram in a shared space would steer nobody,
while the context loader ranks ``meta/`` first and would put
instruction-shaped text at the top of every other member's context.
:func:`instruct` has no space argument for that reason.

**Privacy.** Nothing the caller sent -- a body, a section, a path, a reason
-- is logged. The effect names paths and people, never text.
"""

from hashlib import sha256
from uuid import UUID

from reef.access import (
    PERSONAL_ALIAS,
    Principal,
    resolve_space,
    resolve_writable_space,
)
from reef.config import get_settings
from reef.db import transaction_scope
from reef.engrams import PERSONA_PATH, get_engram, save_engram
from reef.methods import Caller, MethodError, Writer, method, staged
from reef.methods.refusals import refusals
from reef.models import Engram, Promotion
from reef.promotion import confirm_promotion, prepare_promotion

#: The action the staged token of :func:`instruct` is bound to.
INSTRUCT = "instruct"


async def write_persona(
    principal: Principal,
    body: str,
    message: str,
    *,
    channel: Writer = "assistant",
) -> Engram:
    """Write the persona engram in the person's personal space.

    The only caller of :func:`reef.engrams.save_engram` that sets
    ``allow_protected``. It has no space argument: the persona lives in the
    personal space and nowhere else.

    :param principal: the person
    :param body: the full new persona
    :param message: why, kept in history
    :param channel: who made the change, kept on the revision
    :returns: the engram as saved
    :raises reef.engrams.EngramTooLarge: when the body is over the limit
    """
    return await save_engram(
        principal,
        PERSONAL_ALIAS,
        PERSONA_PATH,
        body,
        message=message,
        allow_protected=True,
        channel=channel,
    )


def _digest(body: str) -> str:
    """Fingerprint a persona text.

    :param body: the text
    :returns: its SHA-256, as hex
    """
    return sha256(body.encode()).hexdigest()


@method("instruct")
async def instruct(
    caller: Caller, reason: str, body: str, confirm: str | None = None
) -> dict:
    """Replace the person's persona: how their assistants work with them.

    Two calls. The first changes nothing: it says what would change and gives
    a token. Show the person the new text and the effect, and wait for a yes.
    The second call brings the token back as ``confirm`` with the same
    ``body``; a different body is refused. The persona is personal: there is
    no space to choose.

    :param caller: who is calling
    :param reason: why, in the caller's words
    :param body: the full new persona
    :param confirm: the token from the first call
    :returns: without ``confirm``, ``effect`` (plain text), ``token`` and
        ``expires_in``; with it, ``path`` and ``version``
    :raises MethodError: ``engram_too_large``, ``stale_token`` or
        ``not_configured``
    """
    if len(body) > get_settings().engrams.max_chars:
        raise MethodError("engram_too_large")
    digest = _digest(body)
    async with transaction_scope():
        with refusals():
            await resolve_space(caller.principal, PERSONAL_ALIAS)
        current = await get_engram(caller.principal, PERSONAL_ALIAS, PERSONA_PATH)
    if confirm is None:
        old = len(current.body) if current else 0
        effect = (
            "Every assistant this person uses will follow these instructions "
            "from now on, in place of the current ones. The persona goes "
            f"from {old:,} to {len(body):,} characters."
        )
        return {"effect": effect, **staged.stage(caller, INSTRUCT, {"digest": digest})}
    subject = staged.take(caller, INSTRUCT, confirm)
    if subject.get("digest") != digest:
        raise MethodError("stale_token")
    if current is not None and current.body == body:
        return {"path": current.path, "version": current.version}
    with refusals():
        async with transaction_scope():
            saved = await write_persona(
                caller.principal,
                body,
                "Persona changed with instruct",
                channel=caller.writer,
            )
    return {"path": saved.path, "version": saved.version}


@method("share")
async def share(
    caller: Caller,
    reason: str,
    engram: str,
    to_space: str,
    section: str | None = None,
    to_path: str | None = None,
    confirm: str | None = None,
) -> dict:
    """Move a personal engram, or one section of it, into a shared space.

    Two calls. The first changes nothing: it says who will be able to read
    the text, and gives a token. Show the person the effect and wait for a
    yes. The second call brings the token back as ``confirm``, with the
    same engram, section, space and path. The private original is replaced
    by a pointer to the shared copy, so nothing private exists twice.
    Sharing cannot be undone. A token works once.

    :param caller: who is calling
    :param reason: why, in the caller's words
    :param engram: the path of the personal engram
    :param to_space: the caller's name for the shared space
    :param section: the exact text to share; the whole engram when None
    :param to_path: the path of the shared engram; ``engram`` when None, and
        required with ``section``
    :param confirm: the token from the first call
    :returns: without ``confirm``, ``effect`` (plain text), ``token`` and
        ``expires_in``; with it, ``shared`` (``space/path``)
    :raises MethodError: ``share_to_personal``, ``unknown_space``,
        ``read_only``, ``not_found``, ``share_needs_path``,
        ``passage_not_found``, ``passage_ambiguous``,
        ``share_target_exists``, ``stale_token`` or a refusal to write
    """
    if confirm is None:
        return await _stage_share(caller, engram, to_space, section, to_path)
    return await _carry_out_share(caller, engram, to_space, section, to_path, confirm)


async def _stage_share(
    caller: Caller,
    engram: str,
    to_space: str,
    section: str | None,
    to_path: str | None,
) -> dict:
    """Stage a share and describe it.

    :param caller: who is calling
    :param engram: the personal engram's path
    :param to_space: the caller's name for the shared space
    :param section: the exact text to share, or None
    :param to_path: the shared engram's path, or None
    :returns: ``effect``, ``token`` (the promotion's nonce) and ``expires_in``
    :raises MethodError: as :func:`share` does
    """
    with refusals():
        async with transaction_scope():
            staged_share = await prepare_promotion(
                caller.principal, engram, to_space, section=section, dest_path=to_path
            )
    what = f"the section you named of {engram}" if section else f"the engram {engram}"
    people = ", ".join(staged_share["members"])
    effect = (
        f"This copies {what} into the shared space {to_space} as "
        f"{staged_share['dest_path']}. Everyone in that space can read it, "
        f"now and in future: {people}. This cannot be undone. "
        + (
            f"The section in {engram} is replaced by a pointer to the shared copy."
            if section
            else "The private original is replaced by a pointer to the shared copy."
        )
    )
    return {
        "effect": effect,
        "token": staged_share["nonce"],
        "expires_in": int(staged.TOKEN_TTL_SECONDS),
    }


async def _carry_out_share(
    caller: Caller,
    engram: str,
    to_space: str,
    section: str | None,
    to_path: str | None,
    token: str,
) -> dict:
    """Confirm a staged share, if it was staged for exactly these arguments.

    :param caller: who is calling
    :param engram: the personal engram's path
    :param to_space: the caller's name for the shared space
    :param section: the exact text to share, or None
    :param to_path: the shared engram's path, or None
    :param token: the nonce from the first call
    :returns: ``shared``, the shared engram as ``space/path``
    :raises MethodError: ``stale_token`` when the token is unknown, used,
        or was staged for other arguments; the rest as :func:`share`
    """
    with refusals():
        async with transaction_scope():
            await resolve_space(caller.principal, PERSONAL_ALIAS)
            try:
                row = (
                    await Promotion.objects().where(Promotion.id == UUID(token)).first()
                )
            except ValueError:
                row = None
            if (
                row is None
                or row.person_id != caller.principal.person_id
                or row.consumed_at is not None
            ):
                raise MethodError("stale_token")
            source = (
                await Engram.objects().where(Engram.id == row.source_engram_id).first()
            )
            dest = await resolve_writable_space(caller.principal, to_space)
            if (
                source is None
                or source.path != engram
                or row.section_text != section
                or row.dest_space_id != dest.id
                or row.dest_path != (to_path or engram)
            ):
                raise MethodError("stale_token")
            done = await confirm_promotion(caller.principal, token)
    return {"shared": f"{done['dest_cove']}/{done['dest_path']}"}
