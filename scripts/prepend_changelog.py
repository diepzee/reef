"""Prepend one release's notes to CHANGELOG.md.

Invoked by the release-pr workflow with the notes semantic-release's dry
run generated, on stdin -- notes are multi-line markdown, and stdin dodges
every shell-quoting hazard an argument would invite. All the logic lives
in :mod:`reef.releasenotes`, like ``fold_changes.py`` before it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from reef.releasenotes import FragmentError, prepend_changelog

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str]) -> int:
    """Read notes from stdin and prepend them to the changelog.

    :param argv: ``[changelog-path]``, relative to the repo root
    :returns: a process exit status
    """
    if len(argv) != 1:
        print(
            "usage: prepend_changelog.py <changelog-path> < notes.md", file=sys.stderr
        )
        return 2
    try:
        prepend_changelog(sys.stdin.read(), ROOT / argv[0])
    except FragmentError as error:
        print(f"refusing to write the changelog: {error}", file=sys.stderr)
        return 1
    print(f"prepended release notes to {argv[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
