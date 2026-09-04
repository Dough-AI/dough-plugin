"""Drift check: the gws-connect skill must keep the claims that were measured.

Nothing here calls Google or Dough. A live "create a spreadsheet" test would need
credentials, would mostly be testing Google, and has already been run by hand
against the real OAuth client (folder -> file with that folder as parents ->
filtered list, 3/3). What is NOT covered by that is this file's subject: the
skill's own text, where every failure mode is silent. A rule that loses its
reason gets "simplified" away by the next reader; a scope list that drifts from
triage's makes triage approve a grant the skill never requested.

Each assertion pins a claim TOGETHER WITH ITS REASON, following
test_run_agent_skill_contract.py. A bare substring check for "npm" would pass
just as happily on a skill that told you to use it.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
SKILL = ROOT / "skills" / "gws-connect" / "SKILL.md"
TRIAGE = ROOT / "skills" / "gws-connect" / "scripts" / "triage.py"


def flat(path):
    """Whole file as one line — the docs are hard-wrapped, so a phrase can span a
    newline and any match on raw text would be luck."""
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def near(text, anchor, *needles, window=400):
    """True when every needle appears within `window` chars after `anchor` — i.e.
    in the same explanation, not merely elsewhere in the file."""
    for match in re.finditer(re.escape(anchor), text):
        chunk = text[match.start() : match.start() + window]
        if all(n in chunk for n in needles):
            return True
    return False


def test_skill_exists_with_frontmatter():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "name: gws-connect" in text
    assert "description:" in text


def test_scopes_do_not_drift_from_triage():
    """The scope list is hardcoded in BOTH files.

    If SKILL.md gains a scope and triage.py does not, triage reports "sufficient"
    for a grant that lacks it and the failure surfaces much later as an
    unexplained 403. This is the one drift that cannot be caught by reading
    either file alone.
    """
    in_skill = set(re.findall(r"auth/([a-z.]+)", SKILL.read_text(encoding="utf-8")))
    declared = re.search(r"NEEDED_SCOPES = \(([^)]*)\)", TRIAGE.read_text(encoding="utf-8"))
    assert declared, "NEEDED_SCOPES not found in triage.py"
    in_triage = set(re.findall(r'"([^"]+)"', declared.group(1)))
    assert in_skill == in_triage, (
        f"SKILL.md requests {sorted(in_skill)} but triage checks {sorted(in_triage)}"
    )


def test_does_not_install_the_binary_itself():
    """The installer owns the binary; duplicating that here would be a second,
    untested copy that drifts. The skill must point at the installer instead."""
    text = flat(SKILL)
    assert "install.sh" in text or "install.ps1" in text
    assert not re.search(r"tar -xzf|Expand-Archive|shasum -a 256", text), (
        "the skill is re-implementing the installer's download logic"
    )


def test_npm_is_forbidden_with_its_reason():
    """Without the reason, npm reads as a reasonable shortcut when something
    fails — and nvm shims are absent from non-interactive shells, so gws would
    vanish on the next tool call."""
    assert near(flat(SKILL), "npm", "non-interactive")


def test_never_overwrites_another_tools_config_and_says_why():
    """gws hardcodes one config dir, so writing ours would break whatever
    installed theirs. Stating only "stop" would not survive editing."""
    text = flat(SKILL)
    assert "FOREIGN_CLIENT" in text
    assert near(text, "FOREIGN_CLIENT", "third party") or near(
        text, "XDG_CONFIG_HOME", "one client config"
    )


def test_verifies_by_exit_code_not_by_parsing_stdout():
    """Every gws call prints a keyring banner before any JSON, so parsing the
    stream is how this breaks."""
    assert near(flat(SKILL), "exit code", "keyring")


def test_states_the_scope_boundary_so_limits_are_not_read_as_failures():
    """drive.file cannot see files the app did not create. Without this, a
    session reports a working grant as broken when asked to 'find' a file."""
    text = flat(SKILL)
    assert "drive.file" in text
    assert near(text, "Deliberate", "not create") or "wider Drive" in text


def test_requests_explicit_scopes_never_full():
    """`--full` grants a different set and was the prior art's mistake; it also
    silently omits what is actually needed."""
    text = flat(SKILL)
    assert "--scopes" in text
    assert "--full" not in text


def test_does_not_reference_the_removed_shell_triage():
    """triage.sh was replaced by triage.py so it runs on Windows too."""
    assert "triage.sh" not in flat(SKILL)


def test_credentials_are_never_printed():
    assert near(flat(SKILL), "Never print", "file") or "Never print the file" in flat(SKILL)
