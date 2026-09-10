"""Helpers for tests that touch real Google Sheets through the `gws` CLI.

Nothing here is a fake. Both the script-level suite and the model-driven e2e
run against a spreadsheet Google actually hosts, because the seam under test
is the one between Dough's manifest contract and the Sheets API, and a fake
Sheets would only prove the tests agree with the fake.

Every spreadsheet these tests create goes under `Dough / Dough sheets tests`
in the connected account's Drive. The grant is `drive.file`, so the folder has
to be one this app created — a folder made by hand in the Drive UI is invisible
to it. Move or rename the folder in Drive freely; the app keeps access.

Tests skip unless `gws` is connected, judged by the gws-connect skill's own
triage script so the two cannot disagree about what "connected" means.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
TRIAGE = REPO / "skills" / "gws-connect" / "scripts" / "triage.py"

PARENT_FOLDER = "Dough"
TEST_FOLDER = "Dough sheets tests"
SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"
FOLDER_MIME = "application/vnd.google-apps.folder"


def gws_binary() -> str | None:
    return os.environ.get("GWS_BIN") or shutil.which("gws")


def connected() -> bool:
    if not gws_binary():
        return False
    proc = subprocess.run([sys.executable, str(TRIAGE)], capture_output=True, text=True, check=False)
    return proc.stdout.strip().splitlines()[-1:] == ["CONNECTED"]


requires_gws = pytest.mark.skipif(
    not connected(),
    reason="needs a connected gws (run the gws-connect skill)",
)


def gws(*args: str) -> dict:
    """Run one gws command and return its JSON body.

    Judged by exit code. stdout is parsed from the first brace because every
    invocation prints `Using keyring backend: …` before any JSON.
    """
    proc = subprocess.run([gws_binary(), *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"gws {' '.join(args[:4])} … exited {proc.returncode}: {proc.stderr.strip()[-500:]}")
    body = proc.stdout[proc.stdout.find("{"):] if "{" in proc.stdout else "{}"
    return json.loads(body)


def q(value: str) -> str:
    return "'" + value.replace("'", "\\'") + "'"


def find_folder(name: str, parent: str | None = None) -> str | None:
    query = f"name = {q(name)} and mimeType = {q(FOLDER_MIME)} and trashed = false"
    if parent:
        query += f" and {q(parent)} in parents"
    files = gws("drive", "files", "list", "--params", json.dumps({"q": query, "fields": "files(id)"})).get("files", [])
    return files[0]["id"] if files else None


def ensure_test_folder() -> str:
    """The `Dough / Dough sheets tests` folder, created on first use."""
    parent = find_folder(PARENT_FOLDER)
    if parent is None:
        parent = gws("drive", "files", "create", "--json", json.dumps({"name": PARENT_FOLDER, "mimeType": FOLDER_MIME}))["id"]
    folder = find_folder(TEST_FOLDER, parent)
    if folder is None:
        folder = gws("drive", "files", "create", "--json", json.dumps({"name": TEST_FOLDER, "mimeType": FOLDER_MIME, "parents": [parent]}))["id"]
    return folder


def create_spreadsheet(title: str, folder: str) -> str:
    return gws(
        "drive", "files", "create",
        "--json", json.dumps({"name": title, "mimeType": SPREADSHEET_MIME, "parents": [folder]}),
        "--params", json.dumps({"fields": "id"}),
    )["id"]


def find_spreadsheet(title: str, folder: str) -> str | None:
    query = f"name = {q(title)} and mimeType = {q(SPREADSHEET_MIME)} and {q(folder)} in parents and trashed = false"
    files = gws("drive", "files", "list", "--params", json.dumps({"q": query, "fields": "files(id)"})).get("files", [])
    return files[0]["id"] if files else None


def delete_file(file_id: str) -> None:
    subprocess.run([gws_binary(), "drive", "files", "delete", "--params", json.dumps({"fileId": file_id})], capture_output=True, check=False)


def keep_sheets() -> bool:
    return bool(os.environ.get("DOUGH_KEEP_SHEETS"))


def url_of(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def spreadsheet_id_from(text: str) -> str | None:
    match = re.search(r"docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)", text)
    return match.group(1) if match else None


def tabs(spreadsheet_id: str) -> list[dict]:
    """Each tab's properties plus its merged ranges (`merges`, possibly absent)."""
    body = gws(
        "sheets", "spreadsheets", "get",
        "--params", json.dumps({"spreadsheetId": spreadsheet_id, "fields": "sheets.properties,sheets.merges"}),
    )
    return [{**s["properties"], "merges": s.get("merges", [])} for s in body.get("sheets", [])]


def values(spreadsheet_id: str, a1_range: str, render: str = "UNFORMATTED_VALUE") -> list[list]:
    """Rows as the API returns them: trailing empty cells trimmed, numbers typed."""
    body = gws(
        "sheets", "spreadsheets", "values", "get",
        "--params", json.dumps({"spreadsheetId": spreadsheet_id, "range": a1_range, "valueRenderOption": render}),
    )
    return body.get("values", [])


def write_values(spreadsheet_id: str, a1_range: str, rows: list[list], input_option: str = "USER_ENTERED") -> None:
    gws(
        "sheets", "spreadsheets", "values", "update",
        "--params", json.dumps({"spreadsheetId": spreadsheet_id, "range": a1_range, "valueInputOption": input_option}),
        "--json", json.dumps({"values": rows}),
    )


def add_tab(spreadsheet_id: str, title: str) -> int:
    body = gws(
        "sheets", "spreadsheets", "batchUpdate",
        "--params", json.dumps({"spreadsheetId": spreadsheet_id}),
        "--json", json.dumps({"requests": [{"addSheet": {"properties": {"title": title}}}]}),
    )
    return body["replies"][0]["addSheet"]["properties"]["sheetId"]


def quote_tab(title: str) -> str:
    return "'" + title.replace("'", "''") + "'"


def manifest(spreadsheet_id: str) -> dict:
    """The `Dough` tab as {marker, headers, rows[list of dict]}; rows padded to 7."""
    grid = values(spreadsheet_id, "Dough!A1:G")
    marker = str(grid[1][0]) if len(grid) > 1 and grid[1] else ""
    headers = [str(c) for c in grid[2]] if len(grid) > 2 else []
    rows = []
    for row in grid[3:]:
        padded = list(row) + [""] * (7 - len(row))
        if any(str(c).strip() for c in padded):
            rows.append(dict(zip(headers, padded)))
    return {"marker": marker, "headers": headers, "rows": rows}


def is_error_cell(value) -> bool:
    return isinstance(value, str) and value.startswith("#")
