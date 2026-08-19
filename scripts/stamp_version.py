"""Write one computed version into every manifest reef publishes.

Four artifacts, one number: the server's ``pyproject.toml``, both clients,
and the Claude Code plugin. The clients are two implementations of the same
five commands, so a reader comparing ``reef-cli`` with ``@haai/reef-cli``
must never have to work out which numbering scheme they are looking at — and
the plugin is the same story told to a third audience, so it answers with the
same number as the server it connects to.

The plugin spends its version twice: once in its own manifest, and once in
the marketplace entry that advertises it. Both are stamped, because a
marketplace offering 0.4.0 of a plugin that installs as 0.5.0 is a bug
nobody sees until someone reports a version that does not exist.

Edits are line-targeted rather than round-tripped through a TOML or JSON
writer: a formatter would reflow files this repo maintains by hand, and the
diff of a release commit should show one changed line per file.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: TOML manifests. Their version line is never indented.
_PYPROJECTS = ("pyproject.toml", "clients/python/pyproject.toml")

#: JSON manifests, with the indent their own version line carries. Anchoring
#: to the indent is what keeps the match on the object meant rather than on
#: some nested one that happens to carry a version too.
_JSON_MANIFESTS = (
    ("clients/ts/package.json", 2),
    ("plugins/reef/.claude-plugin/plugin.json", 2),
    (".claude-plugin/marketplace.json", 6),
    # The MCP registry record. It sat at 0.1.0 while the server reached
    # 0.6.0, because nothing stamped it -- and the registry is the one
    # listing that tells a stranger which version they are connecting to.
    ("server.json", 2),
)

#: Every file :func:`stamp` writes, in the order it writes them.
MANIFESTS = _PYPROJECTS + tuple(relative for relative, _ in _JSON_MANIFESTS)


def _replace_once(relative: str, pattern: str, replacement: str) -> str:
    """Substitute the one line matching ``pattern``, or refuse to touch it.

    ``subn`` capped at one substitution cannot distinguish a file with one
    matching line from a file that has grown a second, so the matches are
    counted before anything is written: a manifest that has sprouted another
    version line fails loudly here instead of being stamped half-way.

    :param relative: manifest path, relative to the repository root
    :param pattern: a regex matching exactly one line
    :param replacement: the substitution, in :func:`re.sub` syntax
    :raises SystemExit: when the file does not hold exactly one match
    :returns: the stamped text, already written to disk
    """
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    if len(re.findall(pattern, text)) != 1:
        raise SystemExit(f"{relative}: expected exactly one version line to stamp")
    stamped = re.sub(pattern, replacement, text, count=1)
    path.write_text(stamped, encoding="utf-8")
    return stamped


def stamp(version: str) -> None:
    """Set every manifest's version to ``version``.

    :param version: the version semantic-release computed, without a ``v``
    :raises SystemExit: when any manifest does not hold exactly one version
    """
    for relative in _PYPROJECTS:
        # Anchored to the line: only the [project] version, never a
        # dependency's own pin further down the file.
        _replace_once(relative, r'(?m)^version = "[^"]*"$', f'version = "{version}"')

    for relative, indent in _JSON_MANIFESTS:
        pad = " " * indent
        stamped = _replace_once(
            relative,
            rf'(?m)^({pad}"version": )"[^"]*"',
            rf'\g<1>"{version}"',
        )
        # Prove the result still parses; a broken manifest fails npm publish,
        # or a plugin install, much later and much less clearly.
        json.loads(stamped)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: stamp_version.py <version>")
    stamp(sys.argv[1])
    print(f"stamped {sys.argv[1]} into {len(MANIFESTS)} manifests")
