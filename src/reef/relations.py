"""Read relation lines without changing the surrounding Markdown."""

import re
from dataclasses import dataclass

from reef.models import LinkKind

RELATION_KEYS: dict[str, LinkKind] = {
    "replaces": LinkKind.SUPERSEDES,
    "contradicts": LinkKind.CONTRADICTS,
    "about": LinkKind.ABOUT,
    "part_of": LinkKind.PART_OF,
}
MAX_RELATIONS = 20
_KEY = re.compile(r"^(replaces|contradicts|about|part_of):[ \t]*(.*)$")
_ITEM = re.compile(r"^[ \t]+- (.*)$")
_VALUE = re.compile(r"\[\[([^\[\]\r\n]+)\]\]")


class InvalidRelations(ValueError):
    """A relation is invalid, exceeds the limit, or cannot remain in stored text."""

    def __init__(self, code: str = "relation_invalid") -> None:
        """Keep the refusal code for callers.

        :param code: the reason the block was refused
        """
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Relations:
    """Given kinds replace their lists; absent kinds stay as they were."""

    given: dict[LinkKind, tuple[str, ...]]

    @property
    def empty(self) -> bool:
        """Say whether the body gave no relation keys.

        :returns: whether there is nothing to change
        """
        return not self.given


def _targets(value: str) -> list[str]:
    """Read a comma-separated list of wiki links.

    :param value: the text after a key or list marker
    :returns: targets in the order written
    :raises InvalidRelations: when any text is not a wiki link
    """
    value = value.strip()
    if not value:
        return []
    targets: list[str] = []
    while value:
        quote = value[0] if value[0] in ("'", '"') else ""
        if quote:
            value = value[1:].lstrip()
        match = _VALUE.match(value)
        if match is None:
            raise InvalidRelations()
        target = match[1].split("|", 1)[0].strip()
        if not target:
            raise InvalidRelations()
        targets.append(target)
        value = value[match.end() :].strip()
        if quote:
            if not value.startswith(quote):
                raise InvalidRelations()
            value = value[1:].strip()
        if not value:
            break
        if not value.startswith(",") or not value[1:].strip():
            raise InvalidRelations()
        value = value[1:].strip()
    return targets


def take_block(body: str) -> tuple[str, Relations]:
    """Take recognized relation lines out of a closed leading block.

    Only LF, optionally preceded by CR, ends a line. Leading whitespace and
    byte-order marks, unknown lines and line endings remain as written.
    Repeated keys are refused so an earlier list cannot silently disappear.

    :param body: the Markdown supplied by the writer
    :returns: the remaining body and the relation lists
    :raises InvalidRelations: for a malformed or oversized relation list
    """
    start = next(
        (i for i, char in enumerate(body) if not char.isspace() and char != "\ufeff"),
        len(body),
    )
    prefix = body[:start]
    pieces = body[start:].split("\n")
    lines = [piece + "\n" for piece in pieces[:-1]] + pieces[-1:]
    if not lines or _line_text(lines[0]) != "---":
        return body, Relations({})
    end = next(
        (i for i in range(1, len(lines)) if _line_text(lines[i]) == "---"),
        None,
    )
    if end is None:
        return body, Relations({})
    kept: list[str] = []
    given: dict[LinkKind, tuple[str, ...]] = {}
    index = 1
    while index < end:
        match = _relation_line(_line_text(lines[index]))
        if match is None:
            kept.append(lines[index])
            index += 1
            continue
        kind = RELATION_KEYS[match[1]]
        if kind in given:
            raise InvalidRelations()
        targets = _targets(match[2])
        index += 1
        while index < end:
            item = _ITEM.fullmatch(_line_text(lines[index]))
            if item is None:
                break
            listed = _targets(item[1])
            if len(listed) != 1:
                raise InvalidRelations()
            targets.extend(listed)
            index += 1
        unique = tuple(dict.fromkeys(targets))
        if len(unique) > MAX_RELATIONS:
            raise InvalidRelations("relation_too_many")
        given[kind] = unique
    if not given:
        return body, Relations({})
    remaining = lines[0] + "".join(kept) + lines[end] if kept else ""
    return prefix + remaining + "".join(lines[end + 1 :]), Relations(given)


def _line_text(line: str) -> str:
    """Remove one LF line ending and its optional preceding CR.

    :param line: one piece split at LF, with its ending retained
    :returns: the line's text
    """
    return line[:-2] if line.endswith("\r\n") else line.removesuffix("\n")


def _relation_line(line: str) -> re.Match[str] | None:
    """Recognize only empty values and values holding a wiki link.

    :param line: a line inside a leading block
    :returns: the key and value match, or None for ordinary text
    """
    match = _KEY.fullmatch(line)
    return (
        match if match and (not match[2].strip() or _VALUE.search(match[2])) else None
    )


def refuse_stored_block(body: str) -> None:
    """Refuse text a later editor save could turn into a relation.

    :param body: the body that would be stored
    :raises InvalidRelations: ``relation_block_in_body`` when either the
        original or normalized view has a relation or cannot be parsed
    """
    # Editors may normalize other separators. This view is only a guard;
    # write and the web route always parse the original text.
    for view in (body, "\n".join(body.splitlines())):
        try:
            _, relations = take_block(view)
        except InvalidRelations:
            raise InvalidRelations("relation_block_in_body") from None
        if not relations.empty:
            raise InvalidRelations("relation_block_in_body")
