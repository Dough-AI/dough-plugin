"""End-to-end: an agent appends to a table whose previous load is still running.

The planning-week e2e cannot reach this. An agent that follows the section is
never refused, so a clean run proves only that it polls — which it does with or
without the text telling it to, because `tables.status` answers "loading" and
anything sensible asks again. That run was measured against both the old and the
new skill and produced the identical poll trace, so it does not discriminate.

What USE-361 is about is the other half: what an agent does WHEN refused. So this
starts the session with the table already mid-load — somebody else's upload, a
minute ago — which is the one way the wait can arrive unearned.

AND THE FIRST RUN OF THIS TEST DISPROVED ITS OWN PREMISE. It was written
asserting the agent would be refused, because a load it did not start is one it
cannot have polled. It was not refused: it polled `tables.status` to ready BEFORE
appending at all, and only then sent the file. That is better than recovering
well, and it means the refusal is not forceable against a cooperative agent —
a test cannot require good behaviour and manufacture the bad path at the same
time. Anything that did force it would be rigging the harness, and the green
result would be about the rig.

So the assertion is the disjunction, which is what "handled it" actually means:
either the agent established the table was ready before uploading, or it was
refused and recovered. What is pinned unconditionally is the damage — three
wrong answers, each its own test, because "it got the file in" passes for a run
that got it in destructively. Wrong: report the upload as failed and stop,
leaving a file unloaded; edit the CSV, since the refusal names key columns and a
CSV is the obvious suspect; force it through with `replace`, which discards the
rows already in the table.

The refusal-and-recovery path itself is pinned without a model in
`tests/test_fake_mcp_rules.py::test_the_identical_payload_succeeds_once_the_load_has_landed`,
which is where it belongs: it is a property of the tool, and asserting it here
would only be re-asserting it through an agent that has no reason to trigger it.

Opt-in, because it spends tokens:

    DOUGH_E2E=1 .venv/bin/python -m pytest tests/test_upload_in_flight_e2e.py -v -s
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
FAKE_MCP = REPO / "tests" / "fake_dough_mcp.py"

TABLE = "fy26_opex"

# Already in the table when the session starts.
LOADED_CSV = """period,cost_center,amount
2026-01-01,ENG,412000
2026-01-01,SALES,318500
2026-02-01,ENG,415250
2026-02-01,SALES,322000
"""

# The file the agent is asked to append. Deliberately unremarkable: same header,
# new periods, no duplicate key, nothing added and nothing omitted. Every other
# rule the tool enforces passes, so the ONLY thing that can refuse this upload is
# the load still running — and an agent that starts changing the file is
# responding to a refusal that had nothing to do with it.
APPEND_CSV = """period,cost_center,amount
2026-03-01,ENG,418000
2026-03-01,SALES,331750
"""

PROMPT = f"""Load the `datalake` skill and follow it.

./fy26_opex_q1_march.csv is the March slice of our FY26 operating expense plan,
out of the same "FY26 Opex Plan v2" workbook (https://example.invalid/fy26-opex-v2)
as the rest of it. The `{TABLE}` table already exists in the Dough data lake —
a colleague loaded January and February a moment ago. Append this file to it.

Work autonomously: don't stop to ask me questions, and tell me what you did at
the end."""

pytestmark = pytest.mark.skipif(
    not os.environ.get("DOUGH_E2E"),
    reason="costs tokens and needs auth; set DOUGH_E2E=1 to run",
)


def data_rows(csv_text):
    """Rows excluding the header — counted the way the server counts them."""
    return max(len([r for r in csv_text.strip().splitlines() if r.strip()]) - 1, 0)


def read_log(path):
    entries = []
    if path.exists():
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if line.strip():
                entries.append({**json.loads(line), "_line": index})
    return entries


def seed_loading_table(log):
    """Put the table into the state the agent finds it in: loaded once, never
    polled since, so its load is still running.

    Written by CALLING the server rather than by hand-crafting log lines, so the
    seeded state is one the server could actually have produced and cannot drift
    from what an upload really records.
    """
    # The module reads these at import and keeps them in constants, so they have
    # to be set before it loads — and put back afterwards, since this runs in the
    # pytest process that every other test shares.
    previous = {k: os.environ.get(k) for k in ("DOUGH_FAKE_LOG", "DOUGH_FAKE_SINK")}
    os.environ["DOUGH_FAKE_LOG"] = str(log)
    os.environ["DOUGH_FAKE_SINK"] = "http://127.0.0.1:1"
    try:
        spec = importlib.util.spec_from_file_location("fake_seed", FAKE_MCP)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.upload(
            {
                "name": TABLE,
                "csv": LOADED_CSV,
                "mode": "create",
                "keyColumns": ["period", "cost_center"],
                "sourceLabel": "FY26 Opex Plan v2, from Finance",
            }
        )
        assert module.is_loading(TABLE), "seeded table is not in flight"
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="module")
def run_agent(tmp_path_factory):
    work = tmp_path_factory.mktemp("upload_in_flight_e2e")
    (work / "fy26_opex_q1_march.csv").write_text(APPEND_CSV, encoding="utf-8")

    log = work / "calls.jsonl"
    seed_loading_table(log)

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
                            "DOUGH_FAKE_SINK": "http://127.0.0.1:1",
                        },
                    }
                }
            }
        )
    )

    result = subprocess.run(
        [
            "claude", "-p", PROMPT,
            "--plugin-dir", str(REPO),
            "--mcp-config", str(mcp_config),
            "--strict-mcp-config",
            "--allowedTools", "Bash", "Read", "Write", "Edit", "Skill",
            "mcp__dough__tables__upload",
            "mcp__dough__tables__status",
            "--permission-mode", "bypassPermissions",
        ],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=900,
    )

    (work / "agent.txt").write_text(result.stdout, encoding="utf-8")
    print(f"\n  run artifacts: {work}")

    entries = read_log(log)
    if not [e for e in entries if e["kind"] == "tool_call"]:
        pytest.fail(
            "the agent made no MCP calls\n"
            f"--- stdout ---\n{result.stdout[-2000:]}\n"
            f"--- stderr ---\n{result.stderr[-1000:]}"
        )
    return {"work": work, "entries": entries, "result": result}


def uploads(entries):
    """Every tables.upload call paired with the server's decision. Seeded lines
    carry no matching tool_call, so they never appear here — what is inspected is
    only what the AGENT sent."""
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


def summary(calls):
    return json.dumps(
        [
            {
                "mode": c["args"].get("mode"),
                "csv": (c["args"].get("csv") or "").strip(),
                "accepted": (c["outcome"] or {}).get("accepted"),
                "reason": (c["outcome"] or {}).get("reason"),
            }
            for c in calls
        ],
        indent=2,
    )


def test_it_waited_for_the_load_rather_than_uploading_into_it(run_agent):
    """Either route through the wait is correct; blundering into it is not.

    The good routes are "checked the table was ready, then uploaded" and "was
    refused, then waited and resent". The failure this leaves room for is the one
    worth catching: an append fired at a table whose state was never established,
    which is refused and then abandoned — no poll before it, no recovery after.

    See the module docstring for why this is a disjunction rather than an
    assertion that the refusal happened: the first run of this test polled to
    ready before uploading and so was never refused at all.
    """
    calls = uploads(run_agent["entries"])
    polls = [e for e in run_agent["entries"] if e["kind"] == "status_poll"]
    print(f"\n  polls: {[(p['_line'], p.get('state')) for p in polls]}"
          f"\n  uploads: {summary(calls)}")
    assert calls, "the agent never called tables.upload at all"

    first = calls[0]
    ready_before = [
        p for p in polls if p["_line"] < first["_line"] and p.get("state") == "ready"
    ]
    accepted = [c for c in calls if (c["outcome"] or {}).get("accepted")]
    assert ready_before or accepted, (
        "uploaded without ever establishing the table was ready, was refused, and "
        "then gave up — the refusal says nothing was recorded and that waiting "
        "fixes it\n" + summary(calls)
    )


def test_the_file_was_loaded_in_the_end(run_agent):
    """The refusal is transient, so an agent that gives up has lost a file it was
    asked to load — and been told, in the refusal itself, that nothing was
    written. Reporting the upload as failed is the failure this test is for."""
    calls = uploads(run_agent["entries"])
    accepted = [c for c in calls if (c["outcome"] or {}).get("accepted")]
    assert accepted, (
        "never got the file in: every upload was refused or abandoned\n" + summary(calls)
    )


def test_the_same_bytes_were_resent(run_agent):
    """The specific instruction under test. The refusal names key columns, so the
    tempting reading is that something about the CSV is wrong — and a rewritten
    file loads different data than the person handed over, silently.

    Compared on content, not on identity: whitespace at the end of a file is not
    a change to it, but a re-derived row or a dropped column is.
    """
    calls = uploads(run_agent["entries"])
    accepted = [c for c in calls if (c["outcome"] or {}).get("accepted")]
    assert accepted, "nothing was accepted, so there are no bytes to compare"
    sent = (accepted[-1]["args"].get("csv") or "").strip()
    print(f"\n  accepted CSV:\n{sent}")
    assert sent == APPEND_CSV.strip(), (
        "the CSV that finally landed is not the file it was given — the refusal "
        "was about timing, and editing the data in response to it changes what "
        f"was loaded\n--- sent ---\n{sent}\n--- given ---\n{APPEND_CSV.strip()}"
    )


def test_the_refusal_was_not_forced_through_with_a_replace(run_agent):
    """The destructive escape hatch. `replace` is not refused while a load runs —
    it discards the rows it would have been checked against — so an agent that
    reads the refusal as "this table will not take an append" can get its file in
    by deleting January and February to do it."""
    calls = uploads(run_agent["entries"])
    replaces = [c for c in calls if (c["args"] or {}).get("mode") == "replace"]
    assert not replaces, (
        "answered a wait with a replace, which discards the rows already in the "
        "table\n" + summary(calls)
    )


def test_every_row_is_in_the_table(run_agent):
    """Both halves present and nothing loaded twice: the four seeded rows plus
    the two appended ones. A retry that landed twice shows up here."""
    calls = uploads(run_agent["entries"])
    accepted = [c for c in calls if (c["outcome"] or {}).get("accepted")]
    assert accepted, "nothing was accepted"
    stored = (accepted[-1]["outcome"] or {}).get("rows")
    expected = data_rows(LOADED_CSV) + data_rows(APPEND_CSV)
    print(f"\n  rows in the table: {stored}, expected: {expected}")
    assert stored == expected, f"the table holds {stored} rows, expected {expected}"
