"""Fragments, the fold that consumes them, and what counts as unread."""

import json
from pathlib import Path

import pytest

from rif.whatsnew import (
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


def test_a_missing_feed_reads_as_no_entries(tmp_path: Path):
    """The first release must not need a file somebody remembered to create."""
    assert load_feed(tmp_path / "absent.json") == []


def test_the_fold_prepends_and_consumes(tmp_path: Path):
    fragments = tmp_path / "changes"
    fragments.mkdir()
    (fragments / "57-search.md").write_text(FRAGMENT)
    feed = tmp_path / "whats-new.json"

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
    feed = tmp_path / "whats-new.json"

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
    feed = tmp_path / "whats-new.json"

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
