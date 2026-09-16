"""Drift check: the propose skill must work when nobody is on the machine.

Nothing here calls Dough. The failure this guards already happened, in
production, on 2026-09-16: a hosted agent computed its number, curated and sized
its evidence, and stopped at step 4's consent requirement — "say the word and
I'll upload" — with no human anywhere near it. The run completed successfully
having proposed nothing.

The first attempt to fix it did not work either, and the reason is the point of
this file. The box exports DOUGH_UNATTENDED=1, but **an environment variable is
invisible to a model**: Claude Code does not put the process environment into
its context. A skill that says "if DOUGH_UNATTENDED is set" instructs the agent
to read something it cannot see, so it falls through to the interactive branch
and asks — reproducing the original bug exactly, while looking fixed.

So these assertions pin the OBSERVABLE channel, not the variable.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
PROPOSE = ROOT / "skills" / "propose" / "SKILL.md"
SCRIPT = ROOT / "skills" / "propose" / "scripts" / "collect_evidence.py"


def flat(path):
    """Whole file as one line — the docs are hard-wrapped, so a phrase can span
    a newline and any match on raw text would be luck."""
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def prose(text):
    """Drop markdown emphasis before matching on wording.

    Without this every assertion here is hostage to formatting: `*without*`
    breaks a match on "without evidence", so bolding a word for emphasis would
    fail a test about meaning."""
    return re.sub(r"[*_`]", "", text)


def evidence_section():
    """The evidence-backed flow only.

    The file carries TWO numbered lists — the general proposal steps, then this
    one. Matching "4." against the whole document finds the wrong four, and
    every assertion here would then be about a step that has nothing to do with
    consent while still passing or failing convincingly.
    """
    text = flat(PROPOSE)
    start = re.search(r"## Evidence-backed", text)
    assert start, "propose/SKILL.md no longer has an '## Evidence-backed' section"
    rest = text[start.start():]
    end = re.search(r"## (?!Evidence-backed)", rest)
    return rest[: end.start()] if end else rest


def step(number, nxt):
    section = re.search(rf"{number}\. \*\*.*?(?={nxt}\. \*\*)", evidence_section())
    assert section, f"the evidence-backed flow no longer has a step {number}"
    return section.group(0)


# ── the observable channel ───────────────────────────────────────────────────


def test_scan_reports_whether_anyone_is_there(tmp_path):
    """The mechanism the whole fix rests on. `scan` prints JSON that step 3
    already requires be read in full, so the flag arrives through a channel the
    flow depends on rather than one the agent must know to go looking for."""
    home = tmp_path / "home"
    project = home / ".claude" / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", str(tmp_path))
    project.mkdir(parents=True)
    (project / "sess.jsonl").write_text('{"type":"user"}\n', encoding="utf-8")

    def scan(env_value):
        env = {**os.environ, "DOUGH_UNATTENDED": env_value}
        out = subprocess.run(
            [sys.executable, str(SCRIPT), "scan", "--cwd", str(tmp_path), "--home", str(home)],
            capture_output=True, text=True, env=env, check=True,
        )
        return json.loads(out.stdout)

    assert scan("1")["unattended"] is True, "scan does not report an unattended run"
    assert scan("0")["unattended"] is False, "DOUGH_UNATTENDED=0 must not read as on"
    assert scan("")["unattended"] is False, "an empty value must not read as on"


def test_step_3_tells_the_agent_to_read_it():
    """Reporting the flag is useless if nothing says to look at it."""
    assert "unattended" in step(3, 4), (
        "step 3 does not mention the scan's `unattended` field, so the agent "
        "has no reason to read the one thing that tells it the branch applies"
    )


def test_the_skill_does_not_ask_the_agent_to_read_an_env_var():
    """The exact shape of the first, broken fix. An agent cannot see the
    environment, so an instruction phrased against it silently does nothing."""
    consent = step(4, 5)
    assert "DOUGH_UNATTENDED" not in consent, (
        "step 4 branches on the environment variable directly — invisible to "
        "the model. It must branch on the scan output's `unattended` field."
    )


# ── the branch itself ────────────────────────────────────────────────────────


def test_unattended_proceeds_without_asking():
    assert re.search(r"continue without asking|proceed without asking", step(4, 5), re.I), (
        "step 4 names the signal but never says to proceed on it"
    )


def test_disclosure_still_happens_when_nobody_is_reading():
    """The dangerous shortcut. 'Nobody is watching' argues equally well for
    skipping the disclosure — and that is exactly wrong: unattended is when the
    written record is the ONLY record, because there was no conversation."""
    assert re.search(r"not skipped", prose(step(4, 5)), re.I), (
        "step 4 lets an unattended run proceed without saying the disclosure "
        "still has to be produced"
    )


def test_interactive_consent_survives():
    """A person at a terminal must still be asked; the branch is an addition."""
    consent = prose(step(4, 5))
    assert re.search(r"clear user consent", consent, re.I), (
        "the interactive consent requirement has been dropped, not branched"
    )
    assert re.search(r"Otherwise:", step(4, 5)), (
        "step 4 has no explicit 'Otherwise:' branch, so the unattended path may "
        "have swallowed the interactive one"
    )


def test_unattended_is_not_a_licence_to_skip_evidence():
    """The trade this must never make: 'nobody could approve the upload, so I
    proposed without evidence' — a missing signature for a missing audit
    trail, which is the worse of the two."""
    assert re.search(r"never.*propose.*without evidence", prose(step(4, 5)), re.I | re.S), (
        "step 4 does not forbid proposing unevidenced when consent is "
        "unavailable — the exact shortcut an agent would reach for"
    )


def test_the_human_gate_is_still_named():
    """Why proceeding is defensible at all. If this stops being true the branch
    is no longer safe, so the reasoning is pinned rather than left in a PR."""
    assert re.search(r"proposal is still approved by a person", prose(step(4, 5)), re.I), (
        "step 4 permits unattended upload without stating that the proposal "
        "still requires human approval — the reason it is safe"
    )


# ── every OTHER gate that would deadlock the same way ────────────────────────


def test_a_missing_cli_does_not_wait_for_someone_to_install_it():
    """Step 1 tells the user to open a new terminal. Unattended, there is none."""
    assert re.search(r"unattended", step(1, 2), re.I), (
        "step 1 still assumes a person who can install the CLI and rerun"
    )


def test_a_partial_upload_does_not_wait_for_a_choice():
    """Step 6 offers retry / proceed / cancel. Without a branch it re-creates
    the identical deadlock one step later, holding a rented machine."""
    six = step(6, 7)
    assert re.search(r"unattended", six, re.I), (
        "step 6 still offers a choice with nobody there to make it"
    )
    assert re.search(r"declared missing|proceed", six, re.I), (
        "step 6's unattended path must proceed with the gap declared, not cancel"
    )
