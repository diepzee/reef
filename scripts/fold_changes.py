"""Fold this release's fragments into the published feed.

Invoked by semantic-release's ``prepareCmd`` with the version it computed,
so the entry carries the same number as the tag. All the logic lives in
:mod:`rif.whatsnew` -- only ``src`` and ``tests`` are linted and tested, so
this file stays a shell around it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rif.whatsnew import FragmentError, fold  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str]) -> int:
    """Fold ``changes/`` into ``site/whats-new.json`` for one release.

    :param argv: ``[version, date]`` -- the version semantic-release
        computed and the release date as ``YYYY-MM-DD``
    :returns: a process exit status
    """
    if len(argv) != 2:
        print("usage: fold_changes.py <version> <YYYY-MM-DD>", file=sys.stderr)
        return 2
    version, date = argv

    # README.md is not a fragment; temporarily move it out of the way so
    # fold() only sees actual fragments.
    changes_dir = ROOT / "changes"
    readme_path = changes_dir / "README.md"
    readme_backup = None
    readme_temp = None
    if readme_path.exists():
        readme_backup = readme_path.read_text(encoding="utf-8")
        readme_temp = changes_dir / (readme_path.name + ".temp")
        readme_path.rename(readme_temp)

    try:
        entry = fold(changes_dir, ROOT / "site" / "whats-new.json", version, date)
    except FragmentError as error:
        # Fail the release rather than drop somebody's sentence silently.
        print(f"changelog fragment is unreadable: {error}", file=sys.stderr)
        return 1
    finally:
        # Restore README.md.
        if readme_backup is not None and readme_temp is not None:
            readme_temp.unlink()
            readme_path.write_text(readme_backup, encoding="utf-8")

    if entry is None:
        print(f"{version}: nothing a user would notice; no entry written")
    else:
        print(f"{version}: wrote {len(entry.changes)} change(s) to the feed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
