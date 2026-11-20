"""Prototype fast indexes, built from a :class:`snapshot.Snapshot`.

Spike code. Each variant returns a :class:`Built`: the payload an assistant
would receive (without the operating protocol, which every variant sends
unchanged), the lines it reads, and how each collapsed line expands.

The variants:

- ``today``: the payload ``load_index`` returns now.
- ``slim``: every engram still listed, but as one short text line; files
  become a count on their engram's line.
- ``v1``: engrams at a space's root listed; each top-level folder one line
  with its name, count and latest date.
- ``v2``: ``v1`` plus the top ``k`` engrams by inbound links, use and recency.
- ``v3``: semantic clusters of whole-engram vectors, one line each, labelled
  by the most central engram, with a count.
- ``v4``: the proposal. The slim list when it fits a budget; otherwise
  folders, top engrams and, inside any container too large to list, semantic
  clusters, sized so the whole index fills the budget and no more.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from pydantic_core import to_json
from sklearn.cluster import KMeans
from snapshot import Entry, Snapshot

#: Paths under this folder are Reef's own: always listed, never collapsed.
META = "meta"


@dataclass
class Line:
    """One line of a fast index, as the assistant reads it.

    A direct line names one engram. A container line stands for several and
    names the call that lists them.
    """

    text: str
    members: frozenset[tuple[str, str]]
    direct: bool
    expand: str | None = None


@dataclass
class Built:
    """A fast index: its payload, its lines and its expansions."""

    name: str
    payload: dict
    lines: list[Line]
    expansions: dict[str, list[str]] = field(default_factory=dict)

    def chars(self, protocol: str = "") -> int:
        """Return the payload's size as the MCP tool sends it: compact JSON.

        :param protocol: the operating protocol to count along, if any
        :returns: characters
        """
        payload = dict(self.payload)
        if protocol:
            payload["operating_protocol"] = protocol
        return len(to_json(payload).decode())

    def visible(self) -> set[tuple[str, str]]:
        """Return the engrams the index names directly.

        :returns: space alias and path of each
        """
        return {key for line in self.lines if line.direct for key in line.members}

    def container_of(self, key: tuple[str, str]) -> Line | None:
        """Return the collapsed line an engram sits under, if any.

        :param key: space alias and path
        :returns: the container line, or None
        """
        return next(
            (line for line in self.lines if not line.direct and key in line.members),
            None,
        )


# --- lines -------------------------------------------------------------------


def _stem(path: str) -> str:
    """Return an engram's file name without folder or extension, as words.

    :param path: the engram's path
    :returns: the stem, lower case, hyphens as spaces
    """
    return path.rsplit("/", 1)[-1].removesuffix(".md").replace("-", " ").lower()


def engram_line(entry: Entry) -> str:
    """Write one engram as a single short line.

    The title is left out when it only repeats the file name.

    :param entry: the engram
    :returns: ``path | title | description [#tags] [n files]``
    """
    parts = [entry.path]
    if entry.title and entry.title.lower() != _stem(entry.path):
        parts.append(entry.title)
    if entry.description:
        parts.append(entry.description[:160])
    text = " | ".join(parts)
    if entry.tags:
        text += " " + " ".join(f"#{tag}" for tag in entry.tags)
    if entry.files:
        text += f" [{entry.files} files]"
    return text


def _space_header(space: dict) -> dict:
    """Keep what a fast index still says about a space itself.

    :param space: the space as today's index gives it
    :returns: alias, role, owner and members
    """
    return {key: space[key] for key in ("alias", "role", "owner", "members")}


def _by_space(snapshot: Snapshot) -> dict[str, list[Entry]]:
    """Group a snapshot's engrams by space, in index order.

    :param snapshot: the snapshot
    :returns: entries per space alias
    """
    grouped: dict[str, list[Entry]] = {s["alias"]: [] for s in snapshot.index["spaces"]}
    for entry in snapshot.entries:
        grouped[entry.space].append(entry)
    return grouped


def _expansion(entries: list[Entry]) -> list[str]:
    """List a container's engrams as an expansion returns them.

    :param entries: the container's engrams
    :returns: one slim line each, best scored first
    """
    return [engram_line(entry) for entry in sorted(entries, key=_score, reverse=True)]


# --- signals -----------------------------------------------------------------


def _score(entry: Entry) -> float:
    """Score how much an engram looks like a main concept, without its text.

    Inbound links count most, then the person's own use, then recency. A
    ``core`` tag adds a fixed bonus. Recency is a year-long linear fade, so
    it only breaks ties between engrams the other signals rank alike.

    :param entry: the engram
    :returns: the score, higher first
    """
    links = math.log1p(entry.inbound)
    use = max(entry.activation, 0.0) if entry.activation is not None else 0.0
    age_days = max((_NOW - entry.updated).days, 0)
    recency = max(0.0, 1.0 - age_days / 365)
    core = 1.0 if "core" in entry.tags else 0.0
    return links + use + 0.5 * recency + core


#: When "recent" is measured from. Set per snapshot by :func:`set_now`.
_NOW = datetime(2026, 11, 20)


def set_now(snapshot: Snapshot) -> None:
    """Measure recency from the newest engram in the snapshot.

    The benchmark's dates are years old; measuring from today would make
    every engram equally stale.

    :param snapshot: the snapshot
    """
    global _NOW
    _NOW = max(entry.updated for entry in snapshot.entries)


def top(entries: list[Entry], k: int) -> list[Entry]:
    """Pick the ``k`` engrams that score highest.

    :param entries: candidates
    :param k: how many
    :returns: the best, best first
    """
    return sorted(entries, key=_score, reverse=True)[:k]


# --- clustering --------------------------------------------------------------


def clusters(entries: list[Entry], k: int, seed: int = 0) -> list[list[Entry]]:
    """Split engrams into ``k`` groups by meaning, most central member first.

    K-means on the unit-length whole-engram vectors. Engrams without a vector
    form a group of their own, at the end.

    :param entries: the engrams
    :param k: how many groups to aim for
    :param seed: makes the split repeatable
    :returns: the groups, each sorted from its most central member out
    """
    with_vector = [e for e in entries if e.vector is not None]
    without = [e for e in entries if e.vector is None]
    groups: list[list[Entry]] = []
    if with_vector:
        k = max(1, min(k, len(with_vector)))
        matrix = np.stack([e.vector for e in with_vector])
        model = KMeans(n_clusters=k, n_init=4, random_state=seed).fit(matrix)
        for label in range(k):
            idx = np.flatnonzero(model.labels_ == label)
            if not len(idx):
                continue
            centre = model.cluster_centers_[label]
            order = idx[np.argsort(-(matrix[idx] @ centre))]
            groups.append([with_vector[i] for i in order])
    if without:
        groups.append(without)
    return groups


def cluster_line(group: list[Entry], examples: int = 1) -> str:
    """Label a group by its most central engrams.

    :param group: the group, most central first
    :param examples: how many central engrams to name
    :returns: the label text, with the count
    """
    names = []
    for entry in group[:examples]:
        name = (
            entry.title
            if entry.title and not entry.title.startswith("Conversation on")
            else ""
        )
        text = f"{name}: {entry.description}" if name else entry.description
        names.append(text[: 160 if examples == 1 else 70])
    label = "; ".join(names)
    return f"{label} ({len(group)} engrams)"


# --- variants ----------------------------------------------------------------


def today(snapshot: Snapshot) -> Built:
    """Return today's payload, unchanged.

    :param snapshot: the snapshot
    :returns: the variant
    """
    payload = {k: v for k, v in snapshot.index.items() if k != "operating_protocol"}
    lines = [
        Line(engram_line(e), frozenset([e.key]), direct=True) for e in snapshot.entries
    ]
    return Built("today", payload, lines)


def slim(snapshot: Snapshot) -> Built:
    """List every engram, one short line each.

    :param snapshot: the snapshot
    :returns: the variant
    """
    spaces = []
    lines = []
    for space in snapshot.index["spaces"]:
        entries = _by_space(snapshot)[space["alias"]]
        text = [engram_line(e) for e in entries]
        lines += [
            Line(t, frozenset([e.key]), direct=True)
            for t, e in zip(text, entries, strict=True)
        ]
        spaces.append(
            {
                **_space_header(space),
                "engrams": text,
                "files": len(space["attachments"]),
            }
        )
    return Built(
        "slim", {"version": snapshot.index["version"], "spaces": spaces}, lines
    )


def _folder_line(alias: str, folder: str, entries: list[Entry], examples: int) -> str:
    """Write one collapsed folder.

    :param alias: the space
    :param folder: the folder name
    :param entries: its engrams
    :param examples: how many top-scored titles to name
    :returns: the line
    """
    latest = max(e.updated for e in entries).date().isoformat()
    text = f"{folder}/ ({len(entries)} engrams, latest {latest})"
    if examples:
        names = [e.title or _stem(e.path) for e in top(entries, examples)]
        text += ": " + "; ".join(n[:60] for n in names)
    return text


def folders(snapshot: Snapshot, k: int = 0, examples: int = 0) -> Built:
    """Collapse each top-level folder into one line (V1), plus top engrams (V2).

    :param snapshot: the snapshot
    :param k: how many top-scored engrams inside folders to list as well
    :param examples: how many titles each folder line names
    :returns: the variant
    """
    spaces = []
    lines: list[Line] = []
    expansions: dict[str, list[str]] = {}
    for space in snapshot.index["spaces"]:
        alias = space["alias"]
        entries = _by_space(snapshot)[alias]
        root = [e for e in entries if e.folder is None or e.folder == META]
        nested: dict[str, list[Entry]] = {}
        for e in entries:
            if e.folder is not None and e.folder != META:
                nested.setdefault(e.folder, []).append(e)
        picked = top([e for group in nested.values() for e in group], k)
        listed = root + picked
        listed_text = [engram_line(e) for e in listed]
        lines += [
            Line(t, frozenset([e.key]), direct=True)
            for t, e in zip(listed_text, listed, strict=True)
        ]
        folder_text = []
        for name, group in sorted(nested.items()):
            text = _folder_line(alias, name, group, examples)
            ref = f"{alias}/{name}/"
            folder_text.append(text)
            expansions[ref] = _expansion(group)
            lines.append(
                Line(text, frozenset(e.key for e in group), direct=False, expand=ref)
            )
        body = {**_space_header(space), "engrams": listed_text, "folders": folder_text}
        if space["attachments"]:
            body["files"] = len(space["attachments"])
        spaces.append(body)
    name = "v1" if not k else f"v2 (k={k})"
    payload = {
        "version": snapshot.index["version"],
        "spaces": spaces,
        "note": (
            'Collapsed lines stand for several engrams. load_index(expand="<space>/'
            '<start of the line>") lists one, e.g. expand="personal/people/" or '
            'expand="personal/~3". recall finds any engram, listed or not.'
        ),
    }
    return Built(name, payload, lines, expansions)


def semantic(snapshot: Snapshot, k: int | None = None) -> Built:
    """One line per semantic cluster of each space (V3).

    :param snapshot: the snapshot
    :param k: clusters per space; by default the square root of its size,
        between 2 and 50
    :returns: the variant
    """
    spaces = []
    lines: list[Line] = []
    expansions: dict[str, list[str]] = {}
    for space in snapshot.index["spaces"]:
        alias = space["alias"]
        entries = _by_space(snapshot)[alias]
        meta = [e for e in entries if e.folder == META]
        rest = [e for e in entries if e.folder != META]
        want = k or max(2, min(50, round(math.sqrt(len(rest)))))
        listed_text = [engram_line(e) for e in meta]
        lines += [
            Line(t, frozenset([e.key]), direct=True)
            for t, e in zip(listed_text, meta, strict=True)
        ]
        cluster_text = []
        for number, group in enumerate(clusters(rest, want), 1):
            ref = f"{alias}/~{number}"
            text = f"~{number} {cluster_line(group)}"
            cluster_text.append(text)
            expansions[ref] = _expansion(group)
            lines.append(
                Line(text, frozenset(e.key for e in group), direct=False, expand=ref)
            )
        body = {**_space_header(space), "engrams": listed_text, "topics": cluster_text}
        if space["attachments"]:
            body["files"] = len(space["attachments"])
        spaces.append(body)
    payload = {
        "version": snapshot.index["version"],
        "spaces": spaces,
        "note": (
            'Collapsed lines stand for several engrams. load_index(expand="<space>/'
            '<start of the line>") lists one, e.g. expand="personal/people/" or '
            'expand="personal/~3". recall finds any engram, listed or not.'
        ),
    }
    return Built("v3", payload, lines, expansions)


def budgeted(snapshot: Snapshot, budget: int, k: int = 10, examples: int = 3) -> Built:
    """The proposal (V4): fill a size budget, collapsing only what does not fit.

    1. If the slim list fits in ``budget`` characters, return it.
    2. Otherwise list ``meta/``, root engrams and the top ``k`` by score, and
       collapse each folder into a line naming its top ``examples`` titles.
    3. Split the root set or any folder too large to name in a line into
       semantic clusters. Clusters are shared out between the large
       containers in proportion to their size, as many as the budget holds.

    :param snapshot: the snapshot
    :param budget: the most characters the index (without protocol) may take
    :param k: how many top-scored engrams to list in any case
    :param examples: how many central titles a collapsed line names
    :returns: the variant
    """
    flat = slim(snapshot)
    if flat.chars() <= budget:
        flat.name = f"v4 ({budget // 1000}k)"
        return flat
    spaces = []
    lines: list[Line] = []
    expansions: dict[str, list[str]] = {}
    grouped = _by_space(snapshot)
    # First pass: what is listed in any case, and which containers are large.
    plans = []
    for space in snapshot.index["spaces"]:
        alias = space["alias"]
        entries = grouped[alias]
        meta = [e for e in entries if e.folder == META]
        picked = top([e for e in entries if e.folder != META], k)
        chosen = {e.key for e in meta + picked}
        containers: dict[str, list[Entry]] = {}
        for e in entries:
            if e.key in chosen:
                continue
            containers.setdefault(e.folder or "", []).append(e)
        plans.append((space, meta + picked, containers))
    fixed = sum(
        len(engram_line(e)) + 3 for _, listed, _ in plans for e in listed
    ) + 400 * len(plans)
    line_cost = 230 if examples > 1 else 180
    room = max(0, budget - fixed)
    large = {
        (space["alias"], name): group
        for space, _, containers in plans
        for name, group in containers.items()
        if len(group) > 1
    }
    total_large = sum(len(g) for g in large.values()) or 1
    slots = max(len(large), room // line_cost)
    for space, listed, containers in plans:
        alias = space["alias"]
        listed_text = [engram_line(e) for e in listed]
        lines += [
            Line(t, frozenset([e.key]), direct=True)
            for t, e in zip(listed_text, listed, strict=True)
        ]
        collapsed = []
        number = 0
        for name, group in sorted(containers.items()):
            share = max(1, round(slots * len(group) / total_large))
            if len(group) == 1:
                listed_text.append(engram_line(group[0]))
                lines.append(Line(listed_text[-1], frozenset([group[0].key]), True))
                continue
            parts = [group] if share == 1 else clusters(group, share)
            for part in parts:
                number += 1
                where = f"{name}/" if name else ""
                ref = (
                    f"{alias}/{where}~{number}"
                    if len(parts) > 1
                    else f"{alias}/{name}/"
                )
                if len(parts) == 1 and name:
                    text = _folder_line(alias, name, part, examples)
                else:
                    text = f"{where}~{number} {cluster_line(part, examples)}"
                collapsed.append(text)
                expansions[ref] = _expansion(part)
                lines.append(
                    Line(text, frozenset(e.key for e in part), direct=False, expand=ref)
                )
        body = {**_space_header(space), "engrams": listed_text, "collapsed": collapsed}
        if space["attachments"]:
            body["files"] = len(space["attachments"])
        spaces.append(body)
    payload = {
        "version": snapshot.index["version"],
        "spaces": spaces,
        "note": (
            'Collapsed lines stand for several engrams. load_index(expand="<space>/'
            '<start of the line>") lists one, e.g. expand="personal/people/" or '
            'expand="personal/~3". recall finds any engram, listed or not.'
        ),
    }
    return Built(f"v4 ({budget // 1000}k)", payload, lines, expansions)
