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
    """A relation line cannot be read or contains too many links."""

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
        match = _VALUE.match(value)
        if match is None or not match[1].strip():
            raise InvalidRelations()
        targets.append(match[1].strip())
        value = value[match.end() :].strip()
        if not value:
            break
        if not value.startswith(",") or not value[1:].strip():
            raise InvalidRelations()
        value = value[1:].strip()
    return targets


def take_block(body: str) -> tuple[str, Relations]:
    """Take recognized relation lines out of a closed leading block.

    Unknown lines and their line endings remain exactly as written. Repeated
    keys are refused so an earlier list cannot silently disappear.

    :param body: the Markdown supplied by the writer
    :returns: the remaining body and the relation lists
    :raises InvalidRelations: for a malformed or oversized relation list
    """
    lines = body.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return body, Relations({})
    end = next(
        (i for i in range(1, len(lines)) if lines[i].rstrip("\r\n") == "---"),
        None,
    )
    if end is None:
        return body, Relations({})
    kept: list[str] = []
    given: dict[LinkKind, tuple[str, ...]] = {}
    index = 1
    while index < end:
        match = _KEY.fullmatch(lines[index].rstrip("\r\n"))
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
            item = _ITEM.fullmatch(lines[index].rstrip("\r\n"))
            if item is None:
                break
            targets.extend(_targets(item[1]))
            index += 1
        if len(targets) > MAX_RELATIONS:
            raise InvalidRelations("relation_too_many")
        given[kind] = tuple(dict.fromkeys(targets))
    if not given:
        return body, Relations({})
    remaining = lines[0] + "".join(kept) + lines[end] if kept else ""
    return remaining + "".join(lines[end + 1 :]), Relations(given)
