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
