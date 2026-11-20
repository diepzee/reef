"""Spike only: let ``load_index`` serve a fast index, for the LLM-in-the-loop runs.

Switched on by ``REEF_SPIKE_INDEX`` in the server's environment:

- unset or ``today``: ``load_index`` is unchanged;
- ``v1``, ``v2``, ``v3``: the variants of :mod:`variants`;
- ``v4:<budget>[:k<top>]``: the budgeted proposal, for example ``v4:16000``,
  or ``v4:16000:k0`` to list no top-scored engrams.

``load_index(expand=...)`` lists what one collapsed line holds: a space by
its name, a folder as ``<space>/<folder>/``, a group as the reference its
line gives. The variant is built once per conversation and kept, so the
references stay stable while the assistant works; a real implementation
would store the groups instead.

The operating protocol's paragraph about the index is replaced, because it
promises "every engram and file".
"""

import os
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).parent))

import snapshot as snap
import variants as var

from reef.access import Principal

_CACHE: dict[UUID, var.Built] = {}

PROTOCOL_TODAY = """Call `load_index` first. It returns this protocol, the person's persona
(follow both), and the index. The index lists each space, its members, the
person's role there, and every engram and file in it, with a one-line
description. It holds no engram text.

Read the index to learn what exists. Never answer from a description alone."""

PROTOCOL_FAST = """Call `load_index` first. It returns this protocol, the person's persona
(follow both), and the index. The index shows each space, its members, the
person's role there, and its main engrams, each with a one-line description.
The rest is collapsed: one line stands for a folder or a group of related
engrams, with a count. `load_index(expand=...)` lists what one line holds.
The index holds no engram text.

The index is a map, not a search. To answer a question, use `recall`: it
finds any engram, listed or not. Before you write about something new, expand
the line where it would belong or `recall` the topic, so you add to the engram
that exists rather than start a second one. Never answer from a description
alone."""


#: Added with ``REEF_SPIKE_PROTOCOL=strong``: placement made explicit.
PROTOCOL_STRONG = """

**The index is not complete.** A person, project or topic you do not see in
it may still have an engram. Before `remember`, `write` or `log` about a
subject, call `recall` on the subject first. When an engram about it exists,
add the fact there with `correct`; use `remember` only when none does."""


def variant() -> str:
    """Return the variant this server runs.

    :returns: the value of ``REEF_SPIKE_INDEX``, or ``today``
    """
    return os.environ.get("REEF_SPIKE_INDEX", "today") or "today"


def build(name: str, snapshot: snap.Snapshot) -> var.Built:
    """Build the named variant.

    :param name: the variant, as :func:`variant` returns it
    :param snapshot: the memory
    :returns: the built variant
    """
    if name == "v1":
        return var.folders(snapshot)
    if name == "v2":
        return var.folders(snapshot, k=10)
    if name == "v3":
        return var.semantic(snapshot)
    if name.startswith("v4"):
        # v4:<budget>[:k<top>], for example v4:16000:k0 to list no top engrams.
        parts = name.split(":")
        budget = int(parts[1]) if len(parts) > 1 and parts[1] else 16_000
        top = int(parts[2].removeprefix("k")) if len(parts) > 2 else 10
        return var.budgeted(snapshot, budget, k=top)
    if name == "slim":
        return var.slim(snapshot)
    raise ValueError(f"unknown spike index variant {name!r}")


async def fast_index(principal: Principal, index: dict, expand: str | None) -> dict:
    """Return the fast index, or one expansion of it.

    :param principal: the caller
    :param index: today's payload, already built for the caller
    :param expand: what to list, or None for the index itself
    :returns: the payload
    """
    name = variant()
    built = _CACHE.get(principal.person_id)
    if built is None or expand is None:
        snapshot = await snap.from_index("bench", principal, index)
        var.set_now(snapshot)
        built = build(name, snapshot) if name != "today" else var.slim(snapshot)
        _CACHE[principal.person_id] = built
        # The space listing is always available, whatever the variant.
        for space in var.slim(snapshot).payload["spaces"]:
            built.expansions.setdefault(space["alias"], space["engrams"])
            for line in space["engrams"]:
                folder = line.split(" | ", 1)[0].rsplit("/", 1)
                if len(folder) == 2:
                    built.expansions.setdefault(
                        f"{space['alias']}/{folder[0].split('/')[0]}/", []
                    )
    if expand is not None:
        wanted = expand.strip()
        if not wanted.endswith("/") and f"{wanted}/" in built.expansions:
            wanted = f"{wanted}/"
        lines = built.expansions.get(wanted)
        if lines is None or (not lines and wanted.endswith("/")):
            lines = _folder_lines(built, wanted)
        if lines is None:
            return {
                "expand": expand,
                "error": "Nothing by that name. Expand a space by its name, a "
                "folder as <space>/<folder>/, or a group by the reference its "
                "line gives.",
            }
        return {"expand": expand, "engrams": lines}
    if name == "today":
        return index
    payload = dict(built.payload)
    fast = PROTOCOL_FAST
    if os.environ.get("REEF_SPIKE_PROTOCOL") == "strong":
        fast += PROTOCOL_STRONG
    payload["operating_protocol"] = index["operating_protocol"].replace(
        PROTOCOL_TODAY, fast
    )
    return payload


def _folder_lines(built: var.Built, ref: str) -> list[str] | None:
    """List a folder's engrams from the space listing.

    :param built: the variant, with each space's listing under its name
    :param ref: ``<space>/<folder>/``
    :returns: the folder's lines, or None when there is no such folder
    """
    alias, _, folder = ref.partition("/")
    listing = built.expansions.get(alias)
    if listing is None or not folder:
        return None
    lines = [line for line in listing if line.startswith(folder)]
    return lines or None
