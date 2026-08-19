"""One computed version reaches every manifest a release publishes.

``scripts/stamp_version.py`` is the only thing standing between a release and
a plugin advertising 0.4.0 while the server it connects to answers as 0.5.0.
The script is deliberately line-targeted rather than round-tripped through a
JSON writer, which makes a silently-missed file the failure worth guarding:
these tests stamp real copies of the real manifests and read every one back.
"""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load_script():
    """Import ``scripts/stamp_version.py``, which is not an installed module."""
    spec = importlib.util.spec_from_file_location(
        "stamp_version", ROOT / "scripts" / "stamp_version.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sandbox(tmp_path):
    """A copy of every manifest the script stamps, rooted in ``tmp_path``."""
    script = load_script()
    for relative in script.MANIFESTS:
        source = ROOT / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, target)
    script.ROOT = tmp_path
    return script, tmp_path


def test_every_manifest_receives_the_version(sandbox):
    script, root = sandbox
    script.stamp("9.9.9")
    for relative in script.MANIFESTS:
        assert '"9.9.9"' in (root / relative).read_text(), relative


def test_the_plugin_is_among_them(sandbox):
    """The plugin is published like the clients are, so it is stamped too."""
    script, _ = sandbox
    assert "plugins/reef/.claude-plugin/plugin.json" in script.MANIFESTS
    assert ".claude-plugin/marketplace.json" in script.MANIFESTS


def test_stamped_json_still_parses(sandbox):
    """A manifest broken by a regex fails npm or a plugin install, far later."""
    script, root = sandbox
    script.stamp("9.9.9")
    for relative in script.MANIFESTS:
        if relative.endswith(".json"):
            json.loads((root / relative).read_text())


def test_an_unexpected_second_version_line_is_refused(sandbox):
    """Stamping one line and leaving another stale is the failure to avoid."""
    script, root = sandbox
    target = root / "plugins/reef/.claude-plugin/plugin.json"
    text = target.read_text()
    target.write_text(
        text.replace('  "version"', '  "version": "0.0.0",\n  "version"', 1)
    )
    with pytest.raises(SystemExit):
        script.stamp("9.9.9")
