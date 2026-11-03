"""Offline: `put-tab` writes values into an ordinary tab, in place, and never
into a managed one.

`test_dough_sheets_live.py` exercises real Google; this pins the decisions —
what is refused, which requests are sent, and that the target tab keeps its
sheetId — against a fake `gws` and a fake staging upload.
"""

import argparse
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "skills" / "google-sheets" / "scripts" / "dough_sheets.py"


def load():
    spec = importlib.util.spec_from_file_location("dough_sheets_put_tab", SCRIPT)
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
BANNER = ds.BANNER_TEMPLATE.format(date="2026-07-25")
COPY_ID = 900


class FakeGws:
    """Serves spreadsheets.get / values.get from `tabs`; applies addSheet and
    deleteSheet; answers copyTo with a fresh tab; records every call."""

    binary = "gws"

    def __init__(self, tabs):
        # tabs: (title, sheetId, grid, rowCount, columnCount) in position order
        self.tabs = [{"title": t, "sheetId": i, "grid": g, "rows": r, "cols": c} for t, i, g, r, c in tabs]
        self.calls = []

    def call(self, *args, body=None, params=None, **_):
        self.calls.append((args, body, params))
        if args == ("sheets", "spreadsheets", "get"):
            return {"properties": {"title": "Book"}, "sheets": [
                {"properties": {"title": t["title"], "sheetId": t["sheetId"], "index": n,
                                "gridProperties": {"rowCount": t["rows"], "columnCount": t["cols"]}}}
                for n, t in enumerate(self.tabs)]}
        if args == ("sheets", "spreadsheets", "values", "get"):
            title = params["range"].rsplit("!", 1)[0][1:-1].replace("''", "'")
            return {"values": next(t for t in self.tabs if t["title"] == title)["grid"]}
        if args == ("sheets", "spreadsheets", "sheets", "copyTo"):
            copy_id = COPY_ID + len(self.calls)
            self.tabs.append({"title": f"Copy {copy_id}", "sheetId": copy_id, "grid": [], "rows": 1000, "cols": 26})
            return {"sheetId": copy_id, "title": f"Copy {copy_id}"}
        if args == ("sheets", "spreadsheets", "batchUpdate"):
            replies = []
            for request in body["requests"]:
                if "addSheet" in request:
                    props = request["addSheet"]["properties"]
                    new_id = max(t["sheetId"] for t in self.tabs) + 1
                    grid = props["gridProperties"]
                    self.tabs.insert(props.get("index", len(self.tabs)), {
                        "title": props["title"], "sheetId": new_id, "grid": [],
                        "rows": grid["rowCount"], "cols": grid["columnCount"]})
                    replies.append({"addSheet": {"properties": {"sheetId": new_id}}})
                elif "deleteSheet" in request:
                    self.tabs = [t for t in self.tabs if t["sheetId"] != request["deleteSheet"]["sheetId"]]
                    replies.append({})
                else:
                    replies.append({})
            return {"replies": replies}
        raise AssertionError(f"unexpected gws call {args}")

    def requests(self, kind=None):
        return [r for args, body, _ in self.calls if args[-1] == "batchUpdate"
                for r in body["requests"] if kind is None or kind in r]

    def tab(self, title):
        return next((t for t in self.tabs if t["title"] == title), None)


@pytest.fixture
def staged(monkeypatch):
    """Replace the Drive upload: record what would be staged, copy from a fixed id."""
    record = {"sheets": None, "closed": False}

    def self_sheets():
        return record["sheets"]

    class FakeStaging:
        def __init__(self, gws, sheets):
            self.gws = gws
            record["sheets"] = sheets
            record["uploads"] = record.get("uploads", 0) + 1

        def copy_into(self, book, name):
            assert name in self_sheets(), name
            return self.gws.call("sheets", "spreadsheets", "sheets", "copyTo",
                                 params={"spreadsheetId": "staging", "sheetId": 1},
                                 body={"destinationSpreadsheetId": book.sid})

        def close(self):
            record["closed"] = True

    monkeypatch.setattr(ds, "Staging", FakeStaging)
    return record


def book_with(*extra, manifest=True):
    tabs = [("Dough Manifest", 1, MANIFEST_GRID, 100, 7), ("Revenue Detail", 2, [[BANNER]], 4, 2)] if manifest else []
    tabs += list(extra)
    gws = FakeGws(tabs)
    return gws, ds.Spreadsheet(gws, "sid")


def put(book, title, grid, start_row, index):
    return ds.put_tabs(book, [{"tab": title, "grid": grid, "startRow": start_row, "index": index}])


def exit_code(fn, *args):
    with pytest.raises(SystemExit) as info:
        fn(*args)
    return info.value.code


# --- refusals -----------------------------------------------------------------

@pytest.mark.parametrize("title", ["Dough Manifest", "Dough"])
def test_refuses_the_manifest_tab(title, capsys):
    _, book = book_with()
    assert exit_code(ds.refuse_managed, book, title) == 2
    assert "manifest" in capsys.readouterr().err


def test_refuses_a_managed_tab(capsys):
    _, book = book_with()
    assert exit_code(ds.refuse_managed, book, "Revenue Detail") == 2
    assert "Dough-managed" in capsys.readouterr().err


def test_refuses_a_banner_tab_missing_from_the_manifest(capsys):
    """A banner-carrying tab the manifest doesn't name is drift; writing over it
    would hide the drift instead of surfacing it."""
    _, book = book_with(("Orphan", 3, [[BANNER], ["a"]], 10, 2))
    assert exit_code(ds.refuse_managed, book, "Orphan") == 2
    assert "drift" in capsys.readouterr().err


def test_allows_an_ordinary_tab_and_a_new_one():
    _, book = book_with(("Actions", 3, [["Item ID"]], 10, 2))
    ds.refuse_managed(book, "Actions")
    ds.refuse_managed(book, "Not There Yet")


def test_allows_a_spreadsheet_with_no_manifest():
    _, book = book_with(("Sheet1", 0, [], 1000, 26), manifest=False)
    ds.refuse_managed(book, "Sheet1")


# --- the grid -----------------------------------------------------------------

def test_grid_keeps_inner_blank_rows_and_types_values(tmp_path):
    path = tmp_path / "g.csv"
    path.write_text("Item ID,Amount,Code\nstale:1,12.50,0100\n\nTotal,12.5,\n\n\n", encoding="utf-8")
    assert ds.read_plain_grid(str(path)) == [
        ["Item ID", "Amount", "Code"],
        ["stale:1", 12.5, "0100"],  # '0100' stays text, as in create
        [],
        ["Total", 12.5, ""],
    ]


def test_empty_csv_is_refused(tmp_path):
    path = tmp_path / "g.csv"
    path.write_text("\n\n", encoding="utf-8")
    assert exit_code(ds.read_plain_grid, str(path)) == 2


# --- writing ------------------------------------------------------------------

def test_new_tab_is_added_at_the_index_and_pasted_bounded(staged):
    gws, book = book_with()
    grid = [["Item ID", "Amount"], ["stale:1", 10], ["stale:2", 20]]
    put(book, "Actions", grid, start_row=4, index=0)

    add = gws.requests("addSheet")[0]["addSheet"]["properties"]
    assert add["title"] == "Actions" and add["index"] == 0
    assert add["gridProperties"] == {"rowCount": 1000, "columnCount": 26}, "room for formulas beside the values"
    assert staged["sheets"] == {"g1": grid}
    paste = gws.requests("copyPaste")[0]["copyPaste"]
    actions_id = gws.tab("Actions")["sheetId"]
    assert paste["pasteType"] == "PASTE_VALUES"
    assert paste["destination"] == {"sheetId": actions_id, "startRowIndex": 3, "endRowIndex": 6,
                                    "startColumnIndex": 0, "endColumnIndex": 2}
    assert not any(t["title"].startswith("Copy ") for t in gws.tabs), "the copied staging tab must be deleted"
    assert staged["closed"]
    clear = gws.requests("updateCells")[0]["updateCells"]["range"]
    assert (clear["startRowIndex"], clear["endColumnIndex"]) == (6, 2), "only below the paste, only its columns"


def test_existing_tab_is_written_in_place_and_stale_rows_cleared(staged):
    """Same sheetId (formulas elsewhere keep pointing at it); only the CSV's
    columns below the paste are cleared, and only their values."""
    gws, book = book_with(("Actions", 7, [["old"]], 50, 16))
    put(book, "Actions", [["a", 1], ["b", 2]], start_row=4, index=None)

    assert gws.tab("Actions")["sheetId"] == 7
    assert gws.requests("addSheet") == []
    assert [r for r in gws.requests("deleteSheet") if r["deleteSheet"]["sheetId"] == 7] == []
    assert gws.requests("updateSheetProperties") == [], "no resize when the grid already fits"
    clear = gws.requests("updateCells")[0]["updateCells"]
    assert clear["fields"] == "userEnteredValue", "formatting below the paste is kept"
    assert clear["range"] == {"sheetId": 7, "startRowIndex": 5, "endRowIndex": 50,
                              "startColumnIndex": 0, "endColumnIndex": 2}


def test_existing_tab_grows_but_never_shrinks(staged):
    gws, book = book_with(("Actions", 7, [], 3, 20))
    put(book, "Actions", [["x"] * 3] * 5, start_row=1, index=None)
    resize = gws.requests("updateSheetProperties")[0]["updateSheetProperties"]["properties"]
    assert resize["gridProperties"] == {"rowCount": 5, "columnCount": 20}


def test_paste_comes_before_the_staging_tab_is_deleted(staged):
    gws, book = book_with()
    put(book, "Actions", [["a"]], start_row=1, index=None)
    kinds = [next(iter(r)) for r in gws.requests() if "addSheet" not in r]
    assert kinds.index("copyPaste") < kinds.index("deleteSheet")


def test_every_request_body_fits_the_command_line(staged):
    """Cell contents travel through staging; what goes on the command line is
    structure only, and Gws.call refuses anything over the arg budget."""
    gws, book = book_with(("Actions", 7, [], 10, 2))
    long_text = "evidence " * 400
    put(book, "Actions", [[long_text, long_text]] * 300, start_row=2, index=None)
    for call_args, body, _ in gws.calls:
        if body is not None:
            assert len(json.dumps(body, separators=(",", ":"))) <= ds.arg_budget(), call_args


def test_several_tabs_share_one_upload(staged):
    gws, book = book_with(("JEs", 8, [], 10, 12))
    ds.put_tabs(book, [
        {"tab": "Actions", "grid": [["a"]], "startRow": 4, "index": 0},
        {"tab": "JEs", "grid": [["b"], ["c"]], "startRow": 4, "index": None},
    ])
    assert staged["uploads"] == 1
    assert set(staged["sheets"]) == {"g1", "g2"}
    destinations = [r["copyPaste"]["destination"]["sheetId"] for r in gws.requests("copyPaste")]
    assert destinations == [gws.tab("Actions")["sheetId"], 8]


# --- the command --------------------------------------------------------------

def args(**overrides):
    base = {"sheet": "x" * 30, "payload": None, "tab": "Actions", "csv": None, "start_row": 1, "index": None}
    return argparse.Namespace(**{**base, **overrides})


def test_command_refuses_bad_arguments(tmp_path):
    path = tmp_path / "g.csv"
    path.write_text("a\n", encoding="utf-8")
    good = {"csv": str(path)}
    assert exit_code(ds.put_tab_entries, args(**good, start_row=0)) == 2
    assert exit_code(ds.put_tab_entries, args(**good, index=-1)) == 2
    assert exit_code(ds.put_tab_entries, args(**good, tab="  ")) == 2
    assert exit_code(ds.put_tab_entries, args()) == 2, "--tab without --csv"
    assert exit_code(ds.put_tab_entries, args(**good, payload="p.json")) == 2, "both forms"
    assert ds.put_tab_entries(args(**good))[0]["grid"] == [["a"]]


def test_payload_form(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("x\n", encoding="utf-8")
    b.write_text("y\n", encoding="utf-8")
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps({"tabs": [{"tab": "A", "csvPath": str(a), "startRow": 3, "index": 0},
                                            {"tab": "B", "csvPath": str(b)}]}), encoding="utf-8")
    entries = ds.put_tab_entries(args(tab=None, payload=str(payload)))
    assert [(e["tab"], e["startRow"], e["index"]) for e in entries] == [("A", 3, 0), ("B", 1, None)]
    payload.write_text(json.dumps({"tabs": [{"tab": "A", "csvPath": str(a)}, {"tab": "A", "csvPath": str(b)}]}))
    assert exit_code(ds.put_tab_entries, args(tab=None, payload=str(payload))) == 2
