"""End-to-end: run the real model against a fake Dough MCP and inspect its uploads.

This is the only test that exercises the `datalake` skill's `tables.upload`
section itself. `test_skill_contract.py` checks that the words are on the page;
this checks that a model reading them builds a payload the tool accepts — and,
when it is refused, that it does something other than send the same bytes again.

It costs tokens and takes ~a minute or two, so it is opt-in:

    DOUGH_E2E=1 .venv/bin/python -m pytest tests/test_upload_e2e.py -v -s

Assertions are invariants, not transcripts of behaviour — a model may
legitimately order its work differently, poll status a different number of
times, or choose either of the two documented remedies. What must hold is that
a create says what identifies a row and where the data came from, that the
add-and-omit upload was refused, and that the refusal changed what was sent
next.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
FAKE_MCP = REPO / "tests" / "fake_dough_mcp.py"

TABLE = "fy26_budget"

# A `sourceLabel` is meant to let someone reading a number six months later find
# the file behind it. These are the values that look filled-in and aren't.
GENERIC_LABELS = {
    "upload",
    "uploaded",
    "upload data",
    "data",
    "csv",
    "csv upload",
    "csv file",
    "file",
    "import",
    "imported data",
    "manual",
    "manual upload",
    "spreadsheet",
    "n/a",
    "unknown",
}

# The H1 file the agent is asked to load. One row per cost centre per month.
H1_CSV = """period,cost_center,region,amount
2026-01-01,ENG,EMEA,412000
2026-01-01,SALES,AMER,318500
2026-02-01,ENG,EMEA,415250
2026-02-01,SALES,AMER,322000
2026-03-01,ENG,EMEA,418000
2026-03-01,SALES,AMER,331750
"""

# The follow-up file, and the whole reason this test can reach the rejection.
# It drops `region` and brings a new `headcount` — one missing column plus one
# extra, which is precisely the combination the tool refuses. The mismatch lives
# in the FILE, never in the instruction, and nothing about the header looks
# wrong on its own: the skill says an append may add a column, and says an
# append may omit a non-key one, so only the rule about doing BOTH saves an
# agent here.
#
# An earlier draft used a misspelling (`regoin` for `region`) in a continued
# session. The model spotted the typo from the header alone and corrected it
# before sending — good behaviour, but it never reached the rule, so the
# blind-retry assertion had nothing to bite on.
Q3_CSV = """period,cost_center,amount,headcount
2026-07-01,ENG,430000,41
2026-07-01,SALES,344000,29
2026-08-01,ENG,431500,41
2026-08-01,SALES,347250,30
2026-09-01,ENG,433000,42
2026-09-01,SALES,351000,31
"""

TURN_ONE = f"""Load the `datalake` skill and follow it.

./fy26_budget_h1.csv is our FY26 H1 operating budget. Finance exported it from
their planning workbook "FY26 Plan v3" (https://example.invalid/fy26-plan-v3);
there is no connected system behind it. Get it into the Dough data lake as a
table called `{TABLE}` so we can join it against actuals later.

One row is one cost centre's budget for one month. `amount` is money, `period`
is a date, and `region` is just a label describing the cost centre.

Work autonomously: don't stop to ask me questions, and tell me what you did at
the end."""

TURN_TWO = f"""Load the `datalake` skill and follow it.

Finance has sent through ./fy26_budget_q3.csv — the Q3 slice of the FY26
operating budget that is already in the Dough data lake as the `{TABLE}` table,
out of the same "FY26 Plan v3" workbook. Append it to that table.

Work autonomously: don't stop to ask me questions, and tell me what you did at
the end."""

pytestmark = pytest.mark.skipif(
    not os.environ.get("DOUGH_E2E"),
    reason="costs tokens and needs auth; set DOUGH_E2E=1 to run",
)


def read_log(path):
    """Events in the order they were appended.

    The MCP server is respawned every turn, so several processes write here
    across a run and line position is the only trustworthy ordering.
    """
    entries = []
    if path.exists():
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if line.strip():
                entries.append({**json.loads(line), "_line": index})
    return entries


@pytest.fixture(scope="module")
def run_agent(tmp_path_factory):
    work = tmp_path_factory.mktemp("upload_e2e")
    log = work / "calls.jsonl"
    # One directory per export, and the agent works from the relevant one. Left
    # side by side, the Q3 agent reads the H1 file and reconstructs the table's
    # columns from it — which is not the situation being tested. Someone loading
    # next quarter's export has the table, not last quarter's file.
    for folder, name, body in (
        ("h1", "fy26_budget_h1.csv", H1_CSV),
        ("q3", "fy26_budget_q3.csv", Q3_CSV),
    ):
        (work / folder).mkdir()
        (work / folder / name).write_text(body, encoding="utf-8")

    mcp_config = work / "mcp.json"
    mcp_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "dough": {
                        "command": sys.executable,
                        "args": [str(FAKE_MCP)],
                        "env": {
                            "DOUGH_FAKE_LOG": str(log),
                            # The evidence sink belongs to the propose flow; the
                            # server reads the variable at import, so it needs a
                            # value even though no upload test ever hits it.
                            "DOUGH_FAKE_SINK": "http://127.0.0.1:1",
                        },
                    }
                }
            }
        )
    )

    def agent(prompt, folder, *extra):
        return subprocess.run(
            [
                "claude", "-p", prompt,
                # The skill under test IS loadable headless: --plugin-dir loads
                # this repo as a plugin for the session, so the agent reads the
                # real skills/datalake/SKILL.md rather than an inlined copy that
                # could drift from it. (The propose e2e has to inline its command
                # body because that skill is not loadable this way.) The plugin
                # also ships a .mcp.json pointing at the live Dough server —
                # --strict-mcp-config is what keeps this run on the fake.
                "--plugin-dir", str(REPO),
                "--mcp-config", str(mcp_config),
                "--strict-mcp-config",
                "--allowedTools", "Bash", "Read", "Write", "Edit", "Skill",
                "mcp__dough__tables__upload",
                "mcp__dough__tables__status",
                "--permission-mode", "bypassPermissions",
                *extra,
            ],
            cwd=work / folder,
            capture_output=True,
            text=True,
            timeout=900,
        )

    # Two turns, because the rejection can only happen against a table that
    # already has columns. They are deliberately two SEPARATE sessions rather
    # than a `--continue`: someone appending next quarter's export is not the
    # person who loaded the first one, and an agent that still has the H1 header
    # in context can rule the combination out by eye instead of by rule.
    first = agent(TURN_ONE, "h1")
    second = agent(TURN_TWO, "q3")

    # Keep the agent's own account of both turns; when an assertion fails, what
    # it said is the fastest way to see what it did.
    (work / "agent-turn1.txt").write_text(first.stdout, encoding="utf-8")
    (work / "agent-turn2.txt").write_text(second.stdout, encoding="utf-8")
    print(f"\n  run artifacts: {work}")

    entries = read_log(log)
    if not entries:
        pytest.fail(
            "the agent made no MCP calls\n"
            f"--- turn 1 ---\n{first.stdout[-2000:]}\n"
            f"--- turn 2 ---\n{second.stdout[-2000:]}\n"
            f"--- stderr ---\n{second.stderr[-1000:]}"
        )
    return {"work": work, "entries": entries, "result": second}


def uploads(entries):
    """Every tables.upload call, each paired with what the server decided.

    A call and its outcome are two separate log lines; they are matched by
    position, since the server writes the outcome before answering the call.
    """
    calls = []
    pending = None
    for entry in entries:
        if entry["kind"] == "tool_call" and entry["tool"] == "tables__upload":
            pending = {"args": entry["args"], "_line": entry["_line"], "outcome": None}
            calls.append(pending)
        elif entry["kind"] == "upload_result" and pending is not None:
            pending["outcome"] = entry
            pending = None
    return calls


def rejection(calls):
    """The add-and-omit refusal: the one the whole test is built around.

    Both halves of the message are required, so a plain missing-key refusal
    (which also says "missing from the upload") is not mistaken for it.
    """
    for call in calls:
        outcome = call["outcome"] or {}
        reason = outcome.get("reason") or ""
        if outcome.get("accepted"):
            continue
        if "missing from the upload:" in reason and "would be added" in reason:
            return call
    return None


def payload(call):
    """What identifies a resend: same table, same mode, same bytes."""
    args = call["args"]
    return (
        (args.get("name") or "").strip(),
        (args.get("mode") or "").strip(),
        (args.get("csv") or "").strip(),
    )


def test_the_create_says_what_identifies_a_row(run_agent):
    """keyColumns is required on create, so a skill-following agent sends it the
    first time. Asserting only on the accepted create would prove nothing — the
    fake refuses a keyless create, so that one always has keys."""
    creates = [c for c in uploads(run_agent["entries"]) if c["args"].get("mode") == "create"]
    assert creates, "never created the table"
    keys = creates[0]["args"].get("keyColumns")
    print(f"\n  keyColumns on the first create: {keys!r}")
    assert keys, "the first create carried no keyColumns"


def test_the_create_says_where_the_data_came_from(run_agent):
    """A label that could describe any upload is worth no more than no label."""
    creates = [c for c in uploads(run_agent["entries"]) if c["args"].get("mode") == "create"]
    assert creates, "never created the table"
    label = (creates[0]["args"].get("sourceLabel") or "").strip()
    print(f"\n  sourceLabel: {label!r}")
    assert label, "the create carried no sourceLabel"
    assert label.lower().rstrip(".") not in GENERIC_LABELS, f"generic filler: {label!r}"
    assert label.lower() != TABLE.lower(), "the label is just the table name"


def test_the_add_and_omit_upload_was_rejected(run_agent):
    """The scenario has to actually reach the rule, or the rest proves nothing."""
    calls = uploads(run_agent["entries"])
    refused = rejection(calls)
    assert refused is not None, (
        "no upload was refused for adding and omitting columns; outcomes were "
        + json.dumps(
            [
                {
                    "mode": c["args"].get("mode"),
                    "accepted": (c["outcome"] or {}).get("accepted"),
                    "reason": (c["outcome"] or {}).get("reason"),
                }
                for c in calls
            ],
            indent=2,
        )
    )
    reason = refused["outcome"]["reason"]
    print(f"\n  rejection: {reason}")
    assert "region" in reason and "headcount" in reason


def test_the_rejected_payload_is_not_sent_again(run_agent):
    """The single assertion this test exists for.

    The skill says outright that resending the same payload fails identically
    and gives two remedies. If the model retries blindly, the prose failed.

    The comparison starts AT the refused call, not after it: a blind retry is a
    duplicate OF that call, so excluding it would leave nothing to duplicate.

    One refusal is exempt, and only one. An upload refused because the previous
    load to that table was still running is SUPPOSED to be sent again unchanged
    — nothing was recorded, and the skill says so in as many words. It cannot be
    confused with the rejection this test is about: the column rules are checked
    first, so an add-and-omit payload is always refused for adding and omitting,
    never for arriving early.
    """
    calls = uploads(run_agent["entries"])
    refused = rejection(calls)
    assert refused is not None, "no add-and-omit rejection to reason about"
    after = [c for c in calls if c["_line"] >= refused["_line"]]
    seen = {}
    for call in after:
        if "still loading" in ((call["outcome"] or {}).get("reason") or ""):
            continue
        key = payload(call)
        assert key not in seen, (
            "resent an identical upload after it was refused "
            f"(table={key[0]!r} mode={key[1]!r}, {len(after)} uploads from the rejection on)"
        )
        seen[key] = call


def test_the_rejection_is_not_forced_through_with_a_replace(run_agent):
    """The other way to fail this rejection: guess, and destroy data doing it.

    An earlier version of this test asserted the run had to RECOVER — that some
    later upload had to succeed. That was wrong, and a real run proved it: the
    model hit the rejection, declined to guess, and stopped to ask which of
    `headcount` and `region` the person actually wanted, noting that blanks are
    illegal if `region` is part of the key. That is better than recovering,
    because the rejection is genuinely ambiguous and only the person knows.

    So the invariant is not "it recovers" — a model may legitimately stop — it is
    that it never resolves the ambiguity by GUESSING. The destructive escape hatch
    is `replace`, which rewrites the whole table to whatever shape the CSV happens
    to have; taking it here would discard the columns it could not reconcile.

    Paired with test_the_rejected_payload_is_not_sent_again, this brackets the two
    bad answers — retry blindly, or overwrite — and leaves every good one open.
    """
    calls = uploads(run_agent["entries"])
    refused = rejection(calls)
    assert refused is not None, "no add-and-omit rejection to reason about"
    after = [c for c in calls if c["_line"] > refused["_line"]]
    replaces = [c for c in after if (c["args"] or {}).get("mode") == "replace"]
    print(f"\n  uploads after the rejection: {len(after)}, "
          f"accepted: {sum(1 for c in after if (c['outcome'] or {}).get('accepted'))}, "
          f"replaces: {len(replaces)}")
    assert not replaces, (
        "answered an ambiguous rejection with a destructive replace "
        f"({[ (c['args'] or {}).get('name') for c in replaces ]})"
    )
