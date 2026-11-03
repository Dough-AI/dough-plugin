"""Offline: how large a request body may go on the command line, and that CSV
line breaks inside quoted cells survive into the grid.

The 800-character budget exists for managed Macs, whose endpoint agent kills
gws past ~910 characters. Linux, where hosted agents run, gets a far larger
one, so the same batch is one call instead of dozens. Callers with their own
gws requests read the number from `arg-budget`.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "skills" / "google-sheets" / "scripts" / "dough_sheets.py"


def load():
    spec = importlib.util.spec_from_file_location("dough_sheets_budget", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ds = load()


@pytest.fixture(autouse=True)
def no_override(monkeypatch):
    monkeypatch.delenv("DOUGH_GWS_ARG_BUDGET", raising=False)


@pytest.mark.parametrize("platform, expected", [
    ("darwin", ds.ARG_BUDGET),
    ("win32", ds.ARG_BUDGET),
    ("linux", ds.LINUX_ARG_BUDGET),
])
def test_budget_follows_the_platform(monkeypatch, platform, expected):
    monkeypatch.setattr(ds.sys, "platform", platform)
    assert ds.arg_budget() == expected


def test_the_linux_budget_fits_one_linux_argument():
    # MAX_ARG_STRLEN: one argument may be at most 32 pages (128 KiB).
    assert ds.LINUX_ARG_BUDGET < 131072 and ds.ARG_BUDGET_RANGE[1] < 131072


def test_override_wins_on_any_platform(monkeypatch):
    monkeypatch.setattr(ds.sys, "platform", "linux")
    monkeypatch.setenv("DOUGH_GWS_ARG_BUDGET", "800")
    assert ds.arg_budget() == 800


@pytest.mark.parametrize("value", ["abc", "-5", "199", "120001", "1.5"])
def test_a_bad_override_is_refused(monkeypatch, value):
    monkeypatch.setenv("DOUGH_GWS_ARG_BUDGET", value)
    with pytest.raises(SystemExit) as raised:
        ds.arg_budget()
    assert raised.value.code == 2


class CountingGws:
    def __init__(self):
        self.batches = []

    def call(self, *args, body=None, params=None, **_):
        if args == ("sheets", "spreadsheets", "get"):
            return {"properties": {"title": "Book"}, "sheets": []}
        self.batches.append(body["requests"])
        return {"replies": [{} for _ in body["requests"]]}


def format_requests(n):
    return [{"repeatCell": {"range": {"sheetId": 1, "startRowIndex": i, "endRowIndex": i + 1},
                            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                            "fields": "userEnteredFormat.wrapStrategy"}} for i in range(n)]


@pytest.mark.parametrize("platform, max_calls", [("darwin", None), ("linux", 1)])
def test_batch_splits_by_the_budget_in_force(monkeypatch, platform, max_calls):
    monkeypatch.setattr(ds.sys, "platform", platform)
    gws = CountingGws()
    book = ds.Spreadsheet(gws, "sid")
    requests = format_requests(60)
    book.batch(requests)
    assert [r for batch in gws.batches for r in batch] == requests  # all sent, in order
    if max_calls is None:
        assert len(gws.batches) > 5  # 800 characters hold only a few of these
    else:
        assert len(gws.batches) == max_calls


def test_call_refuses_a_body_over_the_budget(monkeypatch):
    monkeypatch.setenv("DOUGH_GWS_ARG_BUDGET", "200")
    gws = object.__new__(ds.Gws)
    gws.binary = "gws"
    monkeypatch.setattr(ds, "run_gws", lambda *a, **k: pytest.fail("gws must not run"))
    with pytest.raises(SystemExit) as raised:
        gws.call("sheets", "spreadsheets", "batchUpdate", body={"requests": format_requests(5)})
    assert raised.value.code == 2


def test_arg_budget_command_prints_the_budget(monkeypatch):
    env = {"DOUGH_GWS_ARG_BUDGET": "4321", "PATH": "/usr/bin:/bin"}
    out = subprocess.run([sys.executable, str(SCRIPT), "arg-budget"], capture_output=True, text=True, env=env, check=True)
    assert out.stdout.strip() == "4321"


CSV_WITH_BREAKS = 'Item,Flags\r\nA,"duplicate\r\nstale"\r\n\r\nB,"one\ntwo"\r\n'


def test_line_breaks_inside_quoted_cells_survive():
    assert ds.parse_csv(CSV_WITH_BREAKS) == [
        ["Item", "Flags"], ["A", "duplicate\nstale"], [], ["B", "one\ntwo"]]


def test_plain_grid_keeps_breaks_and_blank_rows(tmp_path):
    path = tmp_path / "grid.csv"
    path.write_bytes(CSV_WITH_BREAKS.encode())
    assert ds.read_plain_grid(str(path)) == [["Item", "Flags"], ["A", "duplicate\nstale"], [], ["B", "one\ntwo"]]


def test_data_tab_csv_keeps_breaks(tmp_path):
    path = tmp_path / "data.csv"
    path.write_bytes(CSV_WITH_BREAKS.encode())
    header, rows = ds.read_csv_file(str(path), "Data")
    assert header == ["Item", "Flags"]
    assert rows == [["A", "duplicate\nstale"], ["B", "one\ntwo"]]
