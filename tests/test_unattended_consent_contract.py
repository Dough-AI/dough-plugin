"""Drift check: the propose skill must have a path for a run with nobody on it.

Nothing here calls Dough. The failure this guards is one that already happened,
in production, on 2026-09-16: a hosted agent on a rented machine computed its
number, curated and sized its evidence, and then stopped at step 4's consent
requirement — "say the word and I'll upload" — with no human anywhere near it.
The run completed successfully having proposed nothing.

That is invisible to every other kind of test. The skill was correct, the CLI
worked, the agent obeyed its instructions exactly, and the outcome was a silent
no-op. So these assertions pin the branch that makes an unattended run possible,
AND the two properties it must not quietly trade away.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
PROPOSE = ROOT / "skills" / "propose" / "SKILL.md"


def flat(path):
    """Whole file as one line — the docs are hard-wrapped, so a phrase can span
    a newline and any match on raw text would be luck."""
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def prose(text):
    """Drop markdown emphasis before matching on wording.

    Without this every assertion here is hostage to formatting: `*without*`
    breaks a match on "without evidence", so bolding a word for emphasis would
    fail a test about meaning. Structure is matched on the raw text (above);
    wording is matched on this.
    """
    return re.sub(r"[*_`]", "", text)


def consent_step():
    """Step 4, where curation, disclosure and consent live."""
    text = flat(PROPOSE)
    section = re.search(r"4\. \*\*Curate and disclose\.\*\*.*?(?=5\. \*\*)", text)
    assert section, "propose/SKILL.md no longer has a step 4 'Curate and disclose'"
    return section.group(0)


def test_names_the_unattended_signal():
    """Without this the skill cannot tell an empty room from a person who has
    not answered yet, and the only safe reading is to wait forever."""
    assert "DOUGH_UNATTENDED" in consent_step(), (
        "step 4 does not mention DOUGH_UNATTENDED, so a hosted run has no way "
        "past the consent gate"
    )


def test_unattended_proceeds_without_asking():
    step = consent_step()
    assert re.search(r"continue without asking|proceed without asking", step, re.I), (
        "step 4 names the signal but never says to proceed on it"
    )


def test_disclosure_still_happens_when_nobody_is_reading():
    """The dangerous shortcut. 'Nobody is watching' argues equally well for
    skipping the disclosure — and that is exactly wrong: unattended is when the
    written record is the ONLY record, because there was no conversation."""
    step = consent_step()
    assert re.search(r"not skipped|is the record|run log", step, re.I), (
        "step 4 lets an unattended run proceed without saying the disclosure "
        "still has to be produced"
    )


def test_interactive_consent_survives():
    """A person at a terminal must still be asked. The unattended branch is an
    addition, not a replacement."""
    step = consent_step()
    assert re.search(r"clear user consent", step, re.I), (
        "the interactive consent requirement has been dropped, not branched"
    )
    assert re.search(r"otherwise", step, re.I), (
        "step 4 has no 'otherwise' branch, so the unattended path may have "
        "swallowed the interactive one"
    )


def test_unattended_is_not_a_licence_to_skip_evidence():
    """The trade this must never make: 'nobody could approve the upload, so I
    proposed without evidence'. That swaps a missing signature for a missing
    audit trail, which is the worse of the two."""
    step = prose(consent_step())
    assert re.search(r"never.*propose.*without evidence", step, re.I | re.S), (
        "step 4 does not forbid proposing unevidenced when consent is "
        "unavailable — the exact shortcut an agent would reach for"
    )


def test_the_human_gate_is_still_named():
    """Why proceeding is defensible at all: the proposal itself is approved by a
    person. If that stops being true this branch is no longer safe, so the
    reasoning is pinned here rather than left in a PR description."""
    step = consent_step()
    assert re.search(r"approved by a person|approve", step, re.I), (
        "step 4 permits unattended upload without stating that the proposal "
        "still requires human approval — the reason it is safe"
    )
