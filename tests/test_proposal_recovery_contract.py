"""Static contract checks for the proposal recovery commands."""

import re
from pathlib import Path


ROOT = Path(__file__).parent.parent


def flat(path: Path) -> str:
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


FIX = flat(ROOT / "commands" / "proposal_fix.md")
ABANDON = flat(ROOT / "commands" / "proposal_abandon.md")
PROPOSE_SKILL = flat(ROOT / "skills" / "propose" / "SKILL.md")


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


def test_fix_replaces_exactly_the_eligible_batch_remainder():
    assert "proposals__propose_batch" in FIX
    for phrase in (
        "replacementEligible",
        "replacementSourceProposalIds",
        "sourceProposalId",
        "derivedFromBatchId",
        "idempotencyKey",
    ):
        assert phrase in FIX
    assert "Never replay a posted child" in FIX
    assert "exactly one item for every" in FIX
    assert "no more and no fewer" in FIX
    assert "posted child ids being left untouched" in FIX


def test_fix_keeps_batch_destination_and_handles_concurrent_recovery():
    assert (
        "MUST keep the source batch's connected company, target system, and action kind"
        in FIX
    )
    assert "Reuse the key only when retrying the identical request" in FIX
    assert "invalid_replacement" in FIX
    assert "newest batch" in FIX
    assert "do not weaken or guess around the server's exact-coverage guard" in FIX


def test_shared_evidence_workflow_routes_batch_fix_to_the_batch_tool():
    assert "proposals.propose_batch" in PROPOSE_SKILL
    assert "when fixing a batch" in PROPOSE_SKILL
    assert "proposal tool the invoking workflow requires" in PROPOSE_SKILL


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
