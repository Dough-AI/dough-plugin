#!/usr/bin/env python3
"""Deterministic reader/writer for Dough-managed Google Sheets (dough-manifest v1).

Talks to Google through the `gws` CLI the gws-connect skill installs. Standard
library only — no install, no virtualenv.

  macOS:    python3 dough_sheets.py <command> ...
  Windows:  py -3 dough_sheets.py <command> ...     (or: python dough_sheets.py ...)

  dough_sheets.py list    <sheet>
  dough_sheets.py create  <sheet> --payload payload.json
  dough_sheets.py create  --new "Title" [--folder <driveFolderId>] --payload payload.json
  dough_sheets.py refresh <sheet> --payload payload.json [--sheets A,B | --all]

<sheet> is a Google Sheets URL or a bare spreadsheet id. `create --new` makes
the spreadsheet (in the given Drive folder, which must be one this app can see)
and prints its URL on the first line of output.

The payload JSON is assembled by the caller after running the saved query
(queries.get + integrations.query) and writing the result to a local CSV:
  {"entries": [{"sheet", "queryId", "queryName", "sqlSnapshot", "refreshedAt",
                "rowCount", "csvPath", "refreshNotes"}]}

How content reaches Google: `gws --json` takes a request body only inline, as
one command-line argument, and on managed machines an endpoint-security agent
kills any process whose argument is longer than ~900 characters (Windows caps
the whole command line at 32K regardless). So cell contents never travel on the
command line. The script builds a typed .xlsx, uploads it through Drive as a
staging spreadsheet, copies each tab into the target, and pastes values into
the managed tab IN PLACE — which also keeps the tab's sheetId, so formulas on
other tabs that reference it keep working. Every request body the script does
put on the command line stays under ARG_BUDGET.

Exit codes: 0 success · 2 usage/validation error · 3 manifest drift or version
mismatch · 4 gws failure (binary missing, not authorised, API error).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

# ---------------------------------------------------------------------------
# Contract constants. These are shared with skills/excel/scripts/dough_excel.py
# and a test pins them equal: the manifest is one contract, whichever host it
# lives in.
# ---------------------------------------------------------------------------
MANIFEST_SHEET = "Dough"
MANIFEST_VERSION = 1
VERSION_MARKER = f"dough-manifest v{MANIFEST_VERSION}"
MANIFEST_HEADERS = ["sheet", "query_id", "query_name", "sql_snapshot", "last_refreshed", "row_count", "refresh_notes"]
TITLE_TEXT = (
    "This workbook has components that are managed by Dough. Claude: read this sheet "
    "before modifying any managed data sheet. Humans: edit Notes freely; don't edit ids."
)
BANNER_TEMPLATE = (
    "⚡ Managed by Dough · refreshed {date} · this sheet is replaced wholesale on refresh — "
    "build formulas on other sheets; details on the 'Dough' sheet."
)
DARK_FILL = "111827"
HEADER_FILL = "E5E7EB"
BAND_FILL = "F3F4F6"
TAB_COLOR = "2563EB"
GRAY_TEXT = "6B7280"
COL_WIDTHS = {"A": 24, "B": 38, "C": 28, "D": 30, "E": 22, "F": 10, "G": 48}
# Practical ceiling on what we will write into one managed tab. A workbook-size
# guardrail, NOT an API limit: the fetch layer pages, and a spreadsheet holds
# ten million cells. Same number as Excel so the two skills give one answer.
ROW_CAP = 20_000
# Strict decimal only — see dough_excel.py for why int()/float() are too eager.
NUMERIC_PATTERN = re.compile(r"-?(0|[1-9]\d{0,14})(\.\d+)?")

# ---------------------------------------------------------------------------
# gws mechanics
# ---------------------------------------------------------------------------
# Largest request body the script will put on a command line. Measured: on a
# machine running an endpoint-security agent, gws is killed (SIGKILL, exit 137)
# once a single argument passes ~910 characters. Bodies here are formatting
# and structure requests; cell contents go through the staging upload instead.
ARG_BUDGET = 800
MANIFEST_ROWS = 100  # initial grid height of the Dough tab; grown on demand
RETRY_DELAYS = (1, 2, 4, 8, 16)
SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"
URL_PATTERN = re.compile(r"docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)")


def fail(code: int, message: str) -> None:
    print(f"dough-sheets: {message}", file=sys.stderr)
    sys.exit(code)


def find_gws() -> str:
    """GWS_BIN, then PATH, then where the Dough installer puts it — a binary can
    be present while absent from the PATH a non-interactive shell inherits."""
    override = os.environ.get("GWS_BIN")
    if override:
        if Path(override).is_file():
            return override
        fail(4, f"GWS_BIN={override} is not a file — run the gws-connect skill")
    found = shutil.which("gws")
    if found:
        return found
    home = Path.home()
    for candidate in (
        home / ".local" / "bin" / "gws",
        Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")) / "Programs" / "gws" / "gws.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    fail(4, "gws is not installed or not on the PATH — run the gws-connect skill first")
    raise AssertionError  # unreachable; keeps type checkers calm


class Gws:
    def __init__(self) -> None:
        self.binary = find_gws()

    def call(self, *args: str, body: dict | None = None, params: dict | None = None,
             upload: str | None = None, cwd: str | None = None) -> dict:
        """One gws invocation. `upload` is a path RELATIVE to `cwd`: gws refuses
        to upload a file from outside its working directory."""
        argv = [self.binary, *args]
        if params is not None:
            argv += ["--params", json.dumps(params, separators=(",", ":"))]
        if body is not None:
            encoded = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
            if len(encoded) > ARG_BUDGET:
                fail(2, f"internal: request body for gws {' '.join(args)} is {len(encoded)} chars, over ARG_BUDGET")
            argv += ["--json", encoded]
        if upload is not None:
            argv += ["--upload", upload]
        for delay in (*RETRY_DELAYS, None):
            try:
                proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", check=False, cwd=cwd)
            except OSError as error:
                fail(4, f"cannot run gws: {error}")
            if proc.returncode == 0:
                # Every call prints `Using keyring backend: …` on stderr, and may
                # print other noise before the JSON — take stdout from the first brace.
                out = proc.stdout
                start = out.find("{")
                if start < 0:
                    return {}
                try:
                    return json.loads(out[start:])
                except json.JSONDecodeError as error:
                    fail(4, f"gws {' '.join(args)}: unparseable response: {error}")
            if proc.returncode in (-9, 137):
                fail(4, f"gws {' '.join(args)} was killed (exit 137) — on managed machines an endpoint-security "
                        "agent kills long command lines; this is a bug in the script's sizing, please report it")
            stderr = proc.stderr.strip()
            rate_limited = "429" in stderr or "RESOURCE_EXHAUSTED" in stderr or "Quota exceeded" in stderr
            if rate_limited and delay is not None:
                print(f"note: rate limited by the Sheets API; retrying in {delay}s", file=sys.stderr)
                time.sleep(delay)
                continue
            tail = "\n".join(line for line in stderr.splitlines() if "keyring" not in line)[-800:]
            if "401" in stderr or "invalid_grant" in stderr or "unauthorized" in stderr.lower():
                tail += "\n(not authorised — run the gws-connect skill)"
            fail(4, f"gws {' '.join(args)} exited {proc.returncode}:\n{tail}")
        raise AssertionError  # unreachable


def spreadsheet_id(ref: str) -> str:
    match = URL_PATTERN.search(ref)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", ref):
        return ref
    fail(2, f"not a Google Sheets URL or spreadsheet id: {ref!r}")
    raise AssertionError


def url_of(sid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit"


def quote_tab(title: str) -> str:
    return "'" + title.replace("'", "''") + "'"


def column_letter(index: int) -> str:
    """1-based column index → A1 letters."""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def rgb(hex_color: str) -> dict:
    """Full-precision channels: Google floors to 8-bit, so a rounded 0.3882
    for 0x63 comes back as 0x62."""
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return {"red": r, "green": g, "blue": b}


def pixels(char_width: int) -> int:
    return char_width * 7 + 5


# ---------------------------------------------------------------------------
# Spreadsheet: structure and small writes
# ---------------------------------------------------------------------------
class Spreadsheet:
    def __init__(self, gws: Gws, sid: str) -> None:
        self.gws, self.sid = gws, sid
        self.title = ""
        self.tabs: dict[str, dict] = {}
        self.reload()

    def reload(self) -> None:
        body = self.gws.call(
            "sheets", "spreadsheets", "get",
            params={"spreadsheetId": self.sid, "fields": "properties.title,sheets.properties"},
        )
        self.title = body.get("properties", {}).get("title", "")
        self.tabs = {s["properties"]["title"]: s["properties"] for s in body.get("sheets", [])}

    def batch(self, requests: list[dict]) -> list[dict]:
        """batchUpdate, split into as many calls as ARG_BUDGET requires, in order.

        A Sheets batch is atomic; a split one is not. Requests are ordered so
        that a failure leaves the spreadsheet in a state the next run repairs
        (a tab cleared but unformatted, never a manifest row without its tab).
        """
        replies: list[dict] = []
        group: list[dict] = []
        for request in requests:
            candidate = group + [request]
            if group and len(json.dumps({"requests": candidate}, separators=(",", ":"), ensure_ascii=False)) > ARG_BUDGET:
                replies += self._batch(group)
                group = []
            group.append(request)
        if group:
            replies += self._batch(group)
        return replies

    def _batch(self, requests: list[dict]) -> list[dict]:
        reply = self.gws.call(
            "sheets", "spreadsheets", "batchUpdate",
            params={"spreadsheetId": self.sid}, body={"requests": requests},
        )
        return reply.get("replies", [])

    def read(self, a1_range: str) -> list[list]:
        body = self.gws.call(
            "sheets", "spreadsheets", "values", "get",
            params={"spreadsheetId": self.sid, "range": a1_range, "valueRenderOption": "UNFORMATTED_VALUE"},
        )
        return body.get("values", [])

    def write_small(self, tab: str, first_row: int, rows: list[list]) -> None:
        """RAW values for the few fixed cells that are known to be short (the
        manifest's title rows). Everything else goes through Staging."""
        ncols = max((len(r) for r in rows), default=1)
        a1 = f"{quote_tab(tab)}!A{first_row}:{column_letter(ncols)}{first_row + len(rows) - 1}"
        self.gws.call(
            "sheets", "spreadsheets", "values", "update",
            params={"spreadsheetId": self.sid, "range": a1, "valueInputOption": "RAW"},
            body={"values": rows},
        )


# ---------------------------------------------------------------------------
# Staging: cell contents travel as a typed .xlsx, never on the command line
# ---------------------------------------------------------------------------
def sheet_xml(rows: list[list]) -> str:
    """One worksheet. Strings are inline (no shared-strings part), numbers are
    numbers, so Google's importer keeps '0100' as text and 100 as a number."""
    out = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>',
    ]
    for r, row in enumerate(rows, 1):
        cells = []
        for c, value in enumerate(row, 1):
            if value is None or value == "":
                continue
            ref = f"{column_letter(c)}{r}"
            if isinstance(value, bool):
                cells.append(f'<c r="{ref}" t="b"><v>{int(value)}</v></c>')
            elif isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}"><v>{value!r}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>')
        out.append(f'<row r="{r}">{"".join(cells)}</row>')
    out.append("</sheetData></worksheet>")
    return "".join(out)


def build_xlsx(path: str, sheets: dict[str, list[list]]) -> None:
    names = list(sheets)
    content_types = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
    ]
    workbook = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>',
    ]
    workbook_rels = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ]
    for i, name in enumerate(names, 1):
        content_types.append(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
        workbook.append(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>')
        workbook_rels.append(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>')
    content_types.append("</Types>")
    workbook.append("</sheets></workbook>")
    workbook_rels.append("</Relationships>")
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(content_types))
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", "".join(workbook))
        z.writestr("xl/_rels/workbook.xml.rels", "".join(workbook_rels))
        for i, name in enumerate(names, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", sheet_xml(sheets[name]))


class Staging:
    """A temporary spreadsheet holding every tab this run will write.

    Built as an .xlsx on disk, uploaded once through Drive (which converts it
    to a Google Sheet), then each tab is copied into the target spreadsheet
    with `sheets.copyTo`. Deleted when the run ends, whatever happened.
    """

    def __init__(self, gws: Gws, sheets: dict[str, list[list]]) -> None:
        self.gws = gws
        self.sid: str | None = None
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        with tempfile.TemporaryDirectory() as tmp:
            build_xlsx(os.path.join(tmp, "staging.xlsx"), sheets)
            self.sid = gws.call(
                "drive", "files", "create",
                body={"name": f"dough-sheets staging {stamp}", "mimeType": SPREADSHEET_MIME},
                params={"fields": "id"}, upload="staging.xlsx", cwd=tmp,
            )["id"]
        body = gws.call("sheets", "spreadsheets", "get", params={"spreadsheetId": self.sid, "fields": "sheets.properties(sheetId,title)"})
        self.tabs = {s["properties"]["title"]: s["properties"]["sheetId"] for s in body.get("sheets", [])}
        missing = set(sheets) - set(self.tabs)
        if missing:
            self.close()
            fail(4, f"staging upload lost tabs {sorted(missing)}")

    def copy_into(self, book: Spreadsheet, name: str) -> dict:
        """Copy a staged tab into `book`; returns the copy's properties (its
        sheetId and whatever title Google gave it)."""
        return self.gws.call(
            "sheets", "spreadsheets", "sheets", "copyTo",
            params={"spreadsheetId": self.sid, "sheetId": self.tabs[name]},
            body={"destinationSpreadsheetId": book.sid},
        )

    def close(self) -> None:
        if not self.sid:
            return
        proc = subprocess.run(
            [self.gws.binary, "drive", "files", "delete", "--params", json.dumps({"fileId": self.sid})],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            print(f"note: could not delete the staging spreadsheet {self.sid}; delete it from Drive by hand", file=sys.stderr)
        self.sid = None


# ---------------------------------------------------------------------------
# Formatting requests
# ---------------------------------------------------------------------------
def band_requests(sheet_id: int, ncols: int) -> list[dict]:
    """Row 1 as a dark band: fill and white bold text applied cell by cell
    across the width, text in A1 overflowing across it. Never merged — merged
    ranges break sorting, filtering, and programmatic reads."""
    return [{
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": max(1, ncols)},
            "cell": {"userEnteredFormat": {
                "backgroundColor": rgb(DARK_FILL),
                "textFormat": {"bold": True, "foregroundColor": rgb("FFFFFF")},
                "verticalAlignment": "TOP",
                "wrapStrategy": "OVERFLOW_CELL",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
        }
    }]


def manifest_tab_requests(sheet_id: int) -> list[dict]:
    """Formatting for a freshly added Dough tab (values are written separately)."""
    requests = band_requests(sheet_id, len(MANIFEST_HEADERS))
    requests += [
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 40}, "fields": "pixelSize"}},
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"fontSize": 9, "foregroundColor": rgb(GRAY_TEXT)}}},
            "fields": "userEnteredFormat.textFormat"}},
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": 0, "endColumnIndex": len(MANIFEST_HEADERS)},
            "cell": {"userEnteredFormat": {"backgroundColor": rgb(HEADER_FILL), "textFormat": {"bold": True}}},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"}},
        {"setBasicFilter": {"filter": {"range": {
            "sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": 0, "endColumnIndex": len(MANIFEST_HEADERS)}}}},
    ]
    for i, letter in enumerate(COL_WIDTHS):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": pixels(COL_WIDTHS[letter])}, "fields": "pixelSize"}})
    return requests


def manifest_row_requests(sheet_id: int, row: int) -> list[dict]:
    """Wrap + top-align the row; monospace sql_snapshot; band even rows."""
    r0 = row - 1
    fmt = {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}
    if row % 2 == 0:
        fmt["backgroundColor"] = rgb(BAND_FILL)
    return [
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r0 + 1, "startColumnIndex": 0, "endColumnIndex": len(MANIFEST_HEADERS)},
            "cell": {"userEnteredFormat": fmt},
            "fields": "userEnteredFormat(" + ",".join(fmt) + ")"}},
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r0 + 1, "startColumnIndex": 3, "endColumnIndex": 4},
            "cell": {"userEnteredFormat": {"textFormat": {"fontFamily": "Courier New", "fontSize": 9}}},
            "fields": "userEnteredFormat.textFormat"}},
    ]


def data_tab_requests(sheet_id: int, ncols: int) -> list[dict]:
    return band_requests(sheet_id, ncols) + [
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": max(1, ncols)},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat"}},
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "tabColor": rgb(TAB_COLOR)},
            "fields": "tabColor"}},
    ]


def resize_request(sheet_id: int, nrows: int, ncols: int, title: str | None = None) -> dict:
    properties: dict = {"sheetId": sheet_id, "gridProperties": {"rowCount": max(1, nrows), "columnCount": max(1, ncols)}}
    fields = "gridProperties.rowCount,gridProperties.columnCount"
    if title is not None:
        properties["title"] = title
        fields = "title," + fields
    return {"updateSheetProperties": {"properties": properties, "fields": fields}}


def copy_paste_request(source_id: int, nrows: int, ncols: int, target_id: int, row0: int, source_row0: int = 0) -> dict:
    """Destination bounded exactly: an open-ended destination makes Sheets
    repeat the source to fill the rest of the grid."""
    return {"copyPaste": {
        "source": {"sheetId": source_id, "startRowIndex": source_row0, "endRowIndex": source_row0 + nrows, "startColumnIndex": 0, "endColumnIndex": ncols},
        "destination": {"sheetId": target_id, "startRowIndex": row0, "endRowIndex": row0 + nrows, "startColumnIndex": 0, "endColumnIndex": ncols},
        "pasteType": "PASTE_VALUES",
    }}


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
def read_manifest(book: Spreadsheet) -> list[dict]:
    """Manifest rows (with their spreadsheet row numbers), or fail(3)."""
    if MANIFEST_SHEET not in book.tabs:
        fail(3, f"no '{MANIFEST_SHEET}' sheet — not a Dough-managed spreadsheet")
    grid = book.read(f"{MANIFEST_SHEET}!A1:{column_letter(len(MANIFEST_HEADERS))}")

    def cell(r: int, c: int) -> str:
        return str(grid[r][c]) if r < len(grid) and c < len(grid[r]) and grid[r][c] is not None else ""

    marker = cell(1, 0).strip()
    if not marker.startswith("dough-manifest"):
        fail(3, f"'{MANIFEST_SHEET}'!A2 is not a dough-manifest marker: {marker!r}")
    match = re.fullmatch(r"dough-manifest v(\d+)", marker)
    if not match:
        fail(3, f"unrecognized manifest marker {marker!r}; expected {VERSION_MARKER!r}")
    version = int(match.group(1))
    if version > MANIFEST_VERSION:
        fail(3, f"manifest version v{version} is newer than this script ({VERSION_MARKER}); update the Dough plugin")
    if version < MANIFEST_VERSION:
        fail(3, f"manifest version v{version} predates this script ({VERSION_MARKER}); re-create the spreadsheet or migrate it to {VERSION_MARKER}")
    headers = [cell(2, i) for i in range(len(MANIFEST_HEADERS))]
    if headers != MANIFEST_HEADERS:
        fail(3, f"manifest headers drifted: {headers}")
    rows = []
    for r in range(3, len(grid)):
        raw = [grid[r][c] if c < len(grid[r]) else "" for c in range(len(MANIFEST_HEADERS))]
        if not any(v not in (None, "") for v in raw):
            continue
        values = ["" if v is None else v for v in raw]
        rows.append({"row": r + 1, **dict(zip(MANIFEST_HEADERS, values))})
    return rows


def ensure_manifest_tab(book: Spreadsheet, drop_default: bool = False) -> None:
    if MANIFEST_SHEET in book.tabs:
        return
    reply = book.batch([{"addSheet": {"properties": {
        "title": MANIFEST_SHEET, "index": 0,
        "gridProperties": {"rowCount": MANIFEST_ROWS, "columnCount": len(MANIFEST_HEADERS), "frozenRowCount": 3},
    }}}])
    sheet_id = reply[0]["addSheet"]["properties"]["sheetId"]
    book.write_small(MANIFEST_SHEET, 1, [[TITLE_TEXT], [VERSION_MARKER], list(MANIFEST_HEADERS)])
    requests = manifest_tab_requests(sheet_id)
    if drop_default:
        # A spreadsheet is born with `Sheet1`; on a spreadsheet WE created it
        # is noise, on one the user handed us it is theirs.
        for title, props in book.tabs.items():
            if title != MANIFEST_SHEET:
                requests.append({"deleteSheet": {"sheetId": props["sheetId"]}})
    book.batch(requests)
    book.reload()


def manifest_values(entry: dict) -> list:
    row_count = entry["rowCount"]
    if not isinstance(row_count, (int, float)):
        row_count = int(str(row_count)) if str(row_count).isdigit() else str(row_count)
    return [entry["sheet"], entry["queryId"], entry["queryName"], entry["sqlSnapshot"], entry["refreshedAt"], row_count, entry.get("refreshNotes", "")]


def write_manifest_rows(book: Spreadsheet, staging: Staging, manifest_rows: list[dict], entries: list[dict]) -> None:
    """Upsert one manifest row per entry, keyed by sheet name, from the staged
    `manifest` tab (row i of it is entry i)."""
    sheet = book.tabs[MANIFEST_SHEET]
    targets = []
    for entry in entries:
        target = next((row["row"] for row in manifest_rows if str(row["sheet"]) == entry["sheet"]), None)
        if target is None:
            target = max([row["row"] for row in manifest_rows] + [r for r in targets] + [3]) + 1
            manifest_rows.append({"row": target, **dict(zip(MANIFEST_HEADERS, manifest_values(entry)))})
        targets.append(target)
    copy = staging.copy_into(book, "manifest")
    requests = []
    if max(targets) > sheet["gridProperties"]["rowCount"]:
        requests.append(resize_request(sheet["sheetId"], max(targets) + MANIFEST_ROWS, len(MANIFEST_HEADERS)))
    for i, target in enumerate(targets):
        requests.append(copy_paste_request(copy["sheetId"], 1, len(MANIFEST_HEADERS), sheet["sheetId"], target - 1, source_row0=i))
        requests += manifest_row_requests(sheet["sheetId"], target)
    requests.append({"deleteSheet": {"sheetId": copy["sheetId"]}})
    book.batch(requests)


# ---------------------------------------------------------------------------
# Data tabs
# ---------------------------------------------------------------------------
def read_csv_file(path: str, sheet: str):
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as error:
        fail(2, f"{sheet}: cannot read csvPath {path}: {error}")
    parsed = [row for row in csv.reader(text.splitlines()) if row]
    if not parsed:
        fail(2, f"{sheet}: csvPath {path} is empty")
    return parsed[0], parsed[1:]


def coerce(value: str):
    """CSV gives strings; stage unambiguous decimals as numbers so formulas work."""
    if value == "":
        return ""
    if not NUMERIC_PATTERN.fullmatch(value):
        return value
    return float(value) if "." in value else int(value)


def data_grid(entry: dict) -> list[list]:
    headers, rows = read_csv_file(entry["csvPath"], entry["sheet"])
    if len(rows) > ROW_CAP:
        fail(2, f"{entry['sheet']}: {len(rows)} rows exceeds the {ROW_CAP} cap — use a lower grain")
    banner = BANNER_TEMPLATE.format(date=entry["refreshedAt"][:10])
    return [[banner], list(headers)] + [[coerce(v) for v in row] for row in rows]


def write_data_sheet(book: Spreadsheet, staging: Staging, key: str, entry: dict, grid: list[list]) -> str:
    nrows = len(grid)
    ncols = max(len(r) for r in grid)
    title = entry["sheet"]
    copy = staging.copy_into(book, key)
    if title in book.tabs:
        # Wholesale replace, per contract — but IN PLACE. Deleting the tab
        # would turn every formula that references it into an error,
        # permanently; clearing keeps the sheetId and every reference to it.
        target = book.tabs[title]["sheetId"]
        requests = [
            {"updateCells": {"range": {"sheetId": target}, "fields": "*"}},
            resize_request(target, nrows, ncols),
            copy_paste_request(copy["sheetId"], nrows, ncols, target, 0),
            {"deleteSheet": {"sheetId": copy["sheetId"]}},
        ]
    else:
        target = copy["sheetId"]
        requests = [resize_request(target, nrows, ncols, title=title)]
    book.batch(requests + data_tab_requests(target, ncols))
    book.reload()
    return f"{title}: {nrows - 2} rows · refreshed {entry['refreshedAt']}"


def write_all(book: Spreadsheet, entries: list[dict], manifest_rows: list[dict]) -> list[str]:
    """Stage every tab once, then land each in the target. Validation (CSV
    readable, under the cap) happens before anything touches Google."""
    grids = {f"t{i}": data_grid(entry) for i, entry in enumerate(entries, 1)}
    staged = dict(grids)
    staged["manifest"] = [manifest_values(entry) for entry in entries]
    staging = Staging(book.gws, staged)
    try:
        summaries = [write_data_sheet(book, staging, key, entry, grids[key]) for key, entry in zip(grids, entries)]
        write_manifest_rows(book, staging, manifest_rows, entries)
    finally:
        staging.close()
    return summaries


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def load_payload(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as error:
        fail(2, f"cannot read payload {path}: {error}")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        fail(2, "payload must contain a non-empty 'entries' list")
    required = {"sheet", "queryId", "queryName", "sqlSnapshot", "refreshedAt", "rowCount"}
    for entry in entries:
        missing = required - set(entry)
        if missing:
            fail(2, f"payload entry for {entry.get('sheet', '?')!r} missing {sorted(missing)}")
    if len({e["sheet"] for e in entries}) != len(entries):
        fail(2, "payload names the same sheet twice")
    return entries


def cmd_list(args) -> None:
    book = Spreadsheet(Gws(), spreadsheet_id(args.sheet))
    rows = read_manifest(book)
    entries = [{k: v for k, v in row.items() if k != "row"} for row in rows]
    print(json.dumps({"version": VERSION_MARKER, "title": book.title, "url": url_of(book.sid), "entries": entries}, indent=2, ensure_ascii=False))


def create_spreadsheet(gws: Gws, title: str, folder: str | None) -> str:
    body = {"name": title, "mimeType": SPREADSHEET_MIME}
    if folder:
        body["parents"] = [folder]
    return gws.call("drive", "files", "create", body=body, params={"fields": "id"})["id"]


def cmd_create(args) -> None:
    entries = load_payload(args.payload)
    if bool(args.sheet) == bool(args.new):
        fail(2, "create takes either <sheet> or --new \"Title\", not both and not neither")
    for entry in entries:
        if "csvPath" not in entry:
            fail(2, f"create requires csvPath for {entry['sheet']!r}")
    gws = Gws()
    fresh = bool(args.new)
    sid = create_spreadsheet(gws, args.new, args.folder) if fresh else spreadsheet_id(args.sheet)
    book = Spreadsheet(gws, sid)
    ensure_manifest_tab(book, drop_default=fresh)
    manifest_rows = read_manifest(book)
    summaries = write_all(book, entries, manifest_rows)
    if fresh:
        print(url_of(sid))
    print("\n".join(summaries))


def cmd_refresh(args) -> None:
    entries = load_payload(args.payload)
    book = Spreadsheet(Gws(), spreadsheet_id(args.sheet))
    manifest_rows = read_manifest(book)
    known = {str(row["sheet"]) for row in manifest_rows}
    wanted = {e["sheet"] for e in entries} if args.all or not args.sheets else set(args.sheets.split(","))
    unknown = wanted - known
    if unknown:
        fail(3, f"payload/--sheets name sheets not in the manifest: {sorted(unknown)} — reconcile first")
    missing = [str(row["sheet"]) for row in manifest_rows if row["sheet"] in wanted and row["sheet"] not in book.tabs]
    if missing:
        print(f"note: manifest rows whose sheets were deleted (recreating): {missing}", file=sys.stderr)
    selected = [e for e in entries if e["sheet"] in wanted]
    for entry in selected:
        if "csvPath" not in entry:
            fail(2, f"refresh requires csvPath for {entry['sheet']!r}")
    summaries = write_all(book, selected, manifest_rows) if selected else []
    print("\n".join(summaries) if summaries else "nothing to refresh")


def main() -> None:
    # The banner carries a non-ASCII character; a Windows console defaulting to
    # cp1252 would otherwise crash on printing a summary line.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="dough_sheets.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("list", cmd_list), ("create", cmd_create), ("refresh", cmd_refresh)):
        p = sub.add_parser(name)
        p.add_argument("sheet", nargs="?" if name == "create" else None, help="Google Sheets URL or spreadsheet id")
        if name != "list":
            p.add_argument("--payload", required=True)
        if name == "create":
            p.add_argument("--new", metavar="TITLE", help="create a new spreadsheet with this title")
            p.add_argument("--folder", metavar="FOLDER_ID", help="Drive folder id for --new (must be app-visible)")
        if name == "refresh":
            p.add_argument("--sheets", default="")
            p.add_argument("--all", action="store_true")
        p.set_defaults(handler=handler)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
