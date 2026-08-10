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
import io
import json
import os
import subprocess
import sys
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


@pytest.fixture(scope="module")
def run_agent(tmp_path_factory):
    work = tmp_path_factory.mktemp("planning_week_xlsx_e2e")
    build_workbook(work / WORKBOOK)

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
    if not entries:
        pytest.fail(
            "the agent made no MCP calls\n"
            f"--- stdout ---\n{result.stdout[-2000:]}\n"
            f"--- stderr ---\n{result.stderr[-1000:]}"
        )
    return {"work": work, "entries": entries, "result": result}


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
