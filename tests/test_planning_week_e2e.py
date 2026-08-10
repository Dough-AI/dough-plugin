"""End-to-end: one planning week, three scenario files, one lake table.

`test_upload_e2e.py` drives a model into the add-and-omit rejection and asserts
it handles being refused. This drives a model at the case the skill's "Several
files into one table" section exists to make it AVOID being refused at all: three
scenario extracts of one FP&A plan that collide on their natural key and disagree
about their columns. See `tests/fixtures/planning-week-2026-w32/README.md`, which
is the specification for what each file is designed to surface.

Every test runs once per entry skill in `ENTRY_SKILLS`. The user does not choose
which skill opens a request like this one — the descriptions do, and this request
matches `uploads` as squarely as `datalake`. Whichever door the agent comes
through, the same five invariants have to hold; a rule that only exists behind
one of them is a rule half the sessions never see.

Opt-in, because it spends tokens (two agent runs, one per entry skill):

    DOUGH_E2E=1 .venv/bin/python -m pytest tests/test_planning_week_e2e.py -v -s

Assertions are invariants, not a transcript. A correct run may name the
discriminator anything, order its uploads any way, poll as often as it likes and
describe what it did however it wants — so none of that is asserted. What must
hold is that provenance survived (one upload per file, each with its own label
and URL), that the key gained a column no source file contains, that each load
was seen to finish before the next file was sent, that every row arrived, and
that nothing was refused.

ON THE ABSENCE ASSERTION. `test_no_upload_was_rejected` is the load-bearing one
and it is also the dangerous shape: "nothing was refused" passes for free
against a server that cannot refuse. It is only evidence because
`tests/test_fake_mcp_rules.py` proves the fake's refusals fire — specifically
`test_an_append_repeating_a_key_already_in_the_table_is_rejected`,
`test_a_csv_that_repeats_a_key_internally_is_rejected`,
`test_an_append_that_adds_and_omits_is_rejected_naming_both_sides`,
`test_an_append_sent_while_the_previous_load_runs_is_refused`, and above all
`test_sending_the_next_file_straight_away_is_refused` and
`test_the_naive_flow_over_the_real_fixture_hits_both_rejections`, which send
THESE THREE FILES through the obvious flows and get refused. If that file is
deleted or its rules are weakened, this test stops meaning anything.
"""

import csv
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
FAKE_MCP = REPO / "tests" / "fake_dough_mcp.py"
FIXTURE = REPO / "tests" / "fixtures" / "planning-week-2026-w32"

TABLE = "fy26h2_plan"

# One gsheet, one tab per scenario. The URLs are plausible rather than real: the
# tool stores `sourceUrl` and never fetches it, so what matters is that each file
# has its OWN origin to carry and that collapsing them would be visible.
SHEET = "https://docs.google.com/spreadsheets/d/1KfP9lanW32y6H2sCeNar1oSxmpLe/edit"
SOURCES = [
    ("fy26h2_plan_base.csv", "Base", f"{SHEET}#gid=0"),
    ("fy26h2_plan_upside.csv", "Upside", f"{SHEET}#gid=118342207"),
    ("fy26h2_plan_downside.csv", "Downside", f"{SHEET}#gid=904471553"),
]

# BOTH DOORS INTO THIS SCENARIO.
#
# A user with three scenario tabs of an operating plan does not pick the skill;
# the descriptions do. This request matches `uploads` ("operating plan ... parsed
# from a spreadsheet, CSV, or document") at least as well as `datalake`, so the
# multi-file rules have to survive being entered from either one. Running the
# same invariants from each door is what tells us whether `uploads`' pointer to
# datalake 1b is load-bearing or decorative — an agent that reads `uploads`,
# decides the shape there, and only consults datalake for tool mechanics has
# already chosen its key before it meets the discriminator rule.
ENTRY_SKILLS = ["datalake", "uploads"]

# The confirmation the skill asks for ("matching headers are not matching
# meaning") is given up front, so a correct run has no reason to stop and ask.
# Everything the agent has to work out for itself — the union of the headers,
# that the files collide, that the discriminator has to come from the tab name —
# is deliberately absent. Stating the grain is not a hint: it is what the person
# actually knows, and it is the trap, since the obvious key it implies is the one
# that collides.
def prompt_for(entry):
    return f"""Load the `{entry}` skill and follow it.

Our FP&A planning week just closed and produced three scenario extracts, one per
tab of the "FY26 H2 Plan (2026-W32)" gsheet. I've exported each tab to a CSV in
this directory:

- ./fy26h2_plan_base.csv — the "Base" tab, {SOURCES[0][2]}
- ./fy26h2_plan_upside.csv — the "Upside" tab, {SOURCES[1][2]}
- ./fy26h2_plan_downside.csv — the "Downside" tab, {SOURCES[2][2]}

These three are the scenarios of one plan and they belong together in a single
Dough data lake table called `{TABLE}`, so we can compare them side by side and
against actuals later. There is no connected system behind any of this — it only
exists in that sheet.

One row is one cost centre's planned amount for one account in one month.
`amount` is money and `period` is a date.

Work autonomously: don't stop to ask me questions, and tell me what you did at
the end."""

pytestmark = pytest.mark.skipif(
    not os.environ.get("DOUGH_E2E"),
    reason="costs tokens and needs auth; set DOUGH_E2E=1 to run",
)


def rows_of(csv_text):
    """Data rows, counted the way the server counts them."""
    rows = [r for r in csv.reader(io.StringIO(csv_text or "")) if any(c.strip() for c in r)]
    return max(len(rows) - 1, 0)


def header_of(csv_text):
    for row in csv.reader(io.StringIO(csv_text or "")):
        if any(cell.strip() for cell in row):
            return [cell.strip() for cell in row]
    return []


def fixture_text(filename):
    return (FIXTURE / filename).read_text(encoding="utf-8")


# Everything the fixture holds, read from the fixture rather than restated here,
# so an edit to the CSVs cannot leave this test asserting yesterday's numbers.
FIXTURE_ROWS = sum(rows_of(fixture_text(name)) for name, _, _ in SOURCES)
FIXTURE_COLUMNS = {c for name, _, _ in SOURCES for c in header_of(fixture_text(name))}


def read_log(path):
    entries = []
    if path.exists():
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if line.strip():
                entries.append({**json.loads(line), "_line": index})
    return entries


@pytest.fixture(scope="module", params=ENTRY_SKILLS)
def run_agent(request, tmp_path_factory):
    entry = request.param
    work = tmp_path_factory.mktemp(f"planning_week_e2e_{entry}")
    # The agent needs these as working files it can read, convert and edit, so
    # they are copied in rather than pointed at inside the repo.
    for name, _, _ in SOURCES:
        shutil.copyfile(FIXTURE / name, work / name)

    log = work / "calls.jsonl"
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
            "claude", "-p", prompt_for(entry),
            # --plugin-dir loads this repo as a plugin, so the agent reads the
            # real skills/*/SKILL.md; --strict-mcp-config keeps the run on
            # the fake rather than the live server in the plugin's .mcp.json.
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
    print(f"\n  entry skill: {entry}\n  run artifacts: {work}")

    entries = read_log(log)
    if not entries:
        pytest.fail(
            f"the agent (entered via `{entry}`) made no MCP calls\n"
            f"--- stdout ---\n{result.stdout[-2000:]}\n"
            f"--- stderr ---\n{result.stderr[-1000:]}"
        )
    return {"work": work, "entries": entries, "result": result, "entry": entry}


def uploads(entries):
    """Every tables.upload call paired with what the server decided. The call and
    its outcome are separate log lines, matched by position."""
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


def accepted(entries):
    return [c for c in uploads(entries) if (c["outcome"] or {}).get("accepted")]


def summary(calls):
    return json.dumps(
        [
            {
                "mode": c["args"].get("mode"),
                "sourceLabel": c["args"].get("sourceLabel"),
                "sourceUrl": c["args"].get("sourceUrl"),
                "rows": rows_of(c["args"].get("csv")),
                "accepted": (c["outcome"] or {}).get("accepted"),
                "reason": (c["outcome"] or {}).get("reason"),
            }
            for c in calls
        ],
        indent=2,
    )


def test_each_file_was_uploaded_on_its_own_call(run_agent):
    """Three files, three uploads.

    Concatenating them locally would work and would land one upload carrying
    every row — which is the specific move the skill forbids, because it gives
    all 45 rows a single origin and there is no recovering the per-scenario
    pointer afterwards.
    """
    calls = accepted(run_agent["entries"])
    print(f"\n  accepted uploads: {summary(calls)}")
    assert len(calls) == len(SOURCES), (
        f"entered via `{run_agent['entry']}`: expected one upload per source "
        f"file ({len(SOURCES)}), got {len(calls)}\n"
        + summary(uploads(run_agent["entries"]))
    )


def test_each_upload_carries_its_own_origin(run_agent):
    """Provenance is per file or it is not provenance. Three uploads that all
    say "the 2026-W32 planning week" have collapsed exactly what the per-file
    pointer exists to keep."""
    calls = accepted(run_agent["entries"])
    labels = [(c["args"].get("sourceLabel") or "").strip() for c in calls]
    urls = [(c["args"].get("sourceUrl") or "").strip() for c in calls]
    print(f"\n  labels: {labels}\n  urls: {urls}")
    assert all(labels), f"an upload carried no sourceLabel: {labels}"
    assert len(set(labels)) == len(calls), f"sourceLabel is not per file: {labels}"
    assert all(urls), f"an upload carried no sourceUrl: {urls}"
    assert len(set(urls)) == len(calls), f"sourceUrl is not per file: {urls}"


def test_the_key_contains_a_column_no_source_file_has(run_agent):
    """The discriminator, tested as a concept rather than a name.

    Nothing inside these files says "upside" — it is the name of a tab. So a key
    that tells the scenarios apart necessarily contains a column that appears in
    no file's header. What the agent calls it, and what values it gives it, are
    its own business.
    """
    creates = [c for c in uploads(run_agent["entries"]) if c["args"].get("mode") == "create"]
    assert creates, "never created the table"
    keys = creates[0]["args"].get("keyColumns") or []
    synthesised = [k for k in keys if k not in FIXTURE_COLUMNS]
    print(f"\n  keyColumns: {keys}\n  source columns: {sorted(FIXTURE_COLUMNS)}"
          f"\n  synthesised: {synthesised}")
    assert synthesised, (
        f"entered via `{run_agent['entry']}`: every key column comes from a "
        "source file's header, so nothing distinguishes one scenario's rows "
        f"from another's: keyColumns={keys}"
    )


def test_the_agent_waited_for_each_load_before_sending_the_next_file(run_agent):
    """One table loads one upload at a time, so the files have to be spaced by a
    poll, not just sent one per call.

    The positive form of `test_no_upload_was_rejected` below, and worth asserting
    separately because the two fail for different reasons: that one goes red if
    the agent sent file 2 early, this one goes red if it never established that
    file 1 had landed — which it could also do by pinging status once, seeing
    "loading", and uploading anyway. What must appear between two consecutive
    uploads is a poll that came back READY.
    """
    calls = accepted(run_agent["entries"])
    polls = [
        e for e in run_agent["entries"]
        if e["kind"] == "status_poll" and e.get("name") == TABLE
    ]
    print(f"\n  status polls: {[(p['_line'], p.get('state')) for p in polls]}")
    for previous, following in zip(calls, calls[1:]):
        ready = [
            p for p in polls
            if previous["_line"] < p["_line"] < following["_line"]
            and p.get("state") == "ready"
        ]
        assert ready, (
            f"entered via `{run_agent['entry']}`: sent the next file without "
            "ever seeing the previous load finish "
            f"(upload at line {previous['_line']} → upload at line "
            f"{following['_line']})\n" + summary(calls)
        )


def test_no_upload_was_rejected(run_agent):
    """The whole point of unioning the headers and inventing a discriminator
    BEFORE the first upload is that neither refusal ever arises.

    This is an absence assertion, and it is only evidence because the fake can
    actually refuse — see the module docstring, and
    tests/test_fake_mcp_rules.py::test_the_naive_flow_over_the_real_fixture_hits_both_rejections,
    which drives these same three files into both refusals.
    """
    calls = uploads(run_agent["entries"])
    refused = [c for c in calls if not (c["outcome"] or {}).get("accepted")]
    assert not refused, (
        f"entered via `{run_agent['entry']}`: {len(refused)} upload(s) were "
        "rejected; the union-first and discriminator steps exist to prevent "
        "exactly this\n" + summary(calls)
    )


def test_every_source_row_arrived(run_agent):
    """Not one file quietly dropped, and no file loaded twice."""
    calls = accepted(run_agent["entries"])
    sent = sum(rows_of(c["args"].get("csv")) for c in calls)
    stored = (calls[-1]["outcome"] or {}).get("rows") if calls else None
    print(f"\n  rows sent: {sent}, rows in the table: {stored}, fixture: {FIXTURE_ROWS}")
    assert sent == FIXTURE_ROWS, (
        f"uploaded {sent} rows, the three files hold {FIXTURE_ROWS}\n" + summary(calls)
    )
    assert stored == FIXTURE_ROWS, f"the table holds {stored} rows, expected {FIXTURE_ROWS}"
