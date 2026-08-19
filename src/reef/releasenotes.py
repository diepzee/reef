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
from html import escape
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
    release must not depend on somebody having created it by hand. A
    corrupt file degrades the same way, rather than 500ing the endpoint
    that serves it -- the feed is generated, but nothing stops it being
    hand-edited or truncated on disk after the fact.

    :param path: the ``site/release-notes.json`` path
    :returns: the entries it holds
    """
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
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
    # Guarded the same way as `seen` above: a hand-edited or malformed feed
    # whose newest entry has an unparseable version must not 500 the
    # endpoint. Unread is still the safe direction -- worst case, a dot
    # lights once for a version nobody can compare.
    try:
        newest = parse_version(entries[0].version)
    except ValueError:
        return True
    return newest > seen


#: How each kind is announced on the public page. The reader is told what
#: happened to them, not which enum member we filed it under.
_HEADINGS = {"added": "New", "changed": "Changed", "fixed": "Fixed"}


#: The page's shell. A literal document rather than a template file: the
#: site has no build step and no template engine, and adding either for one
#: page would be the largest thing in the site's chain.
_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>What's new in reef</title>
<meta name="description" content="What has changed in reef, newest first.">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="icon" type="image/png" sizes="180x180" href="/favicon.ico">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<link rel="canonical" href="https://reefwith.me/changelog">
<meta property="og:type" content="website">
<meta property="og:site_name" content="reef">
<meta property="og:url" content="https://reefwith.me/changelog">
<meta property="og:title" content="What's new in reef">
<meta property="og:description" content="What has changed in reef, newest first.">
<meta property="og:image" content="https://reefwith.me/site/og-card.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="The reef share card: the curled brain-coral mark beside the words &quot;Memories you grow together&quot;, on deep teal.">
<meta name="twitter:card" content="summary_large_image">
<style>
@font-face {
  font-family: "Nunito";
  src: url("/site/nunito-latin.woff2") format("woff2");
  font-weight: 200 1000;
  font-style: normal;
  font-display: swap;
}
:root {
  --ground: #fbfcfd; --panel: #f2f7f8; --hairline: #e5edf0;
  --ink: #1c2b33; --muted: #7b8a92;
  --accent: #0d9488;
}
@media (prefers-color-scheme: dark) {
  :root {
    --ground: #0d1a20; --panel: #0f2129; --hairline: #1c333d;
    --ink: #e2f1f5; --muted: #8fb0ba;
    --accent: #38bdd8;
  }
}
body {
  margin: 0 auto; padding: 3rem 1.25rem 6rem; max-width: 42rem;
  font-family: "Nunito", system-ui, sans-serif; font-size: 1.0625rem;
  line-height: 1.6; color: var(--ink); background: var(--ground);
}
h1 { font-size: 1.9rem; font-weight: 800; margin: 0 0 2.5rem; }
h1 a { color: var(--accent); text-decoration: none; }
section { border-top: 1px solid var(--hairline); padding-top: 1.5rem;
  margin-top: 2.5rem; }
h2 { font-size: 1.25rem; font-weight: 800; margin: 0 0 1rem;
  display: flex; align-items: baseline; gap: 0.75rem; }
h2 .date { font-size: 0.875rem; font-weight: 500; color: var(--muted); }
h3 { font-size: 0.8125rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--accent); margin: 1.25rem 0 0.5rem; }
ul { margin: 0; padding-left: 1.25rem; }
li { margin-bottom: 0.4rem; }
.empty { color: var(--muted); }
</style>
</head>
<body>
<h1>What's new in <a href="/">reef</a></h1>
{{body}}
</body>
</html>
"""


def render_page(entries: list[Entry]) -> str:
    """Render the public changelog page.

    A whole standalone document rather than a fragment: it is served as a
    static file by ``GET /changelog`` beside ``index.html``, with no
    template engine and no build step anywhere in the site's chain.

    Every value is escaped. Fragments are prose typed by a person, and
    prose contains ``&`` and angle brackets.

    :param entries: the feed, newest first
    :returns: the complete HTML document
    """
    if entries:
        body = "\n".join(_render_entry(entry) for entry in entries)
    else:
        body = "<p class='empty'>Nothing to report yet — check back after the "
        body += "next release.</p>"
    return _PAGE.replace("{{body}}", body)


def _render_entry(entry: Entry) -> str:
    """Render one release as a section.

    :param entry: the release to render
    :returns: the section's HTML
    """
    parts = [
        (
            f"<section><h2>{escape(entry.version)}"
            f"<span class='date'>{escape(entry.date)}</span></h2>"
        )
    ]
    for kind in KINDS:
        texts = [change.text for change in entry.changes if change.kind == kind]
        if not texts:
            continue
        parts.append(f"<h3>{_HEADINGS[kind]}</h3><ul>")
        parts.extend(f"<li>{escape(text)}</li>" for text in texts)
        parts.append("</ul>")
    parts.append("</section>")
    return "".join(parts)


def prepend_changelog(notes: str, changelog: Path) -> None:
    """Put one release's notes at the top of the changelog file.

    semantic-release's generator emits a complete section -- ``## [X.Y.Z]``
    header, compare link, grouped commits -- so this only stacks sections
    newest-first, exactly as ``@semantic-release/changelog`` used to before
    the release moved into a pull request.

    :param notes: the section for the new release, as generated
    :param changelog: path to ``CHANGELOG.md``; created if missing
    :raises FragmentError: if ``notes`` is blank -- an empty section means
        the dry run upstream produced nothing, and writing it would put an
        empty heading at the top of the public changelog
    """
    if not notes.strip():
        raise FragmentError("refusing to prepend empty release notes")
    existing = changelog.read_text(encoding="utf-8") if changelog.exists() else ""
    body = notes.strip() + "\n"
    if existing:
        body += "\n" + existing
    changelog.write_text(body, encoding="utf-8")
