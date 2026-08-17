"""What shipped, in the words a person reading it would use.

Two records come out of every release and this module owns the second one.
``CHANGELOG.md`` is generated from commit subjects and reads like the repo
talking to itself; this is the one addressed to somebody using reef, which
is why it is hand-written per change rather than derived. A commit subject
like "stop two worktrees' test runs from rebuilding the schema under each
other" is a good commit subject and tells a user nothing: they have no
worktrees.

A fragment is one file per user-facing change, so two open branches never
touch the same line. The fold consumes them at release time into
``site/release-notes.json``, which is the single source for both the public page
and the in-app panel -- one file, so the two can never disagree.

The version comparison lives here and nowhere else. The frontend receives a
boolean.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

#: The kinds a fragment may declare. Deliberately three: a longer list makes
#: authors deliberate over the label instead of over the sentence, and a
#: reader does not distinguish "improved" from "changed".
KINDS = ("added", "changed", "fixed")

#: Front matter: ``---``, a single ``kind:`` line, ``---``, then the body.
#: A whole YAML parser would be a dependency and a much larger grammar than
#: the one field this format has.
_FRONT_MATTER = re.compile(
    r"\A---\s*\n\s*kind:\s*(?P<kind>[a-z]+)\s*\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)


class FragmentError(Exception):
    """Raised when a fragment is not a fragment this format can read."""


@dataclass(frozen=True)
class Change:
    """One sentence about one change, and which kind of change it was."""

    kind: str
    text: str


@dataclass(frozen=True)
class Entry:
    """Everything a single release had to say to a person."""

    version: str
    date: str
    changes: list[Change]


def parse_fragment(raw: str) -> Change:
    """Read one fragment's text into a change.

    :param raw: the fragment file's whole contents
    :raises FragmentError: when the front matter is absent, the kind is not
        one of :data:`KINDS`, or the body is empty
    :returns: the change the fragment describes
    """
    match = _FRONT_MATTER.match(raw)
    if match is None:
        raise FragmentError(
            "a fragment must open with front matter: '---', 'kind: <kind>', '---'"
        )
    kind = match.group("kind")
    if kind not in KINDS:
        raise FragmentError(f"{kind!r} is not one of {', '.join(KINDS)}")
    # A wrapped body is one paragraph that happens to be hard-wrapped, not a
    # list; joining on whitespace is what the reader sees either way.
    text = " ".join(match.group("body").split())
    if not text:
        raise FragmentError("a fragment has no text; say what changed, for a person")
    return Change(kind=kind, text=text)


def read_fragments(directory: Path) -> list[Change]:
    """Read every fragment in ``directory``, grouped by kind.

    Ordered by kind (added, then changed, then fixed) and by filename within
    a kind, so a reader meets what is new before what is repaired, and two
    runs over the same directory produce byte-identical output.

    ``README.md`` is skipped by name: the directory ships with the
    instructions a contributor reads, and prose has no front matter, so
    every run would otherwise die on it. Nothing else is skipped -- a file
    that looks like a fragment and is not one must fail loudly rather than
    be dropped, because a silently ignored fragment is somebody's sentence
    going missing from the release.

    :param directory: the ``changes/`` directory, which need not exist
    :raises FragmentError: if any fragment in it is malformed
    :returns: the changes, in reading order
    """
    if not directory.is_dir():
        return []
    changes = []
    for path in sorted(directory.glob("*.md")):
        if path.name == "README.md":
            continue
        try:
            changes.append(parse_fragment(path.read_text(encoding="utf-8")))
        except FragmentError as error:
            raise FragmentError(f"{path}: {error}") from error
    return sorted(changes, key=lambda change: KINDS.index(change.kind))


def load_feed(path: Path) -> list[Entry]:
    """Read the published feed, newest entry first.

    A missing file reads as no entries rather than raising: the first
    release must not depend on somebody having created it by hand.

    :param path: the ``site/release-notes.json`` path
    :returns: the entries it holds
    """
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        Entry(
            version=entry["version"],
            date=entry["date"],
            changes=[
                Change(kind=change["kind"], text=change["text"])
                for change in entry["changes"]
            ],
        )
        for entry in raw.get("entries", [])
    ]


def write_feed(path: Path, entries: list[Entry]) -> None:
    """Write the feed, with a trailing newline so diffs stay one-line.

    :param path: the ``site/release-notes.json`` path
    :param entries: the entries to write, newest first
    """
    path.write_text(
        json.dumps(feed_as_json(entries), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def feed_as_json(entries: list[Entry]) -> dict:
    """Render entries as the plain JSON both the file and the API use.

    One shape for the stored file and the endpoint, so the panel and the
    public page are reading the same thing in the same form.

    :param entries: the entries to render, newest first
    :returns: a JSON-safe mapping
    """
    return {
        "entries": [
            {
                "version": entry.version,
                "date": entry.date,
                "changes": [
                    {"kind": change.kind, "text": change.text}
                    for change in entry.changes
                ],
            }
            for entry in entries
        ]
    }


def fold(fragments_dir: Path, feed_path: Path, version: str, date: str) -> Entry | None:
    """Fold this release's fragments into the feed and consume them.

    Returns ``None`` and writes nothing when there are no fragments: a
    release that changed nothing a person would notice should not announce
    itself, and an empty entry in the panel reads as a broken feature.

    :param fragments_dir: the ``changes/`` directory
    :param feed_path: the ``site/release-notes.json`` path
    :param version: the version semantic-release computed
    :param date: the release date, ``YYYY-MM-DD``
    :raises FragmentError: if any fragment is malformed -- the release fails
        rather than silently dropping somebody's sentence
    :returns: the entry written, or ``None`` if there was nothing to write
    """
    changes = read_fragments(fragments_dir)
    if not changes:
        return None
    entry = Entry(version=version, date=date, changes=changes)
    write_feed(feed_path, [entry, *load_feed(feed_path)])
    for path in fragments_dir.glob("*.md"):
        if path.name != "README.md":
            path.unlink()
    return entry


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse ``major.minor.patch`` into comparable numbers.

    :param version: a version string, with or without a leading ``v``
    :raises ValueError: when it is not three dot-separated integers
    :returns: the triple, which compares the way versions actually order
    """
    major, minor, patch = version.lstrip("v").split(".", 2)
    return int(major), int(minor), int(patch)


def is_unread(entries: list[Entry], last_seen: str | None) -> bool:
    """Say whether the newest entry is newer than what this person has read.

    An unparseable or absent mark counts as "never seen anything", which is
    the safe direction: the worst case is showing somebody a dot once.

    :param entries: the feed, newest first
    :param last_seen: the person's ``last_seen_release``, or ``None``
    :returns: whether to light the marker
    """
    if not entries:
        return False
    if last_seen is None:
        return True
    try:
        seen = parse_version(last_seen)
    except ValueError:
        return True
    return parse_version(entries[0].version) > seen
