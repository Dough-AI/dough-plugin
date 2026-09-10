"""End-to-end: the real model, the live Dough MCP, real Google Sheets.

`test_google_sheets_skill_contract.py` checks that the words are on the page and
`test_dough_sheets_live.py` checks that the script keeps the contract. This
checks the thing neither can: that a model asked for saved queries in a Google
Sheet produces a spreadsheet the contract describes, and that the formulas a
person builds on top of it survive a refresh.

It costs tokens, takes several minutes, and needs both a Dough login and a
connected gws, so it is opt-in:

    DOUGH_E2E=1 python3 -m pytest tests/test_google_sheets_e2e.py -v -s

Set DOUGH_KEEP_SHEETS=1 to leave the spreadsheet in Drive afterwards.

The prompt does not name the skill. A person asking for a Google Sheet will not
either, so whether the skill's description gets it loaded is part of what is
under test.

Assertions are invariants, not transcripts: the model may lay the summary tab
out however it likes, page the query however it likes, and word its report
however it likes. What must hold is that the manifest names the real saved
queries, every managed tab carries the banner and exactly its rows, nothing is
merged, the summary's SUMIFS agree with the managed tab, and after a refresh
those same cells still hold numbers rather than reference errors.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
from pathlib import Path

import pytest

from sheets_live import (
    REPO,
    add_tab,
    delete_file,
    find_spreadsheet,
    is_error_cell,
    keep_sheets,
    manifest,
    quote_tab,
    requires_gws,
    spreadsheet_id_from,
    tabs,
    ensure_test_folder,
    url_of,
    values,
    write_values,
)

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("DOUGH_E2E"),
        reason="costs tokens and needs auth; set DOUGH_E2E=1 to run",
    ),
    requires_gws,
]

# Three of the org's real saved queries, modest in size so paging them through
# the conversation is cheap. The first is account grain and its own description
# says a summary tab aggregates it via SUMIFS — the pattern under test.
MOM_BY_ACCOUNT = "40d4da26-496a-49cf-b80e-f31823c17a6a"
REVENUE_BY_MONTH = "a7f82945-1c4e-41bf-91f6-89ec33aa0bf8"  # parameterised
OPEX_BY_DEPARTMENT = "9f5cc0cc-3a63-4123-bbba-a7f360b2f922"
QUERY_IDS = {MOM_BY_ACCOUNT, REVENUE_BY_MONTH, OPEX_BY_DEPARTMENT}

MAPPING_COLUMN = "audit_mappings"
MONTH_COLUMN = "apr_2026"

# A tab the TEST adds between the two turns, standing in for the analyst who
# builds on the sheet after it is handed over. It must survive the refresh and
# its formula must still compute — which rules out "refresh" implemented as
# replacing the spreadsheet or its tabs.
ANALYST_TAB = "Analyst notes"

MCP_TOOLS = [
    "mcp__dough__tools__guide",
    "mcp__dough__tools__list",
    "mcp__dough__tools__describe",
    "mcp__dough__queries__list",
    "mcp__dough__queries__get",
    "mcp__dough__integrations__query",
    "mcp__dough__integrations__query__next",
]


def prompt_create(title: str, folder: str) -> str:
    return f"""I want three of our saved Dough queries in a new Google Sheet, so the
finance team can build on them.

Create the spreadsheet in Google Drive folder {folder} (that folder already
exists and this machine's gws can write to it), titled exactly "{title}".

The saved queries, by id:
- {MOM_BY_ACCOUNT}  (Audit P&L — MoM by Account)
- {REVENUE_BY_MONTH}  (Revenue and margin by month; use its default parameters)
- {OPEX_BY_DEPARTMENT}  (Operating expenses by department; use its default parameters)

Then add a summary tab that totals the MoM by Account data by audit mapping for
each month, using SUMIFS formulas over that tab rather than pasted numbers, so
the totals keep working when the data is refreshed later.

Work autonomously: don't stop to ask me questions. When you are done, tell me
what you did and give me the spreadsheet URL."""


def prompt_refresh(url: str) -> str:
    return f"""Refresh every Dough-managed tab in this Google Sheet with the latest data:
{url}

Work autonomously: don't stop to ask me questions, and tell me what you did at
the end."""


def run_claude(prompt: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "claude", "-p", prompt,
            # This repo IS the plugin, so the agent reads the real SKILL.md
            # files. The plugin's own .mcp.json points at the live Dough
            # server, which is what this run wants.
            "--plugin-dir", str(REPO),
            "--allowedTools", "Bash", "Read", "Write", "Edit", "Skill", *MCP_TOOLS,
            "--permission-mode", "bypassPermissions",
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=1200,
    )


def managed_tab_rows(spreadsheet_id: str, tab: str) -> tuple[list[str], list[list]]:
    grid = values(spreadsheet_id, f"{quote_tab(tab)}!A1:Z")
    headers = [str(c) for c in grid[1]] if len(grid) > 1 else []
    return headers, grid[2:]


def totals_by_mapping(headers: list[str], rows: list[list], month: str) -> dict[str, float]:
    mapping_at, month_at = headers.index(MAPPING_COLUMN), headers.index(month)
    totals: dict[str, float] = {}
    for row in rows:
        if len(row) <= max(mapping_at, month_at):
            continue
        amount = row[month_at]
        if isinstance(amount, (int, float)):
            totals[str(row[mapping_at])] = totals.get(str(row[mapping_at]), 0.0) + float(amount)
    return totals


def sumifs_cells(spreadsheet_id: str, tab: str) -> list[tuple[int, int, str]]:
    """(row, col, formula) for every cell on `tab` whose formula uses SUMIF."""
    found = []
    for r, row in enumerate(values(spreadsheet_id, f"{quote_tab(tab)}!A1:Z", render="FORMULA")):
        for c, cell in enumerate(row):
            if isinstance(cell, str) and "SUMIF" in cell.upper():
                found.append((r, c, cell))
    return found


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    work = tmp_path_factory.mktemp("google_sheets_e2e")
    folder = ensure_test_folder()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H%M%S")
    title = f"dough e2e {stamp}"

    first = run_claude(prompt_create(title, folder), work)
    (work / "agent-turn1.txt").write_text(first.stdout + "\n--- stderr ---\n" + first.stderr, encoding="utf-8")
    print(f"\n  run artifacts: {work}")

    spreadsheet = find_spreadsheet(title, folder) or spreadsheet_id_from(first.stdout)
    if not spreadsheet:
        pytest.fail(
            "no spreadsheet was created\n"
            f"--- stdout ---\n{first.stdout[-3000:]}\n"
            f"--- stderr ---\n{first.stderr[-1000:]}"
        )
    print(f"  spreadsheet: {url_of(spreadsheet)}")

    state = {"work": work, "spreadsheet": spreadsheet, "first": first}
    try:
        state["before"] = snapshot(spreadsheet)
        mom = next((name for name, row in state["before"]["managed"].items() if row["query_id"] == MOM_BY_ACCOUNT), None)
        if mom:
            add_tab(spreadsheet, ANALYST_TAB)
            write_values(spreadsheet, f"{quote_tab(ANALYST_TAB)}!A1:B1", [[
                "rows in MoM by Account",
                f"=COUNTA({quote_tab(mom)}!A:A)-2",
            ]])
            state["analyst_before"] = values(spreadsheet, f"{quote_tab(ANALYST_TAB)}!B1")
        second = run_claude(prompt_refresh(url_of(spreadsheet)), work)
        (work / "agent-turn2.txt").write_text(second.stdout + "\n--- stderr ---\n" + second.stderr, encoding="utf-8")
        state["second"] = second
        state["after"] = snapshot(spreadsheet)
        yield state
    finally:
        if keep_sheets():
            print(f"  kept: {url_of(spreadsheet)}")
        else:
            delete_file(spreadsheet)


def snapshot(spreadsheet: str) -> dict:
    """Everything the assertions read, taken once so a failing assertion does not
    re-hit the API and so turn 2's changes are compared against turn 1's state."""
    tab_list = tabs(spreadsheet)
    man = manifest(spreadsheet) if any(t["title"] == "Dough" for t in tab_list) else {"marker": "", "headers": [], "rows": []}
    managed = {row["sheet"]: row for row in man["rows"]}
    data = {name: managed_tab_rows(spreadsheet, name) for name in managed if any(t["title"] == name for t in tab_list)}
    banners = {name: (values(spreadsheet, f"{quote_tab(name)}!A1") or [[""]])[0][0] for name in data}
    others = [t["title"] for t in tab_list if t["title"] != "Dough" and t["title"] not in managed]
    formulas = {name: sumifs_cells(spreadsheet, name) for name in others}
    summary = next((name for name, cells in formulas.items() if cells), None)
    computed = {}
    if summary:
        grid = values(spreadsheet, f"{quote_tab(summary)}!A1:Z")
        for r, c, _ in formulas[summary]:
            computed[(r, c)] = grid[r][c] if r < len(grid) and c < len(grid[r]) else None
    return {
        "tabs": tab_list,
        "manifest": man,
        "managed": managed,
        "data": data,
        "banners": banners,
        "summary": summary,
        "formulas": formulas.get(summary, []),
        "computed": computed,
    }


def test_manifest_names_the_real_saved_queries(run):
    """The `Dough` tab is the contract's whole point: a reader learns what
    produced each tab from the spreadsheet itself, not from a chat log."""
    before = run["before"]
    assert before["manifest"]["marker"].startswith("dough-manifest v1"), before["manifest"]["marker"]
    ids = {row["query_id"] for row in before["manifest"]["rows"]}
    assert ids == QUERY_IDS, f"manifest query_ids {ids}"


def test_each_managed_tab_carries_the_banner_and_exactly_its_rows(run):
    before = run["before"]
    assert set(before["data"]) == set(before["managed"]), (
        f"manifest names {set(before['managed'])} but tabs are {set(before['data'])}"
    )
    for name, (headers, rows) in before["data"].items():
        assert "Managed by Dough" in str(before["banners"][name]), f"{name}: A1 is {before['banners'][name]!r}"
        assert headers and all(headers), f"{name}: row 2 is not a header row: {headers}"
        assert len(rows) == int(float(before["managed"][name]["row_count"])), (
            f"{name}: {len(rows)} data rows but manifest says {before['managed'][name]['row_count']}"
        )


def test_nothing_is_merged(run):
    """Merged ranges break sorting, filtering, and programmatic reads. The
    banner is one cell overflowing across a filled band, never a merge."""
    merged = {t["title"]: t["merges"] for t in run["before"]["tabs"] if t["merges"]}
    assert not merged, merged


def test_parameterised_query_records_its_parameters(run):
    """Manifest v1 has no params column, so refresh_notes has to say what the
    rows were run with — otherwise a refresh cannot reproduce them."""
    row = next(r for r in run["before"]["manifest"]["rows"] if r["query_id"] == REVENUE_BY_MONTH)
    notes = str(row["refresh_notes"]).lower()
    assert "period_start" in notes or "2025-08-01" in notes, f"refresh_notes: {row['refresh_notes']!r}"


def test_summary_sumifs_agree_with_the_managed_tab(run):
    before = run["before"]
    assert before["summary"], f"no tab with SUMIFS formulas; tabs: {[t['title'] for t in before['tabs']]}"
    mom = next(name for name, row in before["managed"].items() if row["query_id"] == MOM_BY_ACCOUNT)
    headers, rows = before["data"][mom]
    expected = totals_by_mapping(headers, rows, MONTH_COLUMN)
    assert expected, f"{mom}: no {MAPPING_COLUMN}/{MONTH_COLUMN} totals in {headers}"
    computed = [v for v in before["computed"].values() if isinstance(v, (int, float))]
    print(f"\n  summary tab: {before['summary']} · {len(before['formulas'])} SUMIFS cells")
    missing = {
        mapping: total
        for mapping, total in expected.items()
        if not any(abs(total - v) < 0.01 for v in computed)
    }
    assert not missing, f"SUMIFS totals for {MONTH_COLUMN} missing from the summary tab: {missing}"


def test_refresh_kept_the_formulas_working(run):
    """The Sheets-specific invariant. Deleting a tab turns every reference to
    it into an error, even if a tab of the same name is added back. Refresh
    must clear in place."""
    before, after = run["before"], run["after"]
    assert before["summary"] and after["computed"], "no summary formulas to compare"
    broken = {cell: v for cell, v in after["computed"].items() if is_error_cell(v) or v is None}
    assert not broken, f"after refresh, summary cells are errors: {broken}"
    mom = next(name for name, row in after["managed"].items() if row["query_id"] == MOM_BY_ACCOUNT)
    headers, rows = after["data"][mom]
    expected = totals_by_mapping(headers, rows, MONTH_COLUMN)
    computed = [v for v in after["computed"].values() if isinstance(v, (int, float))]
    missing = {m: t for m, t in expected.items() if not any(abs(t - v) < 0.01 for v in computed)}
    assert not missing, f"after refresh, SUMIFS no longer agree with the managed tab: {missing}"


def test_refresh_kept_the_tab_a_person_added(run):
    """Refresh replaces managed tabs and nothing else. A tab added after the
    handover, with a formula over a managed tab, is exactly what a wholesale
    re-upload of the spreadsheet destroys."""
    assert "analyst_before" in run, "the analyst tab was never added (no MoM tab in the manifest?)"
    after = run["after"]
    assert any(t["title"] == ANALYST_TAB for t in after["tabs"]), (
        f"refresh removed the {ANALYST_TAB!r} tab; tabs now: {[t['title'] for t in after['tabs']]}"
    )
    value = values(run["spreadsheet"], f"{quote_tab(ANALYST_TAB)}!B1")
    assert value and isinstance(value[0][0], (int, float)), f"analyst formula no longer computes: {value}"
    mom = next(name for name, row in after["managed"].items() if row["query_id"] == MOM_BY_ACCOUNT)
    # COUNTA counts non-empty cells, and a real ledger has rows with no account
    # number (a realised gain/loss line, say) — compare like with like.
    filled = sum(1 for row in after["data"][mom][1] if row and row[0] not in ("", None))
    assert value[0][0] == filled, f"analyst formula says {value[0][0]} rows, tab has {filled} with a first cell"


def test_refresh_moved_every_timestamp_forward(run):
    before = {r["sheet"]: r["last_refreshed"] for r in run["before"]["manifest"]["rows"]}
    after = {r["sheet"]: r["last_refreshed"] for r in run["after"]["manifest"]["rows"]}
    assert set(after) == set(before), f"refresh changed the set of managed tabs: {set(before)} -> {set(after)}"
    stale = {name: (before[name], after[name]) for name in before if not str(after[name]) > str(before[name])}
    assert not stale, f"last_refreshed did not move forward: {stale}"
    ids_after = {t["title"]: t["sheetId"] for t in run["after"]["tabs"]}
    ids_before = {t["title"]: t["sheetId"] for t in run["before"]["tabs"]}
    changed = {n: (ids_before[n], ids_after.get(n)) for n in before if ids_before.get(n) != ids_after.get(n)}
    assert not changed, f"refresh replaced tabs instead of clearing them (sheetId changed): {changed}"
