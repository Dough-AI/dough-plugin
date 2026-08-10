"""End-to-end: the same planning week, but nobody exported the tabs first.

`test_planning_week_e2e.py` hands the agent three CSVs and proves the multi-file
rules hold from either entry skill. It gets there by assuming away the first
step: a real planning week produces a *workbook* — three tabs in one .xlsx, or a
gsheet someone downloaded — and `datalake` 1b covers that step in six words,
"the files must be CSV first (whatever they came from)". Nothing in either skill
says how to split a workbook into per-tab uploads, and `uploads` step 3 talks
about parsing a source, singular.

So this is the identical scenario with exactly ONE variable changed: the source
is a three-tab workbook instead of three pre-exported CSVs. Same 45 rows, same
collisions, same table. Every invariant that held for the CSV run has to hold
here, because nothing about the data changed — only its container. Any failure
is attributable to that container and to the documentation of it, not to the
data or to the tool contract.

WHAT IS DELIBERATELY NOT ASSERTED. The CSV run asserts a distinct `sourceUrl`
per file because there each tab genuinely had its own `#gid=` link. A person who
hands over a downloaded workbook has one URL for the whole book and no per-tab
address, so requiring three distinct URLs here would be asserting something the
source cannot supply. Tab identity is still knowable, so `sourceLabel` carries
it and that IS asserted.

ON THE ABSENCE ASSERTION. `test_no_upload_was_rejected` passes for free against
a server that cannot refuse; it is evidence only because
`tests/test_fake_mcp_rules.py` proves the fake's refusals fire against these
very bytes — see that file's
`test_the_naive_flow_over_the_real_fixture_hits_both_rejections`. This module
builds its workbook FROM those same fixture CSVs, so the traps are the same
ones.

Opt-in, because it spends tokens:

    DOUGH_E2E=1 .venv/bin/python -m pytest tests/test_planning_week_xlsx_e2e.py -v -s
"""

import csv
import hashlib
import http.server
import io
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

# The CSV run owns the fixture, the log reader and the upload-pairing helpers.
# Importing them keeps the two runs measuring the same things the same way — a
# private copy here could drift and quietly stop comparing like with like.
from test_planning_week_e2e import (
    FAKE_MCP,
    FIXTURE,
    REPO,
    SOURCES,
    TABLE,
    accepted,
    fixture_text,
    header_of,
    read_log,
    rows_of,
    summary,
    uploads,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DOUGH_E2E"),
    reason="costs tokens and needs auth; set DOUGH_E2E=1 to run",
)

openpyxl = pytest.importorskip("openpyxl", reason="needed to build the workbook fixture")

# The workbook is built from the CSVs rather than committed as a binary, so the
# two runs cannot drift apart: edit a fixture CSV and both this test and the CSV
# test see the change. The name is deliberately not BigQuery-safe — spaces and
# parentheses are what a real download is called.
WORKBOOK = "FY26 H2 Plan (2026-W32).xlsx"

# One book, one URL. A downloaded gsheet has no per-tab address, which is the
# whole point of the provenance assertion below.
SHEET = "https://docs.google.com/spreadsheets/d/1KfP9lanW32y6H2sCeNar1oSxmpLe/edit"

FIXTURE_ROWS = sum(rows_of(fixture_text(name)) for name, _, _ in SOURCES)
FIXTURE_COLUMNS = {c for name, _, _ in SOURCES for c in header_of(fixture_text(name))}

# The prompt says what a person would say. It names the tabs, because they are
# visible in the book; it does not say to export them, to union their headers,
# that they collide, or that the scenario has to become a column — all of that
# is what the skills are supposed to supply.
PROMPT = f"""Load the `uploads` skill and follow it.

Our FP&A planning week just closed. I've downloaded the "FY26 H2 Plan (2026-W32)"
sheet as a workbook in this directory: ./{WORKBOOK} — it has three tabs, "Base",
"Upside" and "Downside", one per scenario. It came from {SHEET}.

These three are the scenarios of one plan and they belong together in a single
Dough data lake table called `{TABLE}`, so we can compare them side by side and
against actuals later. There is no connected system behind any of this — it only
exists in that sheet.

One row is one cost centre's planned amount for one account in one month.
`amount` is money and `period` is a date.

Work autonomously: don't stop to ask me questions, and tell me what you did at
the end."""


def build_workbook(path):
    """One tab per fixture CSV, values only — no formatting, no summary rows.

    Faithful to the CSV fixture on purpose. Formatting cues and subtotal traps
    are `structuring-uploads-guide.md`'s subject and they would confound this
    run: if the agent stumbles, it has to be the container that did it.
    """
    book = openpyxl.Workbook()
    book.remove(book.active)
    for filename, tab, _ in SOURCES:
        sheet = book.create_sheet(tab)
        for row in csv.reader(io.StringIO(fixture_text(filename))):
            if any(cell.strip() for cell in row):
                sheet.append(row)
    book.save(path)


def start_sink(received):
    """A real PUT target, because this run actually uploads bytes.

    The CSV run points the sink at a dead port: nothing there ever PUTs, so a
    live server would prove nothing. Here the workbook genuinely travels, and a
    dead port would make a correct agent look like a failing one.

    It lives outside the MCP server process for the reason that file's docstring
    gives — Claude Code respawns that process every turn, and a sink inside it
    would change port mid-run, breaking every URL minted on an earlier turn.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_PUT(self):
            body = self.rfile.read(int(self.headers.get("content-length", 0)))
            received[self.path.strip("/")] = {
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def run_agent(tmp_path_factory):
    work = tmp_path_factory.mktemp("planning_week_xlsx_e2e")
    build_workbook(work / WORKBOOK)

    put_objects = {}
    sink = start_sink(put_objects)
    sink_url = f"http://127.0.0.1:{sink.server_address[1]}"

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
                            "DOUGH_FAKE_SINK": sink_url,
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
            "mcp__dough__tables__source__prepare",
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
    if not entries:
        pytest.fail(
            "the agent made no MCP calls\n"
            f"--- stdout ---\n{result.stdout[-2000:]}\n"
            f"--- stderr ---\n{result.stderr[-1000:]}"
        )
    sink.shutdown()
    return {"work": work, "entries": entries, "result": result, "put": put_objects}


def test_each_tab_was_uploaded_on_its_own_call(run_agent):
    """Three tabs, three uploads.

    A workbook makes concatenation easier than the CSV case did, not harder:
    every parser that opens an .xlsx hands back all three sheets at once, and
    stacking them into one frame before uploading is a single line. That upload
    would be accepted, and all 45 rows would then share one origin.
    """
    calls = accepted(run_agent["entries"])
    print(f"\n  accepted uploads: {summary(calls)}")
    assert len(calls) == len(SOURCES), (
        f"expected one upload per tab ({len(SOURCES)}), got {len(calls)}\n"
        + summary(uploads(run_agent["entries"]))
    )


def test_each_upload_names_its_own_tab(run_agent):
    """Provenance when the source has no per-tab address.

    The book has one URL, so the tab name is the only thing that distinguishes
    one upload's origin from another's. Three labels reading "FY26 H2 Plan
    (2026-W32)" have thrown away which scenario each row came from — the one
    fact per-row provenance exists to keep here.
    """
    calls = accepted(run_agent["entries"])
    labels = [(c["args"].get("sourceLabel") or "").strip() for c in calls]
    print(f"\n  labels: {labels}")
    assert all(labels), f"an upload carried no sourceLabel: {labels}"
    assert len(set(labels)) == len(calls), (
        f"sourceLabel does not distinguish the tabs: {labels}"
    )


def test_the_key_contains_a_column_no_tab_has(run_agent):
    """The discriminator, unchanged by the container.

    "Upside" is a tab name in the workbook exactly as it was a tab name in the
    gsheet — still absent from every header, still required in the key.
    """
    creates = [c for c in uploads(run_agent["entries"]) if c["args"].get("mode") == "create"]
    assert creates, "never created the table"
    keys = creates[0]["args"].get("keyColumns") or []
    synthesised = [k for k in keys if k not in FIXTURE_COLUMNS]
    print(f"\n  keyColumns: {keys}\n  tab columns: {sorted(FIXTURE_COLUMNS)}"
          f"\n  synthesised: {synthesised}")
    assert synthesised, (
        "every key column comes from a tab's header, so nothing distinguishes "
        f"one scenario's rows from another's: keyColumns={keys}"
    )


def test_the_agent_waited_for_each_load_before_sending_the_next_tab(run_agent):
    """One table loads one upload at a time, whatever the rows were read from."""
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
            "sent the next tab without ever seeing the previous load finish "
            f"(upload at line {previous['_line']} → upload at line "
            f"{following['_line']})\n" + summary(calls)
        )


def test_no_upload_was_rejected(run_agent):
    """Same two traps as the CSV run — the workbook does not defuse either."""
    calls = uploads(run_agent["entries"])
    refused = [c for c in calls if not (c["outcome"] or {}).get("accepted")]
    assert not refused, (
        f"{len(refused)} upload(s) were rejected; the union-first and "
        "discriminator steps exist to prevent exactly this\n" + summary(calls)
    )


def test_every_source_row_arrived(run_agent):
    """No tab silently dropped, no header row loaded as data.

    Reading a workbook adds failure modes a CSV read does not have: a sheet
    skipped because iteration stopped at the first one, a header repeated into
    the rows, trailing empty cells counted as a row.
    """
    calls = accepted(run_agent["entries"])
    sent = sum(rows_of(c["args"].get("csv")) for c in calls)
    print(f"\n  rows sent: {sent}, fixture: {FIXTURE_ROWS}")
    assert sent == FIXTURE_ROWS, (
        f"uploaded {sent} rows, the workbook holds {FIXTURE_ROWS}\n" + summary(calls)
    )


# ---------------------------------------------------------------------------
# The workbook itself. Everything above this line treats the .xlsx as a
# container to get rows out of; these treat it as the artifact to keep.
#
# The refusals backing these assertions are proved to fire in
# tests/test_fake_mcp_rules.py — an invented path, a path minted for another
# table, a nameless workbook. Without those, "the agent passed a path back"
# would pass against a server that accepts any string at all.
#
# THESE FOUR DO NOT MEASURE THE SKILL TEXT, and the run that added them proved
# it: with skills/ reverted to main and only the tool descriptions in play, all
# four still passed. The agent reads "upload it first with tables.source.prepare"
# off the tool itself and complies. So read a green run here as a REGRESSION
# GUARD on the tool contract — that the prepare/PUT/pass-back loop stays workable
# and that nothing later breaks it — and never as evidence that a change to
# skills/ was necessary or sufficient. What the skills add beyond this is the
# judgement the tool cannot state: which sources want a workbook at all (uploads
# step 5's table), and that the label must still carry sheet and range. Those are
# pinned in tests/test_uploads_skill_contract.py, which does fail against main.
#
# If you add an assertion here, run it once with skills/ stashed before
# believing it. Three of this repo's e2e assertions have now turned out to pass
# identically with and without the skill change they were written to defend.
# ---------------------------------------------------------------------------


def prepares(entries):
    return [e for e in entries if e.get("kind") == "source_prepare"]


def test_the_workbook_was_uploaded_not_just_converted(run_agent):
    """The whole point of the change: a converted workbook must not be the end
    of the provenance chain. Before this, the agent's own CSVs were the only
    thing Dough kept and the book itself was discarded at the end of the run."""
    minted = prepares(run_agent["entries"])
    assert minted, (
        "the agent never called tables.source.prepare, so the workbook it parsed "
        "was discarded and only its CSVs survive"
    )
    assert run_agent["put"], (
        "a path was prepared but no bytes were ever PUT to the sink — the agent "
        "prepared an upload and then did not perform it"
    )


def test_the_bytes_that_landed_are_the_workbook(run_agent):
    """Guards the failure that looks identical in the log: PUTting the CSV, or an
    empty body, to a URL minted for the workbook."""
    expected = hashlib.sha256((run_agent["work"] / WORKBOOK).read_bytes()).hexdigest()
    digests = {o["sha256"] for o in run_agent["put"].values()}
    assert expected in digests, (
        f"none of the {len(digests)} uploaded object(s) is the workbook — "
        "something else was sent to the prepared URL"
    )


def test_every_upload_carries_the_workbook_it_came_from(run_agent):
    """One book, three tabs, three uploads: each attaches it.

    Attaching it to only the first is the plausible half-measure, and it leaves
    two of the three versions pointing at nothing.
    """
    calls = accepted(run_agent["entries"])
    attached = [c["args"].get("sourceWorkbook") for c in calls]
    print(f"\n  workbooks: {attached}")
    assert all(attached), (
        f"{sum(1 for a in attached if not a)} of {len(calls)} uploads carried no "
        "sourceWorkbook"
    )
    minted = {e["objectPath"] for e in prepares(run_agent["entries"])}
    for workbook in attached:
        assert workbook["objectPath"] in minted, (
            f"an upload passed an objectPath nobody prepared: {workbook['objectPath']}"
        )


def test_the_label_still_names_the_tab_now_that_a_file_is_attached(run_agent):
    """The half of provenance the bytes cannot carry.

    A workbook cannot say which of its sheets became these rows, so attaching it
    makes the tab name MORE load-bearing, not less. The risk is an agent that
    treats the attached file as having answered the question and falls back to
    labelling every upload with the book's name.
    """
    calls = accepted(run_agent["entries"])
    labels = [(c["args"].get("sourceLabel") or "").strip() for c in calls]
    tabs = [tab for _, tab, _ in SOURCES]
    for tab in tabs:
        assert any(tab.lower() in label.lower() for label in labels), (
            f'no sourceLabel names the tab "{tab}": {labels}'
        )
