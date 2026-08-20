# Release Feature Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give reef real version numbers computed from conventional commits, and tell users what shipped in a register they understand.

**Architecture:** One merge produces two records. semantic-release derives the version, the tag, and `CHANGELOG.md` from conventional commits. Separately, hand-written fragments under `changes/` fold into `site/release-notes.json`, which feeds a public page and an in-app panel with an unread marker. The marker rides on a new nullable column on `persons`, which is already under `persons_self_select` and `persons_self_update`.

**Tech Stack:** Python 3.13 / Piccolo / Starlette-on-FastMCP, React + TypeScript (bun), semantic-release on GitHub Actions, Postgres with RLS.

**Spec:** `docs/specs/2026-08-17-release-feature-tracking-design.md`

## Global Constraints

- **Docstrings are mandatory** on every Python module, class and function, ReST-formatted, no types in the docstring (type hints carry those).
- **Modern Python types** — `str | None`, not `Optional[str]`.
- **Lint and format both gate CI:** `uv run ruff check src tests` *and* `uv run ruff format --check src tests`. Lint-clean is not enough; run `just fmt` before every commit.
- **Only `src` and `tests` are linted.** Logic goes in `src/reef/`, not `scripts/` — `scripts/` is a thin CLI shell only, and is not on the pytest `pythonpath`.
- **The Dockerfile copies `src`, `scripts`, `site`, `clients/python`, `piccolo_conf.py` — not `docs`.** Anything the running server must read lives under one of those.
- **`kind` is exactly one of `added`, `changed`, `fixed`.** An unknown kind is an error, never a default.
- **Semver comparison exists in Python only.** The frontend consumes the `unread` boolean and never parses a version.
- **RLS is tested, never assumed.** Anything touching `persons` gets a test proving a second person cannot read or write it.
- **User-facing copy follows ISO 24495-1:** lead with what matters to the reader, one idea per sentence, everyday words, active voice.
- **Tests share one Postgres across worktrees.** Run them via `just test-py` (which takes a machine-wide lock), never bare `pytest`. A screenful of unrelated failures usually means a concurrent run — retry before debugging.
- **Do not use the name `whats_new` / `whatsnew` for anything in this feature.** `main` shipped a different feature under that name (PR #59): `rif.activity.whats_new` is an MCP tool reporting *page activity in your coves*, with its own `tests/test_whats_new.py`. This feature is **release notes** — `rif.releasenotes`, `tests/test_release_notes.py`, `site/release-notes.json`, `/api/release-notes`, `ReleaseNotes.tsx`, `useReleaseNotes`. The two are unrelated, and a module name one underscore apart from an existing one is how the wrong import gets made. **User-facing copy is the exception:** the menu item, panel heading and public page still read "What's new", because that is the right phrase for a reader and the shipped tool never appears in the web UI.
- **Commit subjects use conventional-commit prefixes from Task 1 onward** (`feat:`, `fix:`, `docs:`, `ci:`, `test:`, `chore:`), keeping this repo's narrative style after the prefix.

---

### Task 1: The `last_seen_release` column

**Files:**
- Modify: `src/reef/models.py` (the `Person` table, after `session_epoch`)
- Create: `src/reef/piccolo_migrations/rif_2026_08_17t10_00_00_000000.py`
- Test: `tests/test_schema.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `Person.last_seen_release` — a `Varchar(null=True, default=None)` column holding a version string like `"0.4.0"`, or `NULL` for a person who has never opened the panel.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_schema.py`:

```python
async def test_last_seen_release_starts_empty_and_is_writable_by_its_owner(
    household, seed
):
    """A person owns their own read-marker: NULL until they open the panel.

    NULL rather than ``''`` because "never seen anything" and "seen version
    empty-string" are different states, and only one of them should light
    the dot for somebody who predates the feature.
    """
    person_id = household["wouter"].id
    assert (
        await seed.fetchval(
            "SELECT last_seen_release FROM persons WHERE id = $1", person_id
        )
        is None
    )
    await seed.execute(
        "UPDATE persons SET last_seen_release = '0.4.0' WHERE id = $1", person_id
    )
    assert (
        await seed.fetchval(
            "SELECT last_seen_release FROM persons WHERE id = $1", person_id
        )
        == "0.4.0"
    )
```

- [ ] **Step 2: Run it and watch it fail**

Run: `just test-py tests/test_schema.py -k last_seen_release`
Expected: FAIL — `UndefinedColumnError: column "last_seen_release" does not exist`.

- [ ] **Step 3: Add the column to the model**

In `src/reef/models.py`, inside `class Person`, immediately after the `session_epoch` line:

```python
    #: Version whose "what's new" this person has already read, or ``None``
    #: for somebody who has never opened the panel -- including everybody
    #: who predates it, who should see the dot exactly once. A column on
    #: ``persons`` rather than a table of its own: ``persons_self_select``
    #: and ``persons_self_update`` already say "yours and only yours", so
    #: this inherits the rule instead of restating it, and nothing has to
    #: be added to ``enable_statements``.
    last_seen_release = Varchar(null=True, default=None)
```

- [ ] **Step 4: Write the migration**

Create `src/reef/piccolo_migrations/rif_2026_08_17t10_00_00_000000.py`:

```python
"""Remember which release each person has already read about."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from rif.db import run_ddl_atomically

ID = "2026-08-17T10:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "persons.last_seen_release, for the what's-new marker"


async def forwards() -> MigrationManager:
    """Add the per-person read marker for the what's-new panel.

    A plain column on an existing table, deliberately: ``persons`` is
    already self-only under ``persons_self_select`` and
    ``persons_self_update``, so the new value is covered by policies that
    predate it. Nothing is added to :func:`rif.rls.enable_statements` --
    a new table would have needed policy DDL there, and putting policy DDL
    for a *new* table into that function is what broke fresh builds twice.

    ``IF NOT EXISTS`` so a database that already ran this by hand, or a
    re-run of a partially applied chain, is not an error.

    :returns: configured migration manager
    """
    manager = MigrationManager(migration_id=ID, app_name="rif", description=DESCRIPTION)

    async def run() -> None:
        await run_ddl_atomically(
            ["ALTER TABLE persons ADD COLUMN IF NOT EXISTS last_seen_release VARCHAR"]
        )

    manager.add_raw(run)
    return manager
```

- [ ] **Step 5: Run the test and watch it pass**

Run: `just test-py tests/test_schema.py -k last_seen_release`
Expected: PASS.

- [ ] **Step 6: Prove the migration chain still builds an empty database**

The suite builds its schema from the models, so it would pass even if the migration were broken. Exercise the chain itself:

```bash
just db-reset-test
just migrate
```

Expected: the chain runs to completion with no error, and `just psql` → `\d persons` lists `last_seen_release`. Then `just test-py` to confirm the suite still passes against the rebuilt database.

- [ ] **Step 7: Format, lint, commit**

```bash
just fmt
uv run ruff check src tests
git add src/reef/models.py src/reef/piccolo_migrations/rif_2026_08_17t10_00_00_000000.py tests/test_schema.py
git commit -m "feat(whats-new): remember which release each person has read"
```

---

### Task 2: Fragment parsing, the fold, and the unread rule

**Files:**
- Create: `src/reef/releasenotes.py`
- Test: `tests/test_release_notes.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `KINDS: tuple[str, ...]` — `("added", "changed", "fixed")`
  - `class FragmentError(Exception)`
  - `Change` — frozen dataclass, fields `kind: str`, `text: str`
  - `Entry` — frozen dataclass, fields `version: str`, `date: str`, `changes: list[Change]`
  - `parse_fragment(raw: str) -> Change`
  - `read_fragments(directory: Path) -> list[Change]`
  - `load_feed(path: Path) -> list[Entry]`
  - `write_feed(path: Path, entries: list[Entry]) -> None`
  - `fold(fragments_dir: Path, feed_path: Path, version: str, date: str) -> Entry | None`
  - `parse_version(version: str) -> tuple[int, int, int]`
  - `is_unread(entries: list[Entry], last_seen: str | None) -> bool`
  - `feed_as_json(entries: list[Entry]) -> dict` — the API's response shape

- [ ] **Step 1: Write the failing tests**

Create `tests/test_release_notes.py`:

```python
"""Fragments, the fold that consumes them, and what counts as unread."""

import json
from pathlib import Path

import pytest

from rif.releasenotes import (
    Change,
    Entry,
    FragmentError,
    feed_as_json,
    fold,
    is_unread,
    load_feed,
    parse_fragment,
    parse_version,
    read_fragments,
)

FRAGMENT = """---
kind: added
---
Search your pages from your assistant.
"""


def test_a_fragment_parses_to_a_change():
    assert parse_fragment(FRAGMENT) == Change(
        kind="added", text="Search your pages from your assistant."
    )


def test_a_multi_line_body_becomes_one_paragraph():
    """A wrapped body is prose, not two sentences on two lines."""
    raw = "---\nkind: fixed\n---\nOne line,\nand its continuation.\n"
    assert parse_fragment(raw).text == "One line, and its continuation."


def test_an_unknown_kind_is_refused():
    with pytest.raises(FragmentError, match="improved"):
        parse_fragment("---\nkind: improved\n---\nSomething.\n")


def test_a_fragment_without_front_matter_is_refused():
    with pytest.raises(FragmentError, match="front matter"):
        parse_fragment("Just a sentence.\n")


def test_a_fragment_without_a_body_is_refused():
    with pytest.raises(FragmentError, match="no text"):
        parse_fragment("---\nkind: added\n---\n\n")


def test_fragments_are_read_in_kind_order_then_filename(tmp_path: Path):
    """Grouped by kind so the reader meets what is new before what is fixed."""
    (tmp_path / "20-b.md").write_text("---\nkind: fixed\n---\nB.\n")
    (tmp_path / "10-a.md").write_text("---\nkind: added\n---\nA.\n")
    (tmp_path / "30-c.md").write_text("---\nkind: added\n---\nC.\n")
    assert read_fragments(tmp_path) == [
        Change(kind="added", text="A."),
        Change(kind="added", text="C."),
        Change(kind="fixed", text="B."),
    ]


def test_reading_an_empty_directory_yields_nothing(tmp_path: Path):
    assert read_fragments(tmp_path) == []


def test_the_directorys_own_readme_is_not_a_fragment(tmp_path: Path):
    """changes/ ships instructions for contributors; prose is not an entry."""
    (tmp_path / "README.md").write_text("# What to write here\n\nProse.\n")
    (tmp_path / "57-search.md").write_text(FRAGMENT)
    assert read_fragments(tmp_path) == [
        Change(kind="added", text="Search your pages from your assistant.")
    ]


def test_a_readme_alone_folds_to_nothing(tmp_path: Path):
    """The common case: a release where nobody wrote a fragment."""
    (tmp_path / "README.md").write_text("# What to write here\n\nProse.\n")
    assert read_fragments(tmp_path) == []


def test_a_missing_feed_reads_as_no_entries(tmp_path: Path):
    """The first release must not need a file somebody remembered to create."""
    assert load_feed(tmp_path / "absent.json") == []


def test_the_fold_prepends_and_consumes(tmp_path: Path):
    fragments = tmp_path / "changes"
    fragments.mkdir()
    (fragments / "57-search.md").write_text(FRAGMENT)
    feed = tmp_path / "release-notes.json"

    entry = fold(fragments, feed, version="0.4.0", date="2026-08-17")

    assert entry == Entry(
        version="0.4.0",
        date="2026-08-17",
        changes=[Change(kind="added", text="Search your pages from your assistant.")],
    )
    assert json.loads(feed.read_text())["entries"][0]["version"] == "0.4.0"
    assert list(fragments.iterdir()) == []


def test_the_newest_entry_comes_first(tmp_path: Path):
    fragments = tmp_path / "changes"
    fragments.mkdir()
    feed = tmp_path / "release-notes.json"

    (fragments / "1-a.md").write_text("---\nkind: added\n---\nFirst.\n")
    fold(fragments, feed, version="0.4.0", date="2026-08-17")
    (fragments / "2-b.md").write_text("---\nkind: added\n---\nSecond.\n")
    fold(fragments, feed, version="0.5.0", date="2026-08-18")

    versions = [e["version"] for e in json.loads(feed.read_text())["entries"]]
    assert versions == ["0.5.0", "0.4.0"]


def test_a_release_with_no_fragments_writes_nothing(tmp_path: Path):
    """A patch nobody notices should not announce itself."""
    fragments = tmp_path / "changes"
    fragments.mkdir()
    feed = tmp_path / "release-notes.json"

    assert fold(fragments, feed, version="0.4.1", date="2026-08-17") is None
    assert not feed.exists()


def test_versions_compare_as_numbers_not_strings():
    assert parse_version("0.10.0") > parse_version("0.9.0")


def test_a_person_who_has_seen_nothing_has_unread(tmp_path: Path):
    entries = [Entry(version="0.4.0", date="2026-08-17", changes=[])]
    assert is_unread(entries, None) is True


def test_seeing_the_newest_clears_unread():
    entries = [Entry(version="0.4.0", date="2026-08-17", changes=[])]
    assert is_unread(entries, "0.4.0") is False


def test_an_older_mark_is_unread_across_a_ten():
    """The case string comparison gets wrong."""
    entries = [Entry(version="0.10.0", date="2026-08-18", changes=[])]
    assert is_unread(entries, "0.9.0") is True


def test_an_empty_feed_is_never_unread():
    assert is_unread([], None) is False


def test_an_unparseable_mark_reads_as_never_seen():
    """A hand-edited or truncated value must not crash the panel."""
    entries = [Entry(version="0.4.0", date="2026-08-17", changes=[])]
    assert is_unread(entries, "not-a-version") is True


def test_the_api_shape_is_plain_json():
    entries = [
        Entry(
            version="0.4.0",
            date="2026-08-17",
            changes=[Change(kind="added", text="A.")],
        )
    ]
    assert feed_as_json(entries) == {
        "entries": [
            {
                "version": "0.4.0",
                "date": "2026-08-17",
                "changes": [{"kind": "added", "text": "A."}],
            }
        ]
    }
```

- [ ] **Step 2: Run them and watch them fail**

Run: `just test-py tests/test_release_notes.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'rif.releasenotes'`.

- [ ] **Step 3: Write the module**

Create `src/reef/releasenotes.py`:

```python
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
        # Same exclusion as read_fragments: the directory's instructions are
        # not a fragment, and a release must not delete them.
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
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `just test-py tests/test_release_notes.py`
Expected: PASS, all 17.

- [ ] **Step 5: Format, lint, commit**

```bash
just fmt
uv run ruff check src tests
git add src/reef/releasenotes.py tests/test_release_notes.py
git commit -m "feat(whats-new): fragments, the fold that consumes them, and the unread rule"
```

---

### Task 3: The `changes/` directory and the fold CLI

**Files:**
- Create: `changes/README.md`
- Create: `changes/.gitkeep`
- Create: `scripts/fold_changes.py`

**Interfaces:**
- Consumes: `rif.releasenotes.fold` from Task 2.
- Produces: `python scripts/fold_changes.py <version> <date>` — run by semantic-release in Task 8. Exits 0 on success (including "nothing to fold"), 1 with the error on a malformed fragment.

- [ ] **Step 1: Create the directory and its instructions**

`changes/README.md` — this is what a contributor reads, so it follows the plain-language rule:

```markdown
# What to write here

Add a file here when your change is something a **person using reef** would
notice. Most changes are not: test isolation, CI tuning and refactors never
need one.

Name it for your PR number, so two branches never collide:
`changes/57-search-pages.md`.

```markdown
---
kind: added
---
Search your pages from your assistant — ask for anything you've written
down and reef finds it.
```

`kind` is one of `added`, `changed`, `fixed`. Nothing else is accepted.

Write the body for the person reading it, not for us:

- Say what they can now do, not what we built.
- One or two sentences, present tense, no identifiers.
- No `search_pages`, no RLS, no worktrees. "Search your pages", not
  "search_pages: RLS-scoped full-text search".

At release time these files are folded into `site/release-notes.json` and
deleted. That file feeds the public changelog and the "What's new" panel in
the app.

If your PR genuinely changes nothing a user would notice, add the
`no-changelog` label instead and CI will let it through.
```

`changes/.gitkeep` — empty file, so the directory survives a release that empties it.

- [ ] **Step 2: Write the CLI**

Create `scripts/fold_changes.py`:

```python
"""Fold this release's fragments into the published feed.

Invoked by semantic-release's ``prepareCmd`` with the version it computed,
so the entry carries the same number as the tag. All the logic lives in
:mod:`rif.releasenotes` -- only ``src`` and ``tests`` are linted and tested, so
this file stays a shell around it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rif.releasenotes import FragmentError, fold  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str]) -> int:
    """Fold ``changes/`` into ``site/release-notes.json`` for one release.

    :param argv: ``[version, date]`` -- the version semantic-release
        computed and the release date as ``YYYY-MM-DD``
    :returns: a process exit status
    """
    if len(argv) != 2:
        print("usage: fold_changes.py <version> <YYYY-MM-DD>", file=sys.stderr)
        return 2
    version, date = argv
    try:
        entry = fold(ROOT / "changes", ROOT / "site" / "release-notes.json", version, date)
    except FragmentError as error:
        # Fail the release rather than drop somebody's sentence silently.
        print(f"changelog fragment is unreadable: {error}", file=sys.stderr)
        return 1
    if entry is None:
        print(f"{version}: nothing a user would notice; no entry written")
    else:
        print(f"{version}: wrote {len(entry.changes)} change(s) to the feed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 3: Run it by hand against a throwaway fragment**

```bash
printf -- '---\nkind: added\n---\nSearch your pages from your assistant.\n' > changes/0-smoke.md
python3 scripts/fold_changes.py 0.0.1 2026-08-17
cat site/release-notes.json
```

Expected: prints `0.0.1: wrote 1 change(s) to the feed`, `site/release-notes.json` holds the entry, and `changes/0-smoke.md` is gone.

- [ ] **Step 4: Check the empty case, then undo the smoke test**

```bash
python3 scripts/fold_changes.py 0.0.2 2026-08-17
rm site/release-notes.json
git status --short
```

Expected: prints `0.0.2: nothing a user would notice; no entry written`, and `git status` shows only the new untracked files from this task — no leftover smoke fragment, no feed.

- [ ] **Step 5: Commit**

```bash
git add changes/README.md changes/.gitkeep scripts/fold_changes.py
git commit -m "feat(whats-new): a place to write the sentence, and the fold that collects it"
```

---

### Task 4: The API endpoints

**Files:**
- Modify: `src/reef/web/routes_api.py` (handlers near `_appearances`; registration in `register_api_routes`)
- Create: `tests/test_release_notes_api.py`

**Interfaces:**
- Consumes: `Person.last_seen_release` (Task 1), `rif.releasenotes.{load_feed, feed_as_json, is_unread}` (Task 2).
- Produces:
  - `GET /api/release-notes` → `{"entries": [...], "unread": bool}`
  - `POST /api/release-notes/seen` → `{"unread": false}`, requires the `X-Rif-Csrf: 1` header.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_release_notes_api.py`:

```python
"""The what's-new feed: what it shows, and whose mark it moves."""

import json

import pytest
from conftest import _login

from rif.config import get_settings

CSRF = {"X-Rif-Csrf": "1"}

FEED = {
    "entries": [
        {
            "version": "0.10.0",
            "date": "2026-08-18",
            "changes": [
                {"kind": "added", "text": "Search your pages from your assistant."}
            ],
        },
        {
            "version": "0.9.0",
            "date": "2026-08-01",
            "changes": [{"kind": "fixed", "text": "Pictures upload again."}],
        },
    ]
}


@pytest.fixture
def feed(tmp_path, monkeypatch):
    """Point the server's site directory at a temporary feed.

    :param tmp_path: pytest's per-test directory
    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the directory the feed was written into
    """
    (tmp_path / "release-notes.json").write_text(json.dumps(FEED))
    # site_dir is a str on Settings, not a Path -- see src/reef/config.py.
    monkeypatch.setattr(get_settings(), "site_dir", str(tmp_path))
    return tmp_path


async def test_the_feed_is_returned_newest_first(api, world, feed):
    alice, _bob, _ = world
    _login(api, alice)
    response = await api.get("/api/release-notes")
    assert response.status_code == 200
    body = response.json()
    assert [entry["version"] for entry in body["entries"]] == ["0.10.0", "0.9.0"]


async def test_a_person_who_has_read_nothing_has_unread(api, world, feed):
    alice, _bob, _ = world
    _login(api, alice)
    assert (await api.get("/api/release-notes")).json()["unread"] is True


async def test_marking_seen_clears_it(api, world, feed):
    alice, _bob, _ = world
    _login(api, alice)
    stamped = await api.post("/api/release-notes/seen", headers=CSRF)
    assert stamped.status_code == 200
    assert stamped.json() == {"unread": False}
    assert (await api.get("/api/release-notes")).json()["unread"] is False


async def test_marking_seen_twice_is_harmless(api, world, feed):
    alice, _bob, _ = world
    _login(api, alice)
    await api.post("/api/release-notes/seen", headers=CSRF)
    again = await api.post("/api/release-notes/seen", headers=CSRF)
    assert again.status_code == 200
    assert (await api.get("/api/release-notes")).json()["unread"] is False


async def test_an_older_mark_is_still_unread(api, world, feed, seed):
    """0.9.0 is older than 0.10.0 -- the case a string compare gets wrong."""
    alice, _bob, _ = world
    await seed.execute(
        "UPDATE persons SET last_seen_release = '0.9.0' WHERE id = $1", alice.id
    )
    _login(api, alice)
    assert (await api.get("/api/release-notes")).json()["unread"] is True


async def test_a_missing_feed_is_empty_rather_than_broken(
    api, world, tmp_path, monkeypatch
):
    """Before the first release there is no file, and the panel must open."""
    monkeypatch.setattr(get_settings(), "site_dir", str(tmp_path))
    alice, _bob, _ = world
    _login(api, alice)
    response = await api.get("/api/release-notes")
    assert response.status_code == 200
    assert response.json() == {"entries": [], "unread": False}


async def test_another_persons_marker_is_invisible_and_unwritable(tx, household, seed):
    """Attempt what the handlers never do: name another person's row.

    The endpoint tests below cannot prove the policy. Both handlers scope
    every query to ``principal.person_id``, so they touch one row by
    construction and would pass with RLS switched off entirely. This test
    queries the other person's row directly, which only ``persons_self_select``
    and ``persons_self_update`` can stop -- so it is the one that fails if
    either policy regresses to permissive.
    """
    wouter, partner = household["wouter"], household["partner"]
    await seed.execute(
        "UPDATE persons SET last_seen_release = '0.4.0' WHERE id = $1", partner.id
    )
    await arm(Principal(person_id=wouter.id, email=wouter.email))

    # Invisible on read: the row exists, and the policy hides it.
    assert await Person.objects().where(Person.id == partner.id) == []

    # Unwritable: the UPDATE matches no row rather than raising.
    await Person.update({Person.last_seen_release: "9.9.9"}).where(
        Person.id == partner.id
    )
    assert (
        await seed.fetchval(
            "SELECT last_seen_release FROM persons WHERE id = $1", partner.id
        )
        == "0.4.0"
    )


async def test_one_persons_mark_does_not_move_anothers(api, world, feed, seed):
    """Each person's mark moves independently through the endpoints.

    This is application-level scoping, not the RLS proof -- see
    :func:`test_another_persons_marker_is_invisible_and_unwritable` for that.
    """
    alice, bob, _ = world
    _login(api, alice)
    await api.post("/api/release-notes/seen", headers=CSRF)

    _login(api, bob)
    assert (await api.get("/api/release-notes")).json()["unread"] is True

    # Read past the policies to prove the write landed on exactly one row.
    assert (
        await seed.fetchval(
            "SELECT last_seen_release FROM persons WHERE id = $1", alice.id
        )
        == "0.10.0"
    )
    assert (
        await seed.fetchval(
            "SELECT last_seen_release FROM persons WHERE id = $1", bob.id
        )
        is None
    )


async def test_marking_seen_needs_the_csrf_header(api, world, feed):
    alice, _bob, _ = world
    _login(api, alice)
    assert (await api.post("/api/release-notes/seen")).status_code == 403


async def test_a_stranger_gets_nothing(api, feed):
    assert (await api.get("/api/release-notes")).status_code == 401
```

- [ ] **Step 2: Run them and watch them fail**

Run: `just test-py tests/test_release_notes_api.py`
Expected: FAIL — 404s, because the routes do not exist yet.

- [ ] **Step 3: Add the handlers**

In `src/reef/web/routes_api.py`, add to the imports:

```python
from rif.releasenotes import feed_as_json, is_unread, load_feed
```

Then add these handlers immediately after `_set_appearance`:

```python
def _feed_path() -> Path:
    """Where the published feed lives.

    Under ``site/`` rather than ``docs/`` because the Dockerfile copies
    ``site`` and not ``docs`` -- a feed the running server cannot open
    would leave the panel permanently empty in production while passing
    every test locally.

    ``site_dir`` is a ``str`` on ``Settings``, so it is wrapped here the
    same way :mod:`rif.web.static` wraps it.

    :returns: the path to ``release-notes.json``
    """
    return Path(get_settings().site_dir) / "release-notes.json"


async def _release_notes(request: Request, principal: Principal) -> dict:
    """Return what shipped, and whether this person has read it.

    ``unread`` is computed here rather than in the browser so the rule
    exists once: the dot and the list it decorates are then answering the
    same question.

    :param request: the incoming request, unused
    :param principal: the authenticated person
    :returns: the entries, newest first, and the unread flag
    """
    entries = load_feed(_feed_path())
    person = await Person.objects().where(Person.id == principal.person_id).first()
    last_seen = person.last_seen_release if person else None
    return {**feed_as_json(entries), "unread": is_unread(entries, last_seen)}


async def _mark_release_notes_seen(request: Request, principal: Principal) -> dict:
    """Stamp this person as having read up to the newest entry.

    Stamps the newest *version* rather than a timestamp: the question asked
    on read is "is there something newer than what you saw", and a version
    answers it without depending on clocks agreeing.

    :param request: the incoming request, unused
    :param principal: the authenticated person
    :returns: the cleared flag
    """
    entries = load_feed(_feed_path())
    if entries:
        await Person.update({Person.last_seen_release: entries[0].version}).where(
            Person.id == principal.person_id
        )
    return {"unread": False}
```

If `Path` is not already imported in this module, add `from pathlib import Path`.

- [ ] **Step 4: Register the routes**

In `register_api_routes`, immediately after the appearance routes:

```python
mcp.custom_route("/api/release-notes", methods=["GET"])(api(_release_notes))
mcp.custom_route("/api/release-notes/seen", methods=["POST"])(
    api(_mark_release_notes_seen)
)
```

- [ ] **Step 5: Run the tests and watch them pass**

Run: `just test-py tests/test_release_notes_api.py`
Expected: PASS, all 9.

- [ ] **Step 6: Run the whole backend suite**

Run: `just test-py`
Expected: PASS. The route registration is idempotent but shared across tests — a failure here means the new routes leaked into another test's expectations.

- [ ] **Step 7: Format, lint, commit**

```bash
just fmt
uv run ruff check src tests
git add src/reef/web/routes_api.py tests/test_release_notes_api.py
git commit -m "feat(release-notes): serve the feed, and remember who has read it"
```

---

### Task 5: The public changelog page

**Files:**
- Modify: `src/reef/releasenotes.py` (add `render_page`)
- Modify: `scripts/fold_changes.py` (write the page after the fold)
- Test: `tests/test_release_notes.py` (append)

**Interfaces:**
- Consumes: `Entry`, `Change`, `load_feed` (Task 2).
- Produces: `render_page(entries: list[Entry]) -> str` — a complete standalone HTML document written to `site/changelog.html`.

- [ ] **Step 1: Read the page it has to match**

Open `site/index.html` and check the `@font-face` block and the `:root` custom properties against the ones in Step 4's template. If the palette has moved since this plan was written, take the file's values — it is the designed system, and this page sits inside it. Do not invent colours.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_release_notes.py`:

```python
def test_the_page_lists_every_version_newest_first():
    from rif.releasenotes import render_page

    html = render_page(
        [
            Entry(
                version="0.5.0",
                date="2026-08-18",
                changes=[Change(kind="added", text="Second.")],
            ),
            Entry(
                version="0.4.0",
                date="2026-08-17",
                changes=[Change(kind="fixed", text="First.")],
            ),
        ]
    )
    assert html.index("0.5.0") < html.index("0.4.0")
    assert "Second." in html and "First." in html
    assert html.startswith("<!doctype html>")


def test_the_page_escapes_the_text_it_is_given():
    """Fragments are prose written by hand, and prose contains angle brackets."""
    from rif.releasenotes import render_page

    html = render_page(
        [
            Entry(
                version="0.4.0",
                date="2026-08-17",
                changes=[Change(kind="added", text="Use <b>bold</b> & live.")],
            )
        ]
    )
    assert "&lt;b&gt;" in html
    assert "<b>bold</b>" not in html


def test_an_empty_feed_still_renders_a_page():
    """Before the first release the page must not look broken."""
    from rif.releasenotes import render_page

    html = render_page([])
    assert html.startswith("<!doctype html>")
    assert "Nothing to report yet" in html
```

- [ ] **Step 3: Run them and watch them fail**

Run: `just test-py tests/test_release_notes.py -k page`
Expected: FAIL — `ImportError: cannot import name 'render_page'`.

- [ ] **Step 4: Add the renderer**

Add to `src/reef/releasenotes.py` (and add `from html import escape` to its imports):

```python
#: How each kind is announced on the public page. The reader is told what
#: happened to them, not which enum member we filed it under.
_HEADINGS = {"added": "New", "changed": "Changed", "fixed": "Fixed"}


def render_page(entries: list[Entry]) -> str:
    """Render the public changelog page.

    A whole standalone document rather than a fragment: it is served as a
    static file by ``GET /site/{path}`` beside ``index.html``, with no
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
        f"<section><h2>{escape(entry.version)}"
        f"<span class='date'>{escape(entry.date)}</span></h2>"
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
```

And the `_PAGE` constant it fills in. The `@font-face` and `:root` blocks are copied verbatim from `site/index.html` — same font file, same palette, no new colours:

```python
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
```

- [ ] **Step 5: Run the tests and watch them pass**

Run: `just test-py tests/test_release_notes.py`
Expected: PASS.

- [ ] **Step 6: Write the page from the fold CLI**

In `scripts/fold_changes.py`, extend the import to `from rif.releasenotes import FragmentError, fold, load_feed, render_page` and, after a successful fold, replace the `if entry is None:` block with:

```python
    if entry is None:
        print(f"{version}: nothing a user would notice; no entry written")
        return 0
    feed_path = ROOT / "site" / "release-notes.json"
    (ROOT / "site" / "changelog.html").write_text(
        render_page(load_feed(feed_path)), encoding="utf-8"
    )
    print(f"{version}: wrote {len(entry.changes)} change(s) to the feed and the page")
    return 0
```

Hoist `feed_path = ROOT / "site" / "release-notes.json"` above the `fold(...)` call and pass it in, so the path is named once.

- [ ] **Step 7: Look at the page in a browser**

```bash
printf -- '---\nkind: added\n---\nSearch your pages from your assistant.\n' > changes/0-smoke.md
python3 scripts/fold_changes.py 0.0.1 2026-08-17
open site/changelog.html
```

Expected: a page that looks like it belongs beside `site/index.html` — same font, same palette, readable at phone width. Then undo:

```bash
rm site/release-notes.json site/changelog.html
git status --short
```

- [ ] **Step 8: Format, lint, commit**

```bash
just fmt
uv run ruff check src tests
git add src/reef/releasenotes.py scripts/fold_changes.py tests/test_release_notes.py
git commit -m "feat(release-notes): a public page for what shipped"
```

---

### Task 6: The in-app panel and its marker

**Files:**
- Modify: `frontend/src/types.ts`
- Create: `frontend/src/useReleaseNotes.ts`
- Create: `frontend/src/components/ReleaseNotes.tsx`
- Create: `frontend/src/components/ReleaseNotes.test.tsx`
- Modify: `frontend/src/components/AppShell.tsx`
- Modify: `frontend/src/components/AccountMenu.tsx`
- Modify: `frontend/src/components/AccountMenu.test.tsx`
- Modify: `frontend/src/app.css`

**Interfaces:**
- Consumes: `GET /api/release-notes`, `POST /api/release-notes/seen` (Task 4).
- Produces:
  - `types.ts`: `Change { kind: "added" | "changed" | "fixed"; text: string }`, `ReleaseEntry { version: string; date: string; changes: Change[] }`, `ReleaseNotesFeed { entries: ReleaseEntry[]; unread: boolean }`
  - `useReleaseNotes.ts`: `ReleaseNotesContextValue { unread: boolean; openReleaseNotes(): void }`, `ReleaseNotesContext`, `useReleaseNotes()`
  - `ReleaseNotes.tsx`: `ReleaseNotes({ entries, onClose }: { entries: ReleaseEntry[]; onClose(): void })`

- [ ] **Step 1: Add the types**

Append to `frontend/src/types.ts`:

```ts
/** One line in a release's "what's new", in the words a reader uses. */
export interface Change {
  kind: "added" | "changed" | "fixed";
  text: string;
}

/** One release, as `GET /api/release-notes` reports it. */
export interface ReleaseEntry {
  version: string;
  date: string;
  changes: Change[];
}

/**
 * `GET /api/release-notes` — what shipped, and whether this reader has seen it.
 *
 * `unread` is computed by the backend. The frontend never compares versions:
 * "0.10.0" < "0.9.0" as strings, and one rule in one place is how the dot
 * and the list stay in agreement.
 */
export interface ReleaseNotesFeed {
  entries: ReleaseEntry[];
  unread: boolean;
}
```

- [ ] **Step 2: Write the failing panel test**

Create `frontend/src/components/ReleaseNotes.test.tsx`:

```tsx
/**
 * The what's-new panel: what it shows, and how it closes.
 *
 * It is a dialog, so it has to say so — a reader on a screen reader gets no
 * signal otherwise — and Escape has to close it, because every other
 * dismissible surface in the app closes that way.
 */

import { afterEach, expect, test } from "bun:test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import type { ReleaseEntry } from "../types";
import { ReleaseNotes } from "./ReleaseNotes";

afterEach(cleanup);

const ENTRIES: ReleaseEntry[] = [
  {
    version: "0.5.0",
    date: "2026-08-18",
    changes: [{ kind: "added", text: "Search your pages from your assistant." }],
  },
  {
    version: "0.4.0",
    date: "2026-08-17",
    changes: [{ kind: "fixed", text: "Pictures upload again." }],
  },
];

test("it lists every release, newest first", () => {
  render(<ReleaseNotes entries={ENTRIES} onClose={() => {}} />);
  const versions = screen.getAllByRole("heading", { level: 3 });
  expect(versions.map((h) => h.textContent)).toEqual([
    expect.stringContaining("0.5.0"),
    expect.stringContaining("0.4.0"),
  ]);
  expect(screen.getByText("Search your pages from your assistant.")).toBeDefined();
});

test("it announces itself as a dialog", () => {
  render(<ReleaseNotes entries={ENTRIES} onClose={() => {}} />);
  expect(screen.getByRole("dialog")).toBeDefined();
});

test("Escape closes it", () => {
  let closed = false;
  render(<ReleaseNotes entries={ENTRIES} onClose={() => (closed = true)} />);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(closed).toBe(true);
});

test("an empty feed reads as a sentence, not as a blank panel", () => {
  render(<ReleaseNotes entries={[]} onClose={() => {}} />);
  expect(screen.getByText(/nothing to report yet/i)).toBeDefined();
});
```

- [ ] **Step 3: Run it and watch it fail**

Run: `just test-js src/components/ReleaseNotes.test.tsx`
Expected: FAIL — cannot resolve `./ReleaseNotes`.

- [ ] **Step 4: Write the panel**

Read `MembersSheet.tsx` first and match its overlay and dismissal idiom rather than inventing a second one. Then create `frontend/src/components/ReleaseNotes.tsx`:

```tsx
/**
 * What shipped, for the person it shipped to.
 *
 * Read-only and shallow on purpose: a reader opens this to find out what
 * changed, not to do anything. The copy is written in `changes/*.md` by
 * whoever made the change, so this component only groups and renders — if
 * a line here reads like release notes, the fix belongs in the fragment.
 *
 * Overlay, Escape handling and focus follow `MembersSheet`, so the app has
 * one way of dismissing a surface rather than two.
 */

import { useEffect } from "react";

import type { Change, ReleaseEntry } from "../types";

/** How each kind is announced. The reader is told what happened to them. */
const HEADINGS: Record<Change["kind"], string> = {
  added: "New",
  changed: "Changed",
  fixed: "Fixed",
};

const ORDER: Change["kind"][] = ["added", "changed", "fixed"];

export function ReleaseNotes({
  entries,
  onClose,
}: {
  entries: ReleaseEntry[];
  onClose(): void;
}) {
  // Escape closes, as it does for every other dismissible surface here.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="wn-overlay" onClick={onClose}>
      <div
        className="wn-panel"
        role="dialog"
        aria-label="What's new"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="wn-head">
          <h2>What's new</h2>
          <button type="button" className="wn-close" onClick={onClose}>
            Close
          </button>
        </div>

        {entries.length === 0 ? (
          <p className="wn-empty">
            Nothing to report yet — check back after the next release.
          </p>
        ) : (
          entries.map((entry) => (
            <section key={entry.version} className="wn-entry">
              <h3>
                {entry.version}
                <span className="wn-date">{entry.date}</span>
              </h3>
              {ORDER.map((kind) => {
                const lines = entry.changes.filter((c) => c.kind === kind);
                if (lines.length === 0) return null;
                return (
                  <div key={kind}>
                    <h4 className="wn-kind">{HEADINGS[kind]}</h4>
                    <ul>
                      {lines.map((line) => (
                        <li key={line.text}>{line.text}</li>
                      ))}
                    </ul>
                  </div>
                );
              })}
            </section>
          ))
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Run the test and watch it pass**

Run: `just test-js src/components/ReleaseNotes.test.tsx`
Expected: PASS, all 4.

- [ ] **Step 6: Add the context**

Create `frontend/src/useReleaseNotes.ts`, mirroring `useMembersSheet.ts` exactly — same shape, same reason for living in its own module (`AccountMenu` is rendered by `AppShell`, so importing `AppShell` from it would be circular):

```ts
/**
 * Context for the single app-wide what's-new panel that `AppShell` owns.
 *
 * `AccountMenu` is rendered twice — once in the sidebar, once in the mobile
 * header — and both copies need the same unread flag and the same panel.
 * Fetching per copy would mean two requests and two panels; the state lives
 * in `AppShell` and reaches both through here, exactly as `useMembersSheet`
 * does for the members sheet.
 */

import { createContext, useContext } from "react";

/** What {@link useReleaseNotes} exposes: the marker, and a way to open the panel. */
export interface ReleaseNotesContextValue {
  unread: boolean;
  openReleaseNotes(): void;
}

export const ReleaseNotesContext = createContext<ReleaseNotesContextValue | null>(null);

/** The what's-new marker and opener — must be called under `AppShell`. */
export function useReleaseNotes(): ReleaseNotesContextValue {
  const value = useContext(ReleaseNotesContext);
  if (value === null) {
    throw new Error("useReleaseNotes must be used within an AppShell");
  }
  return value;
}
```

- [ ] **Step 7: Wire it into `AppShell`**

In `frontend/src/components/AppShell.tsx`, alongside the existing `sheetCove` / `appearance` state and following the same idiom.

Add the imports:

```tsx
import { ReleaseNotesContext } from "../useReleaseNotes";
import { ReleaseNotes } from "./ReleaseNotes";
import type { ReleaseNotesFeed } from "../types";
```

Add the state and the opener, beside `openMembers`:

```tsx
  const [feed, setFeed] = useState<ReleaseNotesFeed | null>(null);
  const [releaseNotesOpen, setReleaseNotesOpen] = useState(false);

  const openReleaseNotes = useCallback(() => {
    setReleaseNotesOpen(true);
    // Opening *is* reading, so the mark moves now rather than on close: a
    // reader who navigates away mid-panel has still seen it, and coming
    // back to the same dot would read as the app having lost the fact.
    apiSend("POST", "/api/release-notes/seen")
      .then(() => {
        setFeed((current) => (current ? { ...current, unread: false } : current));
      })
      .catch(() => {
        // The dot staying lit is the whole cost of a failed stamp, and the
        // next open tries again. Not worth an error surface.
      });
  }, []);

  const releaseNotesContextValue = useMemo(
    () => ({ unread: feed?.unread ?? false, openReleaseNotes }),
    [feed?.unread, openReleaseNotes],
  );
```

Fetch it on mount, beside the existing `/api/appearance` effect:

```tsx
  useEffect(() => {
    let cancelled = false;
    apiGet<ReleaseNotesFeed>("/api/release-notes")
      .then((payload) => {
        if (!cancelled) setFeed(payload);
      })
      .catch(() => {
        // The panel is not load-bearing: a failure here costs the reader a
        // list of changes, and must not cost them the app.
      });
    return () => {
      cancelled = true;
    };
  }, []);
```

Wrap the existing providers with one more, and render the panel beside `sheet`:

```tsx
        <MembersSheetContext.Provider value={sheetContextValue}>
          <ReleaseNotesContext.Provider value={releaseNotesContextValue}>
```

closing it before `</MembersSheetContext.Provider>`, and next to where `{sheet}` is rendered:

```tsx
          {releaseNotesOpen && (
            <ReleaseNotes
              entries={feed?.entries ?? []}
              onClose={() => setReleaseNotesOpen(false)}
            />
          )}
```

`apiSend` may not be imported in this file yet — check the existing import from `../api` and add it if not.

- [ ] **Step 8: Add the menu item and the dot**

In `frontend/src/components/AccountMenu.tsx`, add `import { useReleaseNotes } from "../useReleaseNotes";` and, inside the component beside the existing `useState` calls:

```tsx
  const { unread, openReleaseNotes } = useReleaseNotes();
```

Then add a menu item between "Export" and the separator:

```tsx
          <button
            type="button"
            role="menuitem"
            className="acct-item"
            onClick={() => {
              setOpen(false);
              openReleaseNotes();
            }}
          >
            What's new
            {unread && <span className="acct-dot" aria-label="unread" />}
          </button>
```

Put the same dot on the trigger button so it is visible with the menu closed — a marker nobody can see until they open the menu tells nobody anything.

- [ ] **Step 9: Extend the AccountMenu test**

`AccountMenu` now calls `useReleaseNotes()`, which throws outside a provider — so every existing render in `AccountMenu.test.tsx` must be wrapped, not just the new one. Add `import { ReleaseNotesContext } from "../useReleaseNotes";`, wrap the existing renders with a provider passing `{ unread: false, openReleaseNotes: () => {} }`, and add:

```tsx
test("the what's new item opens the panel and is marked when unread", () => {
  let opened = false;
  render(
    <MemoryRouter>
      <ReleaseNotesContext.Provider
        value={{ unread: true, openReleaseNotes: () => (opened = true) }}
      >
        <AccountMenu me={ME} />
      </ReleaseNotesContext.Provider>
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("button", { name: /Wouter/ }));
  fireEvent.click(screen.getByRole("menuitem", { name: /what's new/i }));
  expect(opened).toBe(true);
  expect(screen.getByLabelText("unread")).toBeDefined();
});
```

- [ ] **Step 10: Style the dot and the panel**

In `frontend/src/app.css`, add `.acct-dot` — a small filled circle in `var(--accent)`, positioned against the item's trailing edge — and the panel's rules beside the members sheet's. Reuse the existing custom properties; add no new colours.

- [ ] **Step 10b: Test the mark-as-seen wiring itself**

`AccountMenu`'s test passes a hand-built `openReleaseNotes` through the context, so it never reaches the real implementation. "Opening is reading" lives in `AppShell` and is the one genuinely new behaviour in this task — it needs a test that exercises it, or a regression (dropping the local `setFeed`, or making the dot depend on a refetch) passes the whole suite.

In `AppShell.test.tsx`, add `/api/release-notes` to the `responses` map returning `{ entries: [], unread: true }`, then add a test that opens the panel through the account menu and asserts both halves:

- `apiSend` was called with `("POST", "/api/release-notes/seen")`;
- the dot is gone afterwards, **without** a second `GET /api/release-notes` — count the `apiGet` calls for that path and assert it stayed at one.

Adding the entry to `responses` also settles the new fetch, which is what removes the `act(...)` warning this effect otherwise introduces.

- [ ] **Step 11: Run the whole frontend suite and typecheck**

Test output must be pristine. Two `act(...)` warnings pre-date this branch (from the `/api/me` and `/api/appearance` effects) and are not yours to fix; a **third**, from the release-notes effect, is, and Step 10b removes it.

```bash
just test-js
just typecheck
```

Expected: both pass. A failure in `AppShell.test.tsx` or `Sidebar.test.tsx` means a render is missing the new provider — wrap it, as in Step 9.

- [ ] **Step 12: Commit**

```bash
git add frontend/src
git commit -m "feat(release-notes): a panel in the app, and a dot that says to open it"
```

---

### Task 7: CI enforcement

**Files:**
- Modify: `.github/workflows/ci.yml` (new `changelog` job)

**Interfaces:**
- Consumes: the `changes/` directory (Task 3).
- Produces: a required check that a PR either adds a fragment or carries `no-changelog`.

- [ ] **Step 1: Add the job**

Append to `.github/workflows/ci.yml`:

```yaml
  # Every PR either says what a user would notice, or says explicitly that
  # they would notice nothing. Pull requests only: on main this must never
  # be able to fail a release, and there is no PR to carry a label there.
  #
  # The label is the escape hatch that keeps this from becoming a hostage
  # situation -- most PRs (test isolation, CI tuning, refactors) genuinely
  # change nothing a person using reef would see.
  changelog:
    name: Changelog fragment
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0 # the diff below needs the base commit

      - name: Require a fragment or the no-changelog label
        env:
          LABELS: ${{ toJSON(github.event.pull_request.labels.*.name) }}
          BASE: ${{ github.event.pull_request.base.sha }}
          HEAD: ${{ github.event.pull_request.head.sha }}
        run: |
          if echo "$LABELS" | grep -q '"no-changelog"'; then
            echo "Labelled no-changelog; nothing to write."
            exit 0
          fi
          # Merge-base, not $BASE directly. $BASE is main's tip *now*, and a
          # release deletes the fragments it consumed -- so a branch that
          # forked before a release still carries files main no longer has,
          # and a two-dot diff reports them as this PR's additions. That is a
          # false pass on exactly the check meant to catch an empty PR.
          fork=$(git merge-base "$BASE" "$HEAD")
          added=$(git diff --name-only --diff-filter=A "$fork" "$HEAD" -- 'changes/*.md')
          if [ -n "$added" ]; then
            echo "Fragment(s) added:"
            echo "$added"
            exit 0
          fi
          # One ::error:: line, not five. GitHub renders each as its own
          # annotation box, so five echoes become five mid-sentence fragments
          # in the Checks panel. %0A is a newline inside a single annotation.
          echo "::error::Say what changed, or say that nothing did.%0A%0AIf a person using reef would notice this change, add one sentence for them in changes/<pr-number>-<slug>.md -- see changes/README.md for how to write it.%0A%0AIf they would notice nothing (tests, CI, refactors), add the no-changelog label to this PR instead."
          exit 1
```

- [ ] **Step 1b: Let the escape hatch actually fire**

The workflow's `on: pull_request:` has no `types:`, so it uses GitHub's defaults — `opened`, `synchronize`, `reopened`. **`labeled` is not among them.** Adding `no-changelog` to a PR whose check has already failed would therefore change nothing until somebody manually clicked "Re-run failed jobs". An escape hatch that needs a manual re-run is experienced as broken.

Change the trigger to name the types explicitly, keeping the three defaults:

```yaml
on:
  pull_request:
    # labeled/unlabeled are here for the changelog job's escape hatch: adding
    # no-changelog has to re-run the check that the label exists to satisfy,
    # and removing it has to put the check back. Naming any type replaces the
    # default list, so the three defaults are repeated here deliberately.
    types: [opened, synchronize, reopened, labeled, unlabeled]
  push:
    branches: [main]
```

This makes every label change re-run the whole workflow, backend and frontend included. That is the cost, it is accepted, and it is why the comment says what the two extra types are for.

- [ ] **Step 2: Verify the YAML parses**

Run: `python3 -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text()); print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit and open the PR that proves it**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: require a PR to say what a user would notice, or say it changes nothing"
```

This branch's own PR is the test. Expected: the `changelog` check **fails**, because this branch has added no fragment. That failure is the feature working. Then add the `no-changelog` label to this PR — CI tooling is invisible to users — and confirm the check goes green on re-run.

---

### Task 8: semantic-release

**Files:**
- Create: `.releaserc.json`
- Modify: `.github/workflows/ci.yml` (a dry-run step on PRs, a `release` job on main)
- Create: `scripts/stamp_version.py`

**Interfaces:**
- Consumes: `scripts/fold_changes.py` (Tasks 3 and 5).
- Produces: tags `vX.Y.Z` on main, `CHANGELOG.md`, and the version stamped into all three manifests.

- [ ] **Step 1: Write the version stamper**

Create `scripts/stamp_version.py`:

```python
"""Write one computed version into all three of reef's manifests.

Three artifacts, one number: the server's ``pyproject.toml`` and both
clients. The clients are two implementations of the same five commands, so
a reader comparing ``reef-cli`` with ``@haai/reef-cli`` must never have to
work out which numbering scheme they are looking at.

Edits are line-targeted rather than round-tripped through a TOML or JSON
writer: a formatter would reflow files this repo maintains by hand, and the
diff of a release commit should show one changed line per file.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Files holding a version, and how to find it. Order is cosmetic.
_PYPROJECTS = ("pyproject.toml", "clients/python/pyproject.toml")
_PACKAGE_JSON = "clients/ts/package.json"


def stamp(version: str) -> None:
    """Set every manifest's version to ``version``.

    :param version: the version semantic-release computed, without a ``v``
    """
    for relative in _PYPROJECTS:
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        # Anchored to the line: only the [project] version, never a
        # dependency's own pin further down the file.
        stamped, count = re.subn(
            r'(?m)^version = "[^"]*"$', f'version = "{version}"', text, count=1
        )
        if count != 1:
            raise SystemExit(f"{relative}: found no version line to stamp")
        path.write_text(stamped, encoding="utf-8")

    path = ROOT / _PACKAGE_JSON
    text = path.read_text(encoding="utf-8")
    stamped, count = re.subn(
        r'(?m)^(  "version": )"[^"]*"', rf'\g<1>"{version}"', text, count=1
    )
    if count != 1:
        raise SystemExit(f"{_PACKAGE_JSON}: found no version line to stamp")
    path.write_text(stamped, encoding="utf-8")
    # Prove the result still parses; a broken package.json fails npm
    # publish much later and much less clearly.
    json.loads(stamped)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: stamp_version.py <version>")
    stamp(sys.argv[1])
    print(f"stamped {sys.argv[1]} into three manifests")
```

- [ ] **Step 2: Check it by hand, then revert**

```bash
python3 scripts/stamp_version.py 9.9.9
git diff --stat
git checkout pyproject.toml clients/python/pyproject.toml clients/ts/package.json
```

Expected: exactly three files changed, one line each.

- [ ] **Step 3: Write `.releaserc.json`**

```json
{
  "branches": ["main"],
  "plugins": [
    [
      "@semantic-release/commit-analyzer",
      {
        "preset": "conventionalcommits",
        "releaseRules": [
          { "breaking": true, "release": "major" },
          { "type": "feat", "release": "minor" },
          { "type": "fix", "release": "patch" },
          { "type": "perf", "release": "patch" },
          { "type": "refactor", "release": "patch" },
          { "type": "chore", "release": "patch" },
          { "type": "docs", "release": "patch" },
          { "type": "build", "release": "patch" },
          { "type": "ci", "release": "patch" },
          { "type": "style", "release": "patch" },
          { "type": "test", "release": "patch" },
          { "type": "revert", "release": "patch" }
        ]
      }
    ],
    ["@semantic-release/release-notes-generator", { "preset": "conventionalcommits" }],
    "@semantic-release/changelog",
    [
      "@semantic-release/exec",
      {
        "prepareCmd": "python3 scripts/stamp_version.py ${nextRelease.version} && python3 scripts/fold_changes.py ${nextRelease.version} $(date -u +%Y-%m-%d)"
      }
    ],
    [
      "@semantic-release/git",
      {
        "assets": [
          "CHANGELOG.md",
          "site/release-notes.json",
          "site/changelog.html",
          "pyproject.toml",
          "clients/python/pyproject.toml",
          "clients/ts/package.json",
          "changes"
        ],
        "message": "chore(release): ${nextRelease.version} [skip ci]\n\n${nextRelease.notes}"
      }
    ],
    ["@semantic-release/github", { "successComment": false, "failComment": false }]
  ]
}
```

`changes` is in `assets` so the *deletions* the fold made are committed. Without it the fragments come back next release and every entry repeats.

- [ ] **Step 4: Add the PR dry run**

In `.github/workflows/ci.yml`, add this as **its own job**, not as a step inside `backend`:

```yaml
  # A no-write semantic-release run, so a broken .releaserc.json or an
  # unparseable commit history fails in review rather than on main -- where it
  # would sit between a merge and a production deploy.
  #
  # Its own job, mirroring `changelog` above, rather than a step in `backend`.
  # semantic-release reads the full tag history, and this action runs against
  # the job's existing checkout rather than cloning its own -- so as a step in
  # `backend` it would need fetch-depth: 0 there, deepening the clone on every
  # push to main as well, for a step that only ever runs on pull requests.
  release-dry-run:
    name: Release dry run
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0 # semantic-release reads full tag and commit history

      - uses: actions/setup-node@v4
        with:
          node-version: 22 # same as the release job, so both resolve alike

      - name: Semantic-release dry run
        uses: cycjimmy/semantic-release-action@v4
        with:
          dry_run: true
          extra_plugins: |
            @semantic-release/changelog@6
            @semantic-release/git@10
            @semantic-release/exec@6
            conventional-changelog-conventionalcommits@7
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

`backend`'s checkout stays at its default depth.

- [ ] **Step 5: Add the release job**

```yaml
  # Cut a SemVer release from the conventional commits since the last tag.
  # Gated on backend and frontend so a failing build never tags a release.
  #
  # Note for whoever wonders later: [skip ci] stops GitHub Actions, not
  # Railway, which deploys every push to main. Each release therefore costs
  # one extra no-op production deploy. That is known and accepted.
  release:
    name: 🔖 Release
    needs: [backend, frontend]
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    permissions:
      contents: write
      issues: write
      pull-requests: write
    outputs:
      published: ${{ steps.semantic.outputs.new_release_published }}
      version: ${{ steps.semantic.outputs.new_release_version }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0 # semantic-release reads the full tag and commit history
      - uses: actions/setup-node@v4
        with:
          node-version: 22
      - name: 🔖 semantic-release
        id: semantic
        uses: cycjimmy/semantic-release-action@v4
        with:
          extra_plugins: |
            @semantic-release/changelog@6
            @semantic-release/git@10
            @semantic-release/exec@6
            conventional-changelog-conventionalcommits@7
        env:
          GITHUB_TOKEN: ${{ secrets.RELEASE_TOKEN }}
```

`RELEASE_TOKEN` rather than `GITHUB_TOKEN`: branch protection on `main` rejects the default token's push. Task 10 covers creating it.

- [ ] **Step 6: Verify the YAML and the JSON parse**

```bash
python3 -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text()); print('yaml ok')"
python3 -c "import json, pathlib; json.loads(pathlib.Path('.releaserc.json').read_text()); print('json ok')"
```

Expected: both `ok`.

- [ ] **Step 7: Commit**

```bash
git add .releaserc.json .github/workflows/ci.yml scripts/stamp_version.py
git commit -m "ci: let the commits decide the version, and write down what they changed"
```

The dry run on this PR is the proof: read its log and confirm it reports a next version rather than erroring.

---

### Task 9: Path-gated publishing

**Files:**
- Modify: `.github/workflows/ci.yml` (a `publish` job)

**Interfaces:**
- Consumes: the `release` job's `published` and `version` outputs (Task 8).
- Produces: `reef-cli` on PyPI and `@haai/reef-cli` on npm, each only when its own source changed.

- [ ] **Step 1: Add the job**

```yaml
  # Publish only what actually changed. Both clients carry the repo-wide
  # version, so a server-only release would otherwise push two byte-identical
  # packages to two public registries and burn a version number saying
  # nothing. Client versions therefore skip -- 0.3.0 to 0.7.0 -- and every
  # number on a registry corresponds to a real change to that client.
  #
  # The diff ends at HEAD~1 because HEAD is semantic-release's own
  # "chore(release)" commit, which touches every manifest and would make the
  # gate always true. That holds only while @semantic-release/git makes exactly
  # one commit, and the failure is NOT one-directional: zero commits would skip
  # wrongly, but *two* would leave a release commit inside the diff range and
  # publish both clients on every release -- the expensive direction. So the
  # assumption is asserted below rather than trusted, and every branch logs
  # what it decided.
  publish:
    name: 📦 Publish changed clients
    needs: release
    if: needs.release.outputs.published == 'true'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write # PyPI trusted publishing
    steps:
      - uses: actions/checkout@v4
        with:
          ref: main
          fetch-depth: 0

      - name: 🔎 Decide what changed
        id: changed
        run: |
          previous=$(git describe --tags --abbrev=0 "v${{ needs.release.outputs.version }}^" 2>/dev/null || true)
          if [ -z "$previous" ]; then
            echo "No previous tag: first release, publishing both clients."
            echo "python=true" >> "$GITHUB_OUTPUT"
            echo "ts=true" >> "$GITHUB_OUTPUT"
            exit 0
          fi
          # Assert the HEAD~1 assumption instead of trusting it. Exactly one
          # release commit must sit between the previous tag and HEAD; zero or
          # two both mean the range below is wrong, and two would publish
          # everything. Fail loudly rather than take the dangerous branch.
          releases=$(git log --format=%s "$previous"..HEAD | grep -c '^chore(release):' || true)
          if [ "$releases" -ne 1 ]; then
            echo "::error::Expected exactly one chore(release) commit since $previous, found $releases. The publish gate's diff range is only valid with one; refusing to publish."
            exit 1
          fi
          echo "Comparing $previous..HEAD~1 (HEAD is the release commit)."
          changed=$(git diff --name-only "$previous" HEAD~1)
          echo "$changed"
          if echo "$changed" | grep -q '^clients/python/'; then
            echo "python=true" >> "$GITHUB_OUTPUT"
            echo "reef-cli changed: will publish."
          else
            echo "python=false" >> "$GITHUB_OUTPUT"
            echo "reef-cli unchanged: SKIPPING publish."
          fi
          if echo "$changed" | grep -q '^clients/ts/'; then
            echo "ts=true" >> "$GITHUB_OUTPUT"
            echo "@haai/reef-cli changed: will publish."
          else
            echo "ts=false" >> "$GITHUB_OUTPUT"
            echo "@haai/reef-cli unchanged: SKIPPING publish."
          fi

      - uses: astral-sh/setup-uv@v5
        if: steps.changed.outputs.python == 'true'
      - name: 🐍 Publish reef-cli to PyPI
        if: steps.changed.outputs.python == 'true'
        run: |
          uv build --package reef-cli
          uv publish   # trusted publishing; no token in the repo

      - uses: actions/setup-node@v4
        if: steps.changed.outputs.ts == 'true'
        with:
          node-version: 22
          registry-url: https://registry.npmjs.org
      # Build before publishing. package.json points `bin` at dist/index.js and
      # ships `files: ["dist"]`, but dist/ is not in git and there is no
      # prepare/prepublishOnly hook -- so publishing without this step uploads
      # a package whose only executable does not exist. To a public registry,
      # where it cannot be taken back.
      - name: 🔨 Build @haai/reef-cli
        if: steps.changed.outputs.ts == 'true'
        working-directory: clients/ts
        run: |
          npm ci
          npm run build
          test -f dist/index.js  # refuse to publish what the build did not produce
      - name: 📦 Publish @haai/reef-cli to npm
        if: steps.changed.outputs.ts == 'true'
        working-directory: clients/ts
        env:
          NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}
        run: npm publish --access public
```

- [ ] **Step 2: Confirm `uv build --package reef-cli` produces what you expect**

```bash
uv build --package reef-cli
ls dist/
```

Expected: an `sdist` and a wheel named for `reef_cli`, not for `rif`. If it builds the server instead, fix the `--package` argument before this ever runs against a real registry. Then `rm -rf dist/`.

- [ ] **Step 3: Verify the YAML parses**

Run: `python3 -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text()); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: publish a client only when that client actually changed"
```

---

### Task 10: The commit convention and the operator checklist

**Files:**
- Create: `docs/releasing.md`
- Modify: `README.md` (one line pointing contributors at it)

**Interfaces:**
- Consumes: everything above.
- Produces: the written record of how a release happens and what a human must do once.

- [ ] **Step 1: Write `docs/releasing.md`**

Cover, in this order:

1. **Commit subjects.** The types from `.releaserc.json` and what each one bumps. Give reef's own examples — `feat(search): answer "what did we know in March" with a WHERE clause` — so the narrative style is visibly preserved, not replaced. Note that PRs are squash-merged, so the **PR title** is the commit subject that semantic-release reads.
2. **What happens on merge.** Version computed, `CHANGELOG.md` written, fragments folded, manifests stamped, tag pushed, changed clients published; plus the extra no-op Railway deploy from the release commit.
3. **Fragments.** Point at `changes/README.md` rather than repeating it.
4. **The operator steps below.**
5. **How to check a release went out**: the tag, the GitHub release, and the registries.

- [ ] **Step 2: Write the operator checklist into that file**

These are hand steps and none of them can be done in code. The first release fails without 1 and 2.

- [ ] **PyPI trusted publishing** for `reef-cli`: on PyPI, add a trusted publisher for this repo, workflow `ci.yml`, environment blank.
- [ ] **npm token**: create an automation token with publish rights on `@haai/reef-cli`, store it as the `NPM_TOKEN` repo secret.
- [ ] **`RELEASE_TOKEN`**: a fine-grained PAT with `contents: write` on this repo, stored as a repo secret. Branch protection on `main` rejects the default `GITHUB_TOKEN`'s push, and the release fails at the very last step with a permissions error that reads like a bug.
- [ ] **Create the `no-changelog` label** on the repo.
- [ ] **Add the two new checks to branch protection**, by the names they report under: **`Changelog fragment`** and **`Release dry run`**. Both are gated with a job-level `if:`, so each appears as its own named check rather than as a step inside an existing one — and neither is enforced until it is named in branch protection. Without this, Task 7's enforcement is advisory and the dry run is decoration.

- [ ] **Step 3: Point contributors at it**

Add one line to `README.md`'s contributing or development section: how to write a commit subject, and when to add a fragment, both linking `docs/releasing.md`.

- [ ] **Step 4: Commit**

```bash
git add docs/releasing.md README.md
git commit -m "docs(releasing): how a version gets cut, and the four things a human must do once"
```

---

## Verification before calling this done

- [ ] `just test` passes — lint, format check, backend, frontend.
- [ ] `just typecheck` passes.
- [ ] `just db-reset-test && just migrate && just test-py` passes, proving the migration chain builds an empty database.
- [ ] The PR's `changelog` check fails without a fragment and passes with the `no-changelog` label.
- [ ] The PR's semantic-release dry run reports a next version rather than an error.
- [ ] All four operator steps in `docs/releasing.md` are done before the first merge to main — the release job runs on the very next push, and the publish job fails without them.
