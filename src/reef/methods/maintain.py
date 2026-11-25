"""Maintain: ``correct``, ``undo`` and ``forget``.

- :func:`correct` replaces one exact passage of an engram.
- :func:`undo` restores an earlier version of an engram, as a new change.
- :func:`forget` deletes an engram or a file for good, in two calls: the
  first says what would go and gives a token, the second brings the token
  back. See :mod:`reef.methods.staged`.

**The persona.** It changes through ``instruct`` only, so all three refuse
its path before reading or writing anything. Other ``meta/`` paths are
refused as a write to them is.

**Privacy.** Nothing the caller sent -- a passage, a title, a file name --
is logged. Titles and file names appear only in the reply to the caller.
"""

from reef import filetext
from reef.access import resolve_writable_space
from reef.attachments import S3ObjectStore, delete_attachment, get_attachment
from reef.config import get_settings
from reef.db import transaction_scope
from reef.engrams import (
    PERSONA_PATH,
    PROTECTED_PREFIX,
    EngramNotFound,
    InvalidPath,
    delete_engram,
    get_engram,
    normalize_path,
    save_engram,
)
from reef.methods import Caller, MethodError, method, staged
from reef.methods.find import FILE_PREFIX
from reef.methods.logs import LOG_TAG
from reef.methods.refusals import refusals
from reef.models import Engram, Revision, SpaceKind

#: The action staged tokens for :func:`forget` are bound to.
FORGET = "forget"


def _refuse_meta(path: str) -> None:
    """Refuse the persona, and any other path under ``meta/``.

    The path is checked as sent and as normalized, so ``META/Persona`` does
    not slip past.

    :param path: the engram's path as the caller sent it
    :raises MethodError: ``use_instruct`` for the persona,
        ``protected_path`` for another ``meta/`` path
    """
    forms = {path, path.strip().lower()}
    try:
        forms.add(normalize_path(path))
    except InvalidPath:
        pass
    if PERSONA_PATH in forms:
        raise MethodError("use_instruct")
    if any(form.startswith(PROTECTED_PREFIX) for form in forms):
        raise MethodError("protected_path")


async def _find(caller: Caller, space: str, path: str) -> Engram:
    """Find an engram the caller may change, by the name it is stored under.

    An engram stored before paths were normalized keeps its name, so the
    path is tried as sent first, then normalized.

    :param caller: who is calling
    :param space: the caller's name for the space
    :param path: the engram's path
    :returns: the engram
    :raises MethodError: ``not_found`` when there is none, and the refusals
        of :func:`reef.methods.refusals.refusals`
    """
    with refusals():
        await resolve_writable_space(caller.principal, space)
        engram = await get_engram(caller.principal, space, path)
        if engram is None:
            try:
                engram = await get_engram(caller.principal, space, normalize_path(path))
            except InvalidPath:
                engram = None
    if engram is None:
        raise MethodError("not_found")
    return engram


def _changed(space: str, engram: Engram) -> dict:
    """Shape what :func:`correct` and :func:`undo` return.

    :param space: the caller's name for the space
    :param engram: the engram as saved
    :returns: ``space``, ``engram`` (its path) and ``version``
    """
    return {"space": space, "engram": engram.path, "version": engram.version}


@method("correct")
async def correct(
    caller: Caller,
    reason: str,
    space: str,
    engram: str,
    old_text: str,
    new_text: str,
    message: str,
    expected_version: int | None = None,
) -> dict:
    """Replace one exact passage of an engram, and change nothing else.

    The passage must occur exactly once. History keeps the old text and who
    changed it.

    :param caller: who is calling
    :param reason: why, in the caller's words
    :param space: the caller's name for the space
    :param engram: the engram's path
    :param old_text: the passage to replace, exactly as the engram has it
    :param new_text: what takes its place
    :param message: what changed and why, kept in history
    :param expected_version: the version the change is based on; the one
        now, when None
    :returns: ``space``, ``engram`` and the new ``version``
    :raises MethodError: ``use_instruct``, ``protected_path``,
        ``unknown_space``, ``read_only``, ``not_found``,
        ``passage_not_found``, ``passage_ambiguous``, ``version_conflict``,
        ``engram_too_large`` or ``private_content``
    """
    _refuse_meta(engram)
    async with transaction_scope():
        current = await _find(caller, space, engram)
        found = current.body.count(old_text) if old_text else 0
        if found == 0:
            raise MethodError("passage_not_found")
        if found > 1:
            raise MethodError("passage_ambiguous")
        with refusals(current.path):
            saved = await save_engram(
                caller.principal,
                space,
                current.path,
                current.body.replace(old_text, new_text),
                message=message,
                expected_version=(
                    current.version if expected_version is None else expected_version
                ),
                channel=caller.writer,
            )
    return _changed(space, saved)


@method("undo")
async def undo(
    caller: Caller,
    reason: str,
    space: str,
    engram: str,
    version: int | None = None,
) -> dict:
    """Restore an earlier version of an engram, as a new change.

    Versions number the engram's revisions in order, from 1. With no
    ``version`` the one before the current version comes back. Body, title
    and tags are restored; history keeps everything, the undone change
    included. A forgotten engram has no history left, so it cannot come
    back.

    :param caller: who is calling
    :param reason: why, in the caller's words
    :param space: the caller's name for the space
    :param engram: the engram's path
    :param version: the version to restore, or None for the one before this
    :returns: ``space``, ``engram`` and the new ``version``
    :raises MethodError: ``use_instruct``, ``protected_path``,
        ``unknown_space``, ``read_only``, ``not_found``,
        ``nothing_to_undo``, ``no_such_version``, ``version_conflict``,
        ``engram_too_large`` or ``private_content``
    """
    _refuse_meta(engram)
    async with transaction_scope():
        current = await _find(caller, space, engram)
        wanted = current.version - 1 if version is None else version
        if wanted == current.version or (version is None and wanted < 1):
            raise MethodError("nothing_to_undo")
        if wanted < 1 or wanted > current.version:
            raise MethodError("no_such_version")
        revisions = (
            await Revision.objects()
            .where(Revision.engram_id == current.id)
            .order_by(Revision.created_at, Revision.id)
            .limit(1)
            .offset(wanted - 1)
        )
        if not revisions:
            raise MethodError("no_such_version")
        old = revisions[0]
        with refusals(current.path):
            saved = await save_engram(
                caller.principal,
                space,
                current.path,
                old.body,
                message=f"Undo to version {wanted}",
                title=old.title,
                tags=_restored_tags(current, old),
                expected_version=current.version,
                channel=caller.writer,
            )
    return _changed(space, saved)


def _restored_tags(current: Engram, old: Revision) -> list[str]:
    """Choose the tags an undo restores.

    Being a log is structure, not content, so a current ``log`` tag stays
    even when the old revision has none: logs tagged by a migration have no
    revision that carries it.

    :param current: the engram as it is now
    :param old: the revision being restored
    :returns: the old tags, plus ``log`` when the engram is a log now
    """
    tags = list(old.tags)
    if LOG_TAG in (current.tags or []) and LOG_TAG not in tags:
        tags.append(LOG_TAG)
    return tags


@method("forget")
async def forget(
    caller: Caller,
    reason: str,
    space: str,
    engram: str | None = None,
    file: str | None = None,
    confirm: str | None = None,
) -> dict:
    """Delete an engram, with its history, or a stored file, for good.

    Two calls. The first changes nothing: it says what would go, and gives a
    token. Show the person the effect and wait for a yes. The second call
    brings the token back as ``confirm`` for the same target, and deletes.
    A token for one target does not delete another.

    :param caller: who is calling
    :param reason: why, in the caller's words
    :param space: the caller's name for the space
    :param engram: the path of the engram to forget; or ``file``
    :param file: the key of the file to forget; or ``engram``
    :param confirm: the token from the first call
    :returns: without ``confirm``, ``effect`` (plain text), ``token`` and
        ``expires_in``; with it, ``forgotten``, a reference like
        :func:`reef.methods.find.open` takes
    :raises MethodError: ``one_target``, ``use_instruct``,
        ``protected_path``, ``unknown_space``, ``read_only``,
        ``not_found`` for a target that is missing or that the caller may
        not read, ``stale_token`` or ``not_configured``
    """
    if (engram is None) == (file is None):
        raise MethodError("one_target")
    if engram is not None:
        _refuse_meta(engram)
        return await _forget_engram(caller, space, engram, confirm)
    assert file is not None
    return await _forget_file(caller, space, file, confirm)


def _shared(effect: str, space: str, kind: str) -> str:
    """Add to an effect that, in a shared space, every member loses it.

    :param effect: what goes
    :param space: the caller's name for the space
    :param kind: the space's kind
    :returns: the effect, with the sentence about members when shared
    """
    if kind != SpaceKind.SHARED.value:
        return effect
    return f"{effect} Every member of the space {space} loses it."


def _stage(caller: Caller, subject: dict) -> dict:
    """Issue the token for a forget.

    :param caller: who is staging
    :param subject: the target
    :returns: ``token`` and ``expires_in``
    """
    return staged.stage(caller, FORGET, subject)


async def _take(caller: Caller, token: str, accepted: list[dict]) -> dict:
    """Take a token, and require what it was staged for to be this call's target.

    :param caller: who is confirming
    :param token: the token from the first call
    :param accepted: the subjects that name this call's own target
    :returns: the subject staged
    :raises MethodError: ``stale_token`` when it is staged for another target
    """
    subject = await staged.take(caller, FORGET, token)
    own = {k: subject.get(k) for k in ("space", "engram", "file") if k in subject}
    if own not in accepted:
        raise MethodError("stale_token")
    return subject


async def _forget_engram(
    caller: Caller, space: str, path: str, confirm: str | None
) -> dict:
    """Stage or carry out the forgetting of one engram.

    :param caller: who is calling
    :param space: the caller's name for the space
    :param path: the engram's path
    :param confirm: the token, or None to stage
    :returns: what :func:`forget` returns
    :raises MethodError: as :func:`forget` does
    """
    if confirm is None:
        async with transaction_scope():
            current = await _find(caller, space, path)
            with refusals():
                resolved = await resolve_writable_space(caller.principal, space)
        effect = (
            f'This permanently deletes the engram "{current.title}" '
            f"({current.path}) and its whole history. It cannot be undone."
        )
        return {
            "effect": _shared(effect, space, resolved.kind),
            **_stage(
                caller,
                {
                    "space": space,
                    "engram": current.path,
                    "space_id": str(resolved.id),
                    "engram_id": str(current.id),
                },
            ),
        }
    names = {path}
    try:
        names.add(normalize_path(path))
    except InvalidPath:
        pass
    subject = await _take(
        caller, confirm, [{"space": space, "engram": n} for n in names]
    )
    try:
        with refusals():
            async with transaction_scope():
                resolved = await resolve_writable_space(caller.principal, space)
                current = await get_engram(caller.principal, space, subject["engram"])
                # The token was staged for one engram in one space. A new
                # engram at the same path, or the name now pointing at
                # another space, is something nobody has confirmed.
                if (
                    current is None
                    or str(resolved.id) != subject.get("space_id")
                    or str(current.id) != subject.get("engram_id")
                ):
                    raise EngramNotFound
                await delete_engram(caller.principal, space, subject["engram"])
    except EngramNotFound:
        raise MethodError("not_found") from None
    return {"forgotten": f"{space}/{subject['engram']}"}


async def _forget_file(
    caller: Caller, space: str, key: str, confirm: str | None
) -> dict:
    """Stage or carry out the forgetting of one stored file.

    :param caller: who is calling
    :param space: the caller's name for the space
    :param key: the file's key
    :param confirm: the token, or None to stage
    :returns: what :func:`forget` returns
    :raises MethodError: as :func:`forget` does
    """
    subject = {"space": space, "file": key}
    if confirm is None:
        with refusals():
            async with transaction_scope():
                resolved = await resolve_writable_space(caller.principal, space)
                attachment = await get_attachment(caller.principal, space, key)
                read = (
                    attachment is not None
                    and get_settings().reading.enabled
                    and await filetext.has_pages(attachment.id)
                )
        if attachment is None:
            raise MethodError("not_found")
        name = attachment.filename or key.rsplit("/", 1)[-1]
        gone = " and the text Reef read from it" if read else ""
        effect = (
            f'This permanently deletes the file "{name}" '
            f"({attachment.byte_size:,} bytes){gone}. It cannot be undone."
        )
        return {
            "effect": _shared(effect, space, resolved.kind),
            **_stage(caller, {**subject, "space_id": str(resolved.id)}),
        }
    staged_for = await _take(caller, confirm, [subject])
    with refusals():
        removed = await delete_attachment(
            caller.principal,
            space,
            key,
            store=S3ObjectStore(),
            expected_space_id=staged_for.get("space_id") or "",
        )
    if not removed:
        raise MethodError("not_found")
    return {"forgotten": f"{space}/{FILE_PREFIX}{key}"}
