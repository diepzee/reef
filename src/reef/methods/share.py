"""Share and instruct: the two staged methods that reach beyond one engram.

- :func:`share` moves a personal engram, or one section of it, into a shared
  space. Every member of that space can then read it for good, so it takes
  two calls: the first returns the exact text, describes the effect and
  gives the promotion's nonce as the token; the second brings it back with
  the same arguments.
- :func:`instruct` replaces the person's persona, which steers every
  assistant they use. Two calls, too: the token carries a digest of the new
  text and the persona's version at the first call, so only that text can
  be written, and only over the persona the person saw.

**The persona is personal.** :func:`write_persona` is the one sanctioned
bypass of the ``meta/`` write guard, and it only ever writes the caller's own
persona. ``build_instructions`` reads the persona from the caller's personal
space alone, so a ``meta/`` engram in a shared space would steer nobody,
while the context loader ranks ``meta/`` first and would put
instruction-shaped text at the top of every other member's context.
:func:`instruct` has no space argument for that reason.

**Privacy.** Nothing the caller sent -- a body, a section, a path, a reason
-- is logged. The effect names paths and people, never text. Only
:func:`share`'s first call returns engram text, to the caller who owns it,
so the person sees what others will read.
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
from reef.engrams import PERSONA_PATH, VersionConflict, get_engram, save_engram
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
    expected_version: int | None = None,
) -> Engram:
    """Write the persona engram in the person's personal space.

    The only caller of :func:`reef.engrams.save_engram` that sets
    ``allow_protected``. It has no space argument: the persona lives in the
    personal space and nowhere else.

    :param principal: the person
    :param body: the full new persona
    :param message: why, kept in history
    :param channel: who made the change, kept on the revision
    :param expected_version: the version the persona must be at, 0 for none
        yet; None writes whatever it is at
    :returns: the engram as saved
    :raises reef.engrams.EngramTooLarge: when the body is over the limit
    :raises reef.engrams.VersionConflict: when the persona is at another
        version than ``expected_version``
    """
    return await save_engram(
        principal,
        PERSONAL_ALIAS,
        PERSONA_PATH,
        body,
        message=message,
        allow_protected=True,
        channel=channel,
        expected_version=expected_version,
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
    ``body``; a different body is refused. So is a token for a persona that
    changed after the first call, for instance on the website: the person
    agreed to replace the persona they saw, not a later one. A replay of a
    write that already landed writes nothing. The persona is personal: there
    is no space to choose.

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
    version = current.version if current else 0
    if confirm is None:
        old = len(current.body) if current else 0
        effect = (
            "Every assistant this person uses will follow these instructions "
            "from now on, in place of the current ones. The persona goes "
            f"from {old:,} to {len(body):,} characters."
        )
        subject = {"digest": digest, "version": version}
        return {"effect": effect, **staged.stage(caller, INSTRUCT, subject)}
    subject = staged.take(caller, INSTRUCT, confirm)
    if subject.get("digest") != digest:
        raise MethodError("stale_token")
    if current is not None and current.body == body:
        return {"path": current.path, "version": current.version}
    if subject.get("version") != version:
        raise MethodError("stale_token")
    with refusals():
        async with transaction_scope():
            try:
                saved = await write_persona(
                    caller.principal,
                    body,
                    "Persona changed with instruct",
                    channel=caller.writer,
                    expected_version=version,
                )
            except VersionConflict:
                # The persona changed after it was read above: refused as
                # the version check above refuses it.
                raise MethodError("stale_token") from None
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

    Two calls. The first changes nothing: it returns the exact text that
    would be shared, says who will be able to read it, and gives a token.
    Show the person the text and the effect, and wait for a yes. The second
    call brings the token back as ``confirm``, with the same engram,
    section, space and path. The private original is replaced
    by a pointer to the shared copy, so nothing private exists twice.
    Sharing cannot be undone. Repeating the second call with the same token
    and arguments moves nothing again and answers ``already_done``.

    :param caller: who is calling
    :param reason: why, in the caller's words
    :param engram: the path of the personal engram
    :param to_space: the caller's name for the shared space
    :param section: the exact text to share; the whole engram when None
    :param to_path: the path of the shared engram; ``engram`` when None, and
        required with ``section``
    :param confirm: the token from the first call
    :returns: without ``confirm``, ``text`` (exactly what would be shared:
        the whole body, or the section), ``effect`` (plain text), ``token``
        and ``expires_in``; with it, ``shared`` (``space/path``) and
        ``already_done``
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
    :returns: ``text``, ``effect``, ``token`` (the promotion's nonce) and
        ``expires_in``
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
        f"{staged_share['dest_path']}. Everyone in that space can read it: "
        f"{people}, and anyone invited later. This cannot be undone. "
        + (
            f"The section in {engram} is replaced by a pointer to the shared copy."
            if section
            else "The private original is replaced by a pointer to the shared copy."
        )
    )
    return {
        "text": staged_share["disclosure"],
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
    :returns: ``shared``, the shared engram as ``space/path``, and
        ``already_done``, true when this token had already been carried out
    :raises MethodError: ``stale_token`` when the token is unknown or was staged for other arguments; the rest as :func:`share`
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
            if row is None or row.person_id != caller.principal.person_id:
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
            done = await confirm_promotion(
                caller.principal, token, channel=caller.writer
            )
    return {
        "shared": f"{done['dest_cove']}/{done['dest_path']}",
        "already_done": done["already_done"],
    }
