"""The google-sheets script against real Google Sheets.

Mirrors test_dough_excel.py case for case, plus the two things a spreadsheet
on Google has that a file on disk does not: a formula on another tab that must
survive a refresh (deleting a tab breaks every reference to it, so refresh has
to clear in place), and a request-size limit that forces big tabs to be written
in chunks.

Skips unless gws is connected. Each test gets its own spreadsheet under
`Dough / Dough sheets tests`, deleted afterwards unless DOUGH_KEEP_SHEETS=1.

    python3 -m pytest tests/test_dough_sheets_live.py -v
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys

import pytest

from sheets_live import (
    REPO,
    add_tab,
    create_spreadsheet,
    delete_file,
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

pytestmark = requires_gws

SCRIPT = REPO / "skills" / "google-sheets" / "scripts" / "dough_sheets.py"


def run(*argv):
    return subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, text=True)


def csv_path(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def payload(tmp_path, entries):
    p = tmp_path / "payload.json"
    p.write_text(json.dumps({"entries": entries}))
    return str(p)


def entry(tmp_path, sheet="Revenue Detail", rows="month,revenue\n2026-01,100\n2026-02,200\n", **overrides):
    base = {
        "sheet": sheet,
        "queryId": "q-1",
        "queryName": "Revenue by month",
        "sqlSnapshot": "SELECT month, revenue FROM finance.revenue",
        "refreshedAt": "2026-07-25T14:03:00Z",
        "rowCount": rows.count("\n") - 1,
        "csvPath": csv_path(tmp_path, f"{sheet}.csv", rows),
        "refreshNotes": "USD, sorted by month",
    }
    return {**base, **overrides}


@pytest.fixture(scope="module")
def folder():
    return ensure_test_folder()


@pytest.fixture
def sheet(folder, request):
    """A fresh spreadsheet with Google's default `Sheet1`, like a person made it."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%H%M%S")
    spreadsheet = create_spreadsheet(f"dough-sheets-test {stamp} {request.node.name}", folder)
    yield spreadsheet
    if keep_sheets():
        print(f"\n  kept: {url_of(spreadsheet)}")
    else:
        delete_file(spreadsheet)


def tab(spreadsheet, title):
    return next((t for t in tabs(spreadsheet) if t["title"] == title), None)


def grid(spreadsheet, title):
    return values(spreadsheet, f"{quote_tab(title)}!A1:Z")


def test_create_into_existing_spreadsheet(tmp_path, sheet):
    result = run("create", url_of(sheet), "--payload", payload(tmp_path, [entry(tmp_path)]))
    assert result.returncode == 0, result.stderr
    titles = [t["title"] for t in tabs(sheet)]
    assert titles[0] == "Dough"
    assert "Revenue Detail" in titles
    assert "Sheet1" in titles, "the tab the spreadsheet came with must survive"
    man = manifest(sheet)
    assert man["marker"] == "dough-manifest v1"
    assert man["headers"] == ["sheet", "query_id", "query_name", "sql_snapshot", "last_refreshed", "row_count", "refresh_notes"]
    assert man["rows"][0]["sheet"] == "Revenue Detail"
    assert man["rows"][0]["query_id"] == "q-1"
    assert man["rows"][0]["last_refreshed"] == "2026-07-25T14:03:00Z"
    assert man["rows"][0]["row_count"] == 2  # numeric, not "2"
    data = grid(sheet, "Revenue Detail")
    assert "Managed by Dough" in data[0][0]
    assert data[1] == ["month", "revenue"]
    assert data[2] == ["2026-01", 100]  # number coerced from the CSV string; month stays text
    assert not any(t["merges"] for t in tabs(sheet))
    assert "Revenue Detail: 2 rows" in result.stdout


def test_create_new_spreadsheet_prints_its_url(tmp_path, folder):
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%H%M%S")
    title = f"dough-sheets-test {stamp} created-by-script"
    result = run("create", "--new", title, "--folder", folder, "--payload", payload(tmp_path, [entry(tmp_path)]))
    assert result.returncode == 0, result.stderr
    spreadsheet = spreadsheet_id_from(result.stdout.splitlines()[0])
    assert spreadsheet, f"first stdout line should be the URL: {result.stdout!r}"
    try:
        assert [t["title"] for t in tabs(spreadsheet)] == ["Dough", "Revenue Detail"], "no stray default tab"
        assert manifest(spreadsheet)["rows"][0]["sheet"] == "Revenue Detail"
    finally:
        if not keep_sheets():
            delete_file(spreadsheet)


def test_manifest_and_data_tab_formatting(tmp_path, sheet):
    """The parts of the contract a reader sees before reading a single value."""
    run("create", url_of(sheet), "--payload", payload(tmp_path, [entry(tmp_path)]))
    dough = tab(sheet, "Dough")
    assert dough["gridProperties"]["frozenRowCount"] == 3
    assert dough["gridProperties"]["columnCount"] == 7
    detail = tab(sheet, "Revenue Detail")
    color = detail["tabColor"]
    assert tuple(round(color[c] * 255) for c in ("red", "green", "blue")) == (0x25, 0x63, 0xEB)
    assert detail["gridProperties"]["columnCount"] == 2
    assert detail["gridProperties"]["rowCount"] == 4, "banner + header + 2 data rows, nothing else"


def test_refresh_shrunk_rows_and_mangled_banner_restored(tmp_path, sheet):
    run("create", url_of(sheet), "--payload", payload(tmp_path, [entry(tmp_path)]))
    write_values(sheet, "'Revenue Detail'!A1", [["user typed over the banner"]], input_option="RAW")
    shorter = entry(tmp_path, rows="month,revenue\n2026-03,300\n", refreshedAt="2026-08-01T09:00:00Z")
    result = run("refresh", url_of(sheet), "--all", "--payload", payload(tmp_path, [shorter]))
    assert result.returncode == 0, result.stderr
    data = grid(sheet, "Revenue Detail")
    assert "Managed by Dough" in data[0][0]  # banner self-healed
    assert "2026-08-01" in data[0][0]        # with the new date
    assert data[2] == ["2026-03", 300]
    assert len(data) == 3                    # old rows gone
    man = manifest(sheet)
    assert len(man["rows"]) == 1             # updated in place, not appended
    assert man["rows"][0]["last_refreshed"] == "2026-08-01T09:00:00Z"
    assert man["rows"][0]["row_count"] == 1


def test_refresh_keeps_formulas_on_other_tabs_working(tmp_path, sheet):
    """Deleting a tab turns every formula that references it into an error,
    permanently — adding a tab of the same name back does not repair it. So
    refresh clears the tab in place and its sheetId never changes."""
    run("create", url_of(sheet), "--payload", payload(tmp_path, [entry(tmp_path)]))
    add_tab(sheet, "Model")
    write_values(sheet, "Model!A1", [["=SUMIFS('Revenue Detail'!B:B,'Revenue Detail'!A:A,\"2026-02\")"]])
    assert values(sheet, "Model!A1") == [[200]]
    id_before = tab(sheet, "Revenue Detail")["sheetId"]

    bigger = entry(tmp_path, rows="month,revenue\n2026-01,100\n2026-02,250\n2026-03,300\n", refreshedAt="2026-08-01T09:00:00Z")
    result = run("refresh", url_of(sheet), "--all", "--payload", payload(tmp_path, [bigger]))
    assert result.returncode == 0, result.stderr
    assert tab(sheet, "Revenue Detail")["sheetId"] == id_before
    assert values(sheet, "Model!A1") == [[250]], "formula must still point at the refreshed tab"
    assert tab(sheet, "Model") is not None


def test_refresh_recreates_a_deleted_managed_tab(tmp_path, sheet):
    run("create", url_of(sheet), "--payload", payload(tmp_path, [entry(tmp_path)]))
    detail = tab(sheet, "Revenue Detail")
    subprocess.run(
        ["gws", "sheets", "spreadsheets", "batchUpdate", "--params", json.dumps({"spreadsheetId": sheet}),
         "--json", json.dumps({"requests": [{"deleteSheet": {"sheetId": detail["sheetId"]}}]})],
        capture_output=True, check=True,
    )
    result = run("refresh", url_of(sheet), "--all", "--payload", payload(tmp_path, [entry(tmp_path)]))
    assert result.returncode == 0, result.stderr
    assert "recreating" in result.stderr
    assert grid(sheet, "Revenue Detail")[2] == ["2026-01", 100]


def test_refresh_unknown_sheet_is_drift(tmp_path, sheet):
    run("create", url_of(sheet), "--payload", payload(tmp_path, [entry(tmp_path)]))
    rogue = entry(tmp_path, sheet="Not In Manifest")
    result = run("refresh", url_of(sheet), "--all", "--payload", payload(tmp_path, [rogue]))
    assert result.returncode == 3
    assert "reconcile" in result.stderr


def test_list_reads_the_manifest(tmp_path, sheet):
    run("create", url_of(sheet), "--payload", payload(tmp_path, [entry(tmp_path)]))
    result = run("list", url_of(sheet))
    assert result.returncode == 0, result.stderr
    listed = json.loads(result.stdout)
    assert listed["version"] == "dough-manifest v1"
    assert listed["entries"][0]["sheet"] == "Revenue Detail"
    assert listed["entries"][0]["query_id"] == "q-1"


def test_bare_id_and_url_both_work(tmp_path, sheet):
    run("create", sheet, "--payload", payload(tmp_path, [entry(tmp_path)]))
    assert run("list", url_of(sheet)).returncode == 0


def test_newer_manifest_version_stops(tmp_path, sheet):
    run("create", url_of(sheet), "--payload", payload(tmp_path, [entry(tmp_path)]))
    write_values(sheet, "Dough!A2", [["dough-manifest v2"]], input_option="RAW")
    result = run("list", url_of(sheet))
    assert result.returncode == 3
    assert "update the Dough plugin" in result.stderr


def test_older_manifest_version_says_migrate_not_update(tmp_path, sheet):
    run("create", url_of(sheet), "--payload", payload(tmp_path, [entry(tmp_path)]))
    write_values(sheet, "Dough!A2", [["dough-manifest v0"]], input_option="RAW")
    result = run("list", url_of(sheet))
    assert result.returncode == 3
    assert "predates" in result.stderr


def test_not_a_managed_spreadsheet(sheet):
    result = run("list", url_of(sheet))
    assert result.returncode == 3
    assert "not a Dough-managed" in result.stderr


def test_big_tab_lands_whole(tmp_path, sheet):
    """`gws --json` takes the body inline on the command line; Windows caps a
    command line at 32K characters and an endpoint-security agent on managed
    Macs kills gws past ~900. A tab this size cannot travel that way at all,
    which is why the script stages content through a Drive upload — and every
    row has to arrive, typed."""
    n = 1500
    lines = ["period,cost_center,region,amount,memo"]
    lines += [f"2026-{1 + i % 12:02d}-01,CC{i % 40:03d},{'EMEA' if i % 2 else 'AMER'},{i * 1.25:.2f},line {i}" for i in range(n)]
    big = entry(tmp_path, sheet="Ledger", rows="\n".join(lines) + "\n")
    result = run("create", url_of(sheet), "--payload", payload(tmp_path, [big]))
    assert result.returncode == 0, result.stderr
    data = values(sheet, "Ledger!A1:E")
    assert len(data) == n + 2
    assert data[2] == ["2026-01-01", "CC000", "AMER", 0, "line 0"]
    assert data[-1] == [f"2026-{1 + (n - 1) % 12:02d}-01", f"CC{(n - 1) % 40:03d}", "EMEA", (n - 1) * 1.25, f"line {n - 1}"]
    assert sum(1 for row in data[2:] if len(row) == 5) == n, "a row was dropped or truncated"


def test_gws_missing_is_a_clear_failure(tmp_path, sheet, monkeypatch):
    monkeypatch.setenv("GWS_BIN", str(tmp_path / "no-such-gws"))
    monkeypatch.setenv("PATH", str(tmp_path))
    result = run("list", url_of(sheet))
    assert result.returncode == 4
    assert "gws-connect" in result.stderr
