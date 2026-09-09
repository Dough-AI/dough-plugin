"""Static contract checks for the proposal recovery commands."""

import re
from pathlib import Path


ROOT = Path(__file__).parent.parent


def flat(path: Path) -> str:
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


FIX = flat(ROOT / "commands" / "proposal_fix.md")
ABANDON = flat(ROOT / "commands" / "proposal_abandon.md")


def test_fix_creates_one_derived_proposal_with_fresh_evidence():
    assert "proposals__get" in FIX
    assert "proposals__propose" in FIX
    assert "derivedFrom" in FIX
    assert "revisionNote" in FIX
    assert "dough evidence upload" in FIX
    assert "explicit confirmation" in FIX
    assert "Do not request or reproduce prior transcript" in FIX
    assert "Load the `propose` skill" in FIX
    assert "Evidence-backed proposals" in FIX


def test_fix_allows_the_replacement_to_change_shape_and_destination():
    for phrase in ("connected company", "target system", "action kind", "target record"):
        assert phrase in FIX
    assert "superseded—not abandoned" in FIX
    assert "`revisionId` to call `proposals.get`" in FIX


def test_abandon_is_confirmed_and_preserves_the_outcome():
    assert "proposals__get" in ABANDON
    assert "proposals__abandon" in ABANDON
    assert "explicit confirmation" in ABANDON
    assert "preserves the rejection/failure" in ABANDON
    assert "latest proposal's proposer or an organization admin" in ABANDON
