"""Drift check: the run-agent skill must keep the claims that were measured.

Nothing here calls Dough or Claude. These assertions exist because every failure
mode in this skill is silent — a session that quietly did not move, or a guard
that fires on the success path and trains everyone to ignore it.

Each assertion pins a claim TOGETHER WITH ITS REASON. A bare substring check for
"pwd" would pass just as happily on a skill that told you to run it.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
SKILL = ROOT / "skills" / "run-agent" / "SKILL.md"
README = ROOT / "README.md"


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
    assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
    assert re.search(r"^name: run-agent$", text, re.M), "skill name must be run-agent"
    # The description is the only thing that decides whether this skill is ever
    # reached, so it must name the phrasings a user actually types.
    desc = re.search(r"^description: (.+)$", text, re.M)
    assert desc, "SKILL.md needs a description"
    for verb in ("run", "use", "start"):
        assert verb in desc.group(1).lower(), f"description should trigger on '{verb}'"


def test_states_why_being_in_the_directory_matters():
    # Measured: --add-dir registers a directory's skills but does NOT load its
    # CLAUDE.md. That asymmetry is the entire reason this skill moves the session
    # instead of just adding the directory, and it fails silently, so a reader
    # who does not know it will "simplify" the skill straight into the bug.
    body = flat(SKILL)
    assert near(body, "--add-dir", "registers the skills", "does not load"), (
        "the skill must say why adding a directory is not the same as being in it"
    )


def test_fetches_with_sync_not_install():
    # `install`/`upgrade` are being removed; sync overwrites unconditionally,
    # which is why the skill has no "is it already there" branch to get wrong.
    body = flat(SKILL)
    assert "dough agent sync" in body
    assert "dough agent install" not in body
    assert "dough agent upgrade" not in body


def test_does_not_refuse_agents_that_need_credentials():
    # The whole point of the PreToolUse delivery path: a secret-bearing agent can
    # now run in-session. An earlier design refused these outright.
    body = flat(SKILL)
    assert near(body, "requires credentials", "not a refusal", "Keep going"), (
        "the skill must say that requiring credentials is not a reason to stop"
    )


def test_two_turn_protocol_is_explained_not_just_stated():
    body = flat(SKILL)
    assert near(body, "End your turn", "next message"), (
        "step 4 must tell the user when the agent actually becomes available"
    )


def test_pwd_is_forbidden_with_the_turn_boundary_reason():
    body = flat(SKILL)
    assert near(body, "Do not run `pwd`", "does not take effect", "until this turn ends"), (
        "forbidding pwd without the turn-boundary reason reads as arbitrary, and "
        "the reason is the whole point: the check fails a CORRECT switch"
    )


def test_bash_cd_is_forbidden_with_its_reason():
    body = flat(SKILL)
    assert near(body, "Do not fall back to a Bash `cd`", "does not move the session"), (
        "cd is the tempting wrong move when the change-directory tool looks inert"
    )


def test_says_the_session_holds_no_credentials():
    # The single most important thing this skill can say. Anyone who checks the
    # session environment finds it empty, and without this they will report a
    # bug, "fix" it, or refuse to proceed. (The old `!`-bypass caveat was pinned
    # here and is now gone: it only mattered while a hook injected per command,
    # and nothing is injected any more.)
    body = flat(SKILL)
    assert near(body, "This session holds none of them", "not a fault"), (
        "the skill must state that an empty session is correct, not broken"
    )
    assert near(body, "env | grep", "empty", "working"), (
        "name the exact check someone will run and say what it correctly shows"
    )
    assert "do\nnot try to fix it" in SKILL.read_text(encoding="utf-8") or near(
        body, "Do not report that as a problem", "do not try to fix it"
    ), "an explanation without an instruction still leaves room to 'fix' it"


def test_points_writers_at_the_loader():
    # The failure mode this prevents: a model writing its own script, finding
    # nothing in os.environ, and inlining a value into a command instead.
    body = flat(SKILL)
    assert near(body, "Run the agent's scripts", "dough_secrets.load()", window=300), (
        "the normal path — run what ships — must come before the write-your-own case"
    )
    assert near(body, "write your own script", "call the same", "loader"), (
        "improvised code must be told where credentials come from"
    )
    assert near(body, "do not try to read one out of the environment",
                "never inline a value into a command"), (
        "both wrong moves must be named, not just the right one"
    )


def test_never_runs_dough_agent_run_itself():
    # It launches a NEW session and refuses from inside one. Every mention must
    # be advice directed at the user, never an action the model takes.
    body = flat(SKILL)
    for match in re.finditer(r"dough agent run", body):
        chunk = body[max(0, match.start() - 200) : match.start() + 200].lower()
        assert any(
            phrase in chunk for phrase in ("from a terminal", "tell the user", "never")
        ), f"unqualified `dough agent run` near: {chunk[:160]!r}"


def test_credential_values_are_never_printed():
    body = flat(SKILL)
    assert near(body, "Never print", "credential", window=200)
    assert near(body, "never read the file one", "just to display it"), (
        "reading the credential file to show it is the loophole in 'never print'"
    )


def test_vault_failures_are_relayed_not_investigated():
    # These messages are written for a person and name the fix. A model that
    # treats one as a symptom burns a turn diagnosing something already diagnosed.
    body = flat(SKILL)
    assert near(body, "Relay that message", "rather than investigating"), (
        "the skill must say a vault message is the answer, not a clue"
    )


def test_readme_lists_the_skill():
    # README enumerates the skills by hand; a new one that is not listed is
    # invisible to anyone deciding whether to install.
    assert "run-agent" in README.read_text(encoding="utf-8"), (
        "add run-agent to the README skills list"
    )
