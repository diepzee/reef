"""Logs: engrams that grow by entries, and may be split into parts.

**What a log is.** An engram is a log if and only if it carries the tag
``log``. A log can outgrow the engram size cap, so it may continue in a
second engram: ``climbing-log.md``, ``climbing-log-2.md``,
``climbing-log-3.md``, and so on. The parts share a stem, sit in one folder
and each carry the tag.

**What an entry is.** A section at the log's entry level, which
:func:`entry_level` reads from its parts: level 3 (``###``) when any part
has one, level 2 when the parts have ``##`` but no ``###``, and level 3 for
a log with neither. An entry runs to the next heading of its level or a
higher one, so deeper headings stay inside it. Text before the first entry
heading, such as a part's opening link or the log's intro, is not an entry.
Headings inside fenced code do not count.

:func:`series` finds the parts and :func:`last_entries` reads the end of
them. :func:`reef.methods.find.brief` uses both. :func:`reef.methods.capture.log`
uses :func:`series`, writes each entry at :func:`entry_level`, so that
``brief`` keeps finding every entry, and names new parts with
:func:`part_path`.
"""

import re

from reef.access import resolve_space
from reef.db import transaction_scope
from reef.methods import Caller
from reef.models import Engram
from reef.sections import headings

#: The tag that makes an engram a log.
LOG_TAG = "log"

#: A trailing part number in a stem, such as the ``-2`` of ``climbing-log-2``.
_PART_SUFFIX = re.compile(r"-([1-9]\d*)$")

#: The level of entries when a log has ``##`` headings but no ``###``.
_SHALLOW = 2

#: The level of entries otherwise, and of a new log's entries.
_DEEP = 3


def _is_log(engram: Engram | None) -> bool:
    """Say whether an engram is a log.

    :param engram: the engram, or None
    :returns: True when it exists and carries the ``log`` tag
    """
    return engram is not None and LOG_TAG in (engram.tags or [])


def _split_path(path: str) -> tuple[str, str]:
    """Split a path into its folder and its file name without ``.md``.

    :param path: the engram's path
    :returns: the folder with its trailing ``/`` (empty at the top), and the
        name
    """
    folder, slash, name = path.rpartition("/")
    return folder + slash, name.removesuffix(".md")


def base_path(path: str) -> str | None:
    """Name the first part of the log a later part's path would belong to.

    :param path: an engram's path
    :returns: ``<stem>.md`` for a path ``<stem>-<n>.md`` with n of 2 or
        more; None for any other path
    """
    folder, name = _split_path(path)
    suffix = _PART_SUFFIX.search(name)
    if suffix is None or int(suffix.group(1)) < 2:
        return None
    return f"{folder}{name[: suffix.start()]}.md"


def part_path(base: str, number: int) -> str:
    """Name a part of a log.

    :param base: the path of the log's first part
    :param number: the part's number, 1 for the first
    :returns: the base for part 1, else ``<stem>-<number>.md`` beside it
    """
    if number == 1:
        return base
    folder, stem = _split_path(base)
    return f"{folder}{stem}-{number}.md"


def part_number(base: str, path: str) -> int:
    """Read a part's number from its path.

    :param base: the path of the log's first part
    :param path: the path of one of its parts, as :func:`series` gives them
    :returns: 1 for the base, else the ``<n>`` of ``<stem>-<n>.md``
    """
    if path == base:
        return 1
    folder, stem = _split_path(base)
    return int(path.removeprefix(f"{folder}{stem}-").removesuffix(".md"))


def _escape_like(text: str) -> str:
    """Escape the characters that mean something in a ``LIKE`` pattern.

    :param text: literal text
    :returns: the text, to be matched literally
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def series(caller: Caller, space: str, path: str) -> list[Engram]:
    """Find the parts of the log a path belongs to, first to last.

    The base is ``<stem>.md``, where the stem is the path without ``.md``
    and without a trailing ``-<n>`` (n of 2 or more), but only when that
    engram exists and is a log. Otherwise the path is its own base. The
    parts are the base and every ``<stem>-<n>.md`` (n of 2 or more) in the
    same folder that is a log, ordered by n as a number.

    The reads are armed for the caller, in a transaction of their own or
    the one already open.

    :param caller: who is reading
    :param space: the caller's name for the space
    :param path: the path of any part of the log
    :returns: the parts, or an empty list when the path names no engram the
        caller can read, or one that is not a log
    :raises reef.access.AccessDenied: when the caller has no space of that
        name
    """
    async with transaction_scope():
        found = await resolve_space(caller.principal, space)
        engram = (
            await Engram.objects()
            .where(Engram.space_id == found.id, Engram.path == path)
            .first()
        )
        if not _is_log(engram):
            return []
        base = engram
        if (named := base_path(path)) is not None:
            candidate = (
                await Engram.objects()
                .where(Engram.space_id == found.id, Engram.path == named)
                .first()
            )
            if _is_log(candidate):
                base = candidate
        folder, stem = _split_path(base.path)
        pattern = re.compile(rf"^{re.escape(folder + stem)}-([1-9]\d*)\.md$")
        siblings = await Engram.objects().where(
            Engram.space_id == found.id,
            Engram.path.like(f"{_escape_like(folder + stem)}-%.md"),
        )
    numbered = []
    for sibling in siblings:
        match = pattern.match(sibling.path)
        if match is not None and int(match.group(1)) >= 2 and _is_log(sibling):
            numbered.append((int(match.group(1)), sibling))
    return [base, *(part for _, part in sorted(numbered, key=lambda pair: pair[0]))]


def _entries_of(body: str, level: int) -> list[str]:
    """Cut one part's body into its entries.

    :param body: the part's markdown
    :param level: the heading level of an entry
    :returns: each entry with its heading line, in order
    """
    lines = body.splitlines()
    found = headings(body)
    entries = []
    for index, (number, depth, _) in enumerate(found):
        if depth != level:
            continue
        end = len(lines)
        for later, deeper, _ in found[index + 1 :]:
            if deeper <= level:
                end = later
                break
        entries.append("\n".join(lines[number:end]).strip())
    return entries


def entry_level(parts: list[Engram]) -> int:
    """Read the heading level a log's entries use.

    :param parts: the parts, as :func:`series` gives them; none for a new log
    :returns: 3 when any part has a ``###`` heading, 2 when the parts have
        ``##`` headings but no ``###``, else 3
    """
    levels = {level for part in parts for _, level, _ in headings(part.body)}
    if _DEEP not in levels and _SHALLOW in levels:
        return _SHALLOW
    return _DEEP


def last_entries(parts: list[Engram], n: int) -> list[str]:
    """Return the last entries of a log, across its parts.

    :param parts: the parts, first to last, as :func:`series` gives them
    :param n: how many entries, at most
    :returns: up to ``n`` entries, oldest first, each with its heading line
    """
    if n <= 0:
        return []
    level = entry_level(parts)
    entries = [entry for part in parts for entry in _entries_of(part.body, level)]
    return entries[-n:]
