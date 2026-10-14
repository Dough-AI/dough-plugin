"""Offline: the Sheets script finds, and converges, the renamed manifest tab.

The manifest tab was `Dough` before plugin 0.31.0 and is `Dough Manifest` now.
`test_dough_sheets_live.py` exercises real Google; this pins the read and rename
decisions against a fake `gws` that keeps just enough spreadsheet state —
tab titles, sheetIds, positions and cell values — to tell a rename from a
delete-and-add.
"""

import argparse
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "skills" / "google-sheets" / "scripts" / "dough_sheets.py"


def load():
    spec = importlib.util.spec_from_file_location("dough_sheets_offline", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ds = load()

MANIFEST_GRID = [
    [ds.TITLE_TEXT],
    [ds.VERSION_MARKER],
    list(ds.MANIFEST_HEADERS),
    ["Revenue Detail", "q-1", "Revenue by month", "SELECT 1", "2026-07-25T14:03:00Z", 2, "USD"],
]


class FakeGws:
    """Records every call; serves spreadsheets.get / values.get from `tabs`."""

    binary = "gws"

    def __init__(self, tabs):
        # tabs: list of (title, sheetId, grid) in position order
        self.tabs = [{"title": t, "sheetId": i, "grid": g} for t, i, g in tabs]
        self.calls = []

    def call(self, *args, body=None, params=None, **_):
        self.calls.append((args, body, params))
        if args == ("sheets", "spreadsheets", "get"):
            return {"properties": {"title": "Book"}, "sheets": [
                {"properties": {"title": t["title"], "sheetId": t["sheetId"], "index": n,
                                "gridProperties": {"rowCount": 100, "columnCount": 7}}}
                for n, t in enumerate(self.tabs)]}
        if args == ("sheets", "spreadsheets", "values", "get"):
            title = params["range"].rsplit("!", 1)[0]
            assert title.startswith("'") and title.endswith("'"), f"unquoted A1 range: {params['range']}"
            tab = next(t for t in self.tabs if t["title"] == title[1:-1].replace("''", "'"))
            return {"values": tab["grid"]}
        if args == ("sheets", "spreadsheets", "batchUpdate"):
            replies = []
            for request in body["requests"]:
                if "updateSheetProperties" in request:
                    props = request["updateSheetProperties"]["properties"]
                    if "title" in props:
                        next(t for t in self.tabs if t["sheetId"] == props["sheetId"])["title"] = props["title"]
                    replies.append({})
                elif "addSheet" in request:
                    props = request["addSheet"]["properties"]
                    new_id = max([t["sheetId"] for t in self.tabs] + [0]) + 1
                    self.tabs.insert(props.get("index", len(self.tabs)), {"title": props["title"], "sheetId": new_id, "grid": []})
                    replies.append({"addSheet": {"properties": {"sheetId": new_id}}})
                elif "deleteSheet" in request:
                    self.tabs = [t for t in self.tabs if t["sheetId"] != request["deleteSheet"]["sheetId"]]
                    replies.append({})
                else:
                    replies.append({})
            return {"replies": replies}
        if args == ("sheets", "spreadsheets", "values", "update"):
            return {}
        raise AssertionError(f"unexpected gws call {args}")

    def requests(self, kind):
        return [r for args, body, _ in self.calls if args[-1] == "batchUpdate"
                for r in body["requests"] if kind in r]

    def titles(self):
        return [t["title"] for t in self.tabs]


def legacy_gws():
    return FakeGws([("Model", 7, [["x"]]), ("Dough", 42, MANIFEST_GRID), ("Revenue Detail", 9, [["banner"]])])


def test_reads_a_legacy_only_manifest():
    gws = legacy_gws()
    rows = ds.read_manifest(ds.Spreadsheet(gws, "sid"))
    assert [r["sheet"] for r in rows] == ["Revenue Detail"]
    assert gws.requests("updateSheetProperties") == []  # reading never writes


def test_both_names_present_reads_the_new_one(capsys):
    gws = FakeGws([("Dough", 1, [["junk"], ["not a marker"]]), ("Dough Manifest", 2, MANIFEST_GRID)])
    rows = ds.read_manifest(ds.Spreadsheet(gws, "sid"))
    assert [r["sheet"] for r in rows] == ["Revenue Detail"]
    assert "both" in capsys.readouterr().err


def test_missing_manifest_error_names_the_new_tab(capsys):
    with pytest.raises(SystemExit) as exit_info:
        ds.read_manifest(ds.Spreadsheet(FakeGws([("Sheet1", 0, [])]), "sid"))
    assert exit_info.value.code == 3
    assert "'Dough Manifest'" in capsys.readouterr().err


def test_adopt_renames_in_place_by_sheet_id():
    gws = legacy_gws()
    book = ds.Spreadsheet(gws, "sid")
    ds.adopt_legacy_manifest(book)
    assert gws.requests("updateSheetProperties") == [{"updateSheetProperties": {
        "properties": {"sheetId": 42, "title": "Dough Manifest"}, "fields": "title"}}]
    assert gws.requests("deleteSheet") == [] and gws.requests("addSheet") == []
    assert gws.titles() == ["Model", "Dough Manifest", "Revenue Detail"]  # position kept
    assert book.tabs["Dough Manifest"]["sheetId"] == 42                    # book reloaded
    ds.adopt_legacy_manifest(book)                                         # converged: no-op
    assert len(gws.requests("updateSheetProperties")) == 1


def test_adopt_leaves_a_legacy_tab_alone_when_the_new_one_exists():
    gws = FakeGws([("Dough", 1, [["user stuff"]]), ("Dough Manifest", 2, MANIFEST_GRID)])
    ds.adopt_legacy_manifest(ds.Spreadsheet(gws, "sid"))
    assert gws.requests("updateSheetProperties") == []
    assert gws.titles() == ["Dough", "Dough Manifest"]


def test_ensure_does_not_add_a_second_manifest_to_a_legacy_spreadsheet():
    gws = legacy_gws()
    ds.ensure_manifest_tab(ds.Spreadsheet(gws, "sid"))
    assert gws.requests("addSheet") == []


def test_ensure_adds_the_manifest_under_the_new_name():
    gws = FakeGws([("Sheet1", 0, [])])
    ds.ensure_manifest_tab(ds.Spreadsheet(gws, "sid"), drop_default=True)
    assert gws.titles() == ["Dough Manifest"]
    update = next(params for args, _, params in gws.calls if args[-1] == "update")
    assert update["range"].startswith("'Dough Manifest'!A1")


def run_command(monkeypatch, tmp_path, handler, gws, **extra):
    """Run a command handler with gws faked and the Google-side writes
    (staging upload, copy, paste) replaced by a recorder of the book state."""
    seen = {}

    def fake_write_all(book, entries, manifest_rows):
        seen["titles"] = list(book.tabs)
        return [f"{e['sheet']}: ok" for e in entries]

    monkeypatch.setattr(ds, "Gws", lambda: gws)
    monkeypatch.setattr(ds, "write_all", fake_write_all)
    csv_path = tmp_path / "r.csv"
    csv_path.write_text("month,revenue\n2026-01,100\n", encoding="utf-8")
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"entries": [{
        "sheet": "Revenue Detail", "queryId": "q-1", "queryName": "Revenue by month",
        "sqlSnapshot": "SELECT 1", "refreshedAt": "2026-08-01T09:00:00Z", "rowCount": 1,
        "csvPath": str(csv_path)}]}))
    args = argparse.Namespace(sheet="1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefg", payload=str(payload), sheets="", all=True, new=None, folder=None, **extra)
    handler(args)
    return seen


def test_refresh_renames_a_legacy_manifest_before_writing(monkeypatch, tmp_path):
    gws = legacy_gws()
    seen = run_command(monkeypatch, tmp_path, ds.cmd_refresh, gws)
    assert seen["titles"] == ["Model", "Dough Manifest", "Revenue Detail"]
    assert gws.requests("addSheet") == [] and gws.requests("deleteSheet") == []


def test_create_into_a_legacy_spreadsheet_renames_not_duplicates(monkeypatch, tmp_path):
    gws = legacy_gws()
    seen = run_command(monkeypatch, tmp_path, ds.cmd_create, gws)
    assert seen["titles"].count("Dough Manifest") == 1
    assert "Dough" not in seen["titles"]
    assert gws.requests("addSheet") == []


def test_banner_names_the_manifest_sheet():
    assert ds.BANNER_TEMPLATE.endswith("details on the 'Dough Manifest' sheet.")
