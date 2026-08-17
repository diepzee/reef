"""Fold this release's fragments into the published feed.

Invoked by semantic-release's ``prepareCmd`` with the version it computed,
so the entry carries the same number as the tag. All the logic lives in
:mod:`rif.releasenotes` -- the ``Justfile`` lints and tests ``src``,
``tests``, ``scripts`` and ``clients``, but this file stays a thin shell
around that module rather than duplicating its logic.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rif.releasenotes import FragmentError, fold, load_feed, render_page

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
    feed_path = ROOT / "site" / "release-notes.json"
    try:
        entry = fold(ROOT / "changes", feed_path, version, date)
    except FragmentError as error:
        # Fail the release rather than drop somebody's sentence silently.
        print(f"changelog fragment is unreadable: {error}", file=sys.stderr)
        return 1
    if entry is None:
        print(f"{version}: nothing a user would notice; no entry written")
        return 0
    (ROOT / "site" / "changelog.html").write_text(
        render_page(load_feed(feed_path)), encoding="utf-8"
    )
    print(f"{version}: wrote {len(entry.changes)} change(s) to the feed and the page")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
