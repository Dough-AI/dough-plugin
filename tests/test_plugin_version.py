"""The plugin version lives in three places and they must never disagree.

UPDATING.md: the CLI can fall back to the git SHA, but the Claude Desktop app is
version-gated on the marketplace plugin-entry version. Bump two of the three and
the commit lands on main while every desktop client silently stays on the old
skill, with the Update button grayed out. Nothing else enforces this.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def versions():
    plugin = load(PLUGIN)
    marketplace = load(MARKETPLACE)
    entries = [p for p in marketplace["plugins"] if p["name"] == plugin["name"]]
    assert len(entries) == 1, f"expected one '{plugin['name']}' entry, got {len(entries)}"
    return {
        "plugin.json version": plugin["version"],
        "marketplace.json metadata.version": marketplace["metadata"]["version"],
        # The one the desktop app reads.
        "marketplace.json plugins[].version": entries[0]["version"],
    }


def test_all_three_versions_agree():
    found = versions()
    assert len(set(found.values())) == 1, f"version fields disagree: {found}"


def test_versions_are_semver():
    for where, value in versions().items():
        assert SEMVER.match(value), f"{where} is not semver: {value!r}"
