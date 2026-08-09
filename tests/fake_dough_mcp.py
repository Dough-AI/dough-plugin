#!/usr/bin/env python3
"""A throwaway Dough MCP server that records what an agent actually sends.

Speaks just enough MCP over stdio to stand in for the real server: initialize,
tools/list, tools/call. Every tool call is appended to $DOUGH_FAKE_LOG as JSONL,
so a test can assert on what the agent did rather than what it said it did.

The upload sink deliberately lives OUTSIDE this process, at $DOUGH_FAKE_SINK.
Claude Code spawns a fresh MCP server per invocation, so a sink hosted here would
bind a new port each turn and every URL minted on a previous turn would start
refusing connections mid-run. The test owns the sink and outlives all of them.

Stdlib only. Nothing may be written to stdout except JSON-RPC responses.
"""

import csv
import io
import json
import os
import sys
import threading
import uuid
from pathlib import Path

LOG = Path(os.environ["DOUGH_FAKE_LOG"])
SINK = os.environ["DOUGH_FAKE_SINK"].rstrip("/")

# Where an uploaded table lands. The caller sends a bare name; the tool reports
# the full one back.
UPLOAD_DATASET = "dough_uploads"

_lock = threading.Lock()


def record(kind, **fields):
    """Append one event.

    Ordering comes from line position in the file, not from a counter: several
    processes append here across a run, so only the file itself knows the true
    order.
    """
    with _lock:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"kind": kind, **fields}) + "\n")


TOOLS = [
    {
        "name": "proposals__evidence__begin",
        "description": (
            "Declare the files and session transcript you are about to attach to a "
            "proposal. Returns one presigned PUT per object so bytes go straight to "
            "storage. Hash each object with sha256 BEFORE uploading."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sessionId": {"type": "string"},
                "objects": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "role": {"type": "string", "enum": ["transcript", "file"]},
                            "filename": {"type": "string"},
                            "mime": {"type": "string"},
                            "bytes": {"type": "integer"},
                            "sha256": {"type": "string"},
                        },
                        "required": ["key", "role", "filename", "mime", "bytes", "sha256"],
                    },
                },
            },
            "required": ["sessionId", "objects"],
        },
    },
    {
        "name": "proposals__propose",
        "description": (
            "Raise a write to a connected system for human approval. Does NOT perform "
            "the write. `transcript` carries the evidence reference."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "enum": ["quickbooks"]},
                "kind": {"type": "string", "enum": ["journal_entry"]},
                "payload": {"type": "object"},
                "rationale": {"type": "string"},
                "proposedVia": {"type": "string", "enum": ["agent", "human"]},
                "transcript": {"type": "object"},
            },
            "required": ["target", "kind", "payload"],
        },
    },
    {
        "name": "tables__upload",
        "description": (
            "Load a CSV into the data lake as a table. `mode` is required. "
            "`keyColumns` says what identifies one row and is required when mode is "
            "'create'. `sourceLabel` is required: where the data came from, in words a "
            "person would recognise. Asynchronous — poll tables.status with "
            "kind:'uploaded'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Bare table name, no dataset."},
                "csv": {"type": "string", "description": "The CSV itself, header row first."},
                "mode": {"type": "string", "enum": ["create", "append", "replace"]},
                "keyColumns": {"type": "array", "items": {"type": "string"}},
                "sourceLabel": {"type": "string"},
                "sourceUrl": {"type": "string"},
                "columnTypes": {"type": "object"},
                "confirm": {"type": "boolean"},
            },
            "required": ["name", "csv", "mode", "sourceLabel"],
        },
    },
    {
        "name": "tables__status",
        "description": (
            "Poll a table build. `kind` defaults to 'calculated'; an uploaded table "
            "needs kind:'uploaded'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": ["calculated", "uploaded"]},
            },
            "required": ["name"],
        },
    },
]


def header_of(csv_text):
    """The CSV's column names: the first row that isn't blank."""
    for row in csv.reader(io.StringIO(csv_text or "")):
        if any(cell.strip() for cell in row):
            return [cell.strip() for cell in row]
    return []


def data_row_count(csv_text):
    rows = [r for r in csv.reader(io.StringIO(csv_text or "")) if any(c.strip() for c in r)]
    return max(len(rows) - 1, 0)


def uploaded_tables():
    """Every uploaded table's current shape, replayed from the log.

    Claude Code respawns this server every turn, so nothing survives in memory —
    the log is the only state that outlives a process. Each accepted upload
    records the table's shape AFTER it, so the last such event wins.
    """
    tables = {}
    if not LOG.exists():
        return tables
    for line in LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("kind") != "upload_result" or not event.get("accepted"):
            continue
        tables[event["name"]] = {
            "columns": list(event["columns"]),
            "keyColumns": list(event["keyColumns"]),
            "rows": event["rows"],
        }
    return tables


class Rejected(Exception):
    """A refusal derived from the arguments — what the real tool would say."""


def reject(name, mode, reason):
    record("upload_result", tool="tables__upload", name=name, mode=mode,
           accepted=False, reason=reason)
    raise Rejected(f"tables.upload rejected: {reason}")


def quoted(columns):
    return ", ".join(f'"{c}"' for c in sorted(columns))


def upload(args):
    """The real add/omit/key rules, decided from the arguments alone.

    Never from a call counter: a scripted "fail the second call" would pass just
    as happily against an agent that learned nothing from the error.
    """
    name = (args.get("name") or "").strip()
    mode = (args.get("mode") or "").strip()
    csv_text = args.get("csv") or ""
    keys = [k.strip() for k in (args.get("keyColumns") or []) if k and k.strip()]
    source_label = (args.get("sourceLabel") or "").strip()
    columns = header_of(csv_text)

    if not name:
        reject(name, mode, "`name` is required.")
    if mode not in ("create", "append", "replace"):
        reject(name, mode, '`mode` is required and must be one of "create", "append", "replace".')
    if not source_label:
        reject(name, mode, "`sourceLabel` is required — say where this data came from.")
    if not columns:
        reject(name, mode, "the CSV has no header row.")

    tables = uploaded_tables()
    table = tables.get(name)

    if mode == "create":
        if table:
            reject(name, mode, f'a table named "{name}" already exists — append to it, '
                               "or replace it with confirm:true.")
        if not keys:
            reject(name, mode, '`keyColumns` is required when mode is "create" — name the '
                               "columns that identify one row.")
        unknown = [k for k in keys if k not in columns]
        if unknown:
            reject(name, mode, f"key column(s) {quoted(unknown)} are not in the CSV header.")
        new_columns, new_keys, rows = columns, keys, data_row_count(csv_text)

    elif mode == "replace":
        if not args.get("confirm"):
            reject(name, mode, 'mode "replace" overwrites the table and requires confirm:true.')
        if not table and not keys:
            reject(name, mode, "`keyColumns` is required when the table does not exist yet.")
        new_keys = keys or table["keyColumns"]
        unknown = [k for k in new_keys if k not in columns]
        if unknown:
            reject(name, mode, f"key column(s) {quoted(unknown)} are not in the CSV header.")
        new_columns, rows = columns, data_row_count(csv_text)

    else:  # append
        if not table:
            reject(name, mode, f'no uploaded table named "{name}" — use mode "create" first.')
        new_keys = list(table["keyColumns"])
        for key in keys:
            if key not in new_keys:
                if key not in table["columns"]:
                    reject(name, mode, f"widening the key onto {quoted([key])}, which this "
                                       "upload is introducing, needs a replace.")
                new_keys.append(key)
        missing = [c for c in table["columns"] if c not in columns]
        extra = [c for c in columns if c not in table["columns"]]
        missing_keys = [c for c in missing if c in new_keys]
        if missing_keys:
            reject(name, mode, f"key column(s) {quoted(missing_keys)} missing from the upload. "
                               "Those rows would have no value for a column the table "
                               "identifies rows by, which is not allowed — add them to the CSV.")
        if missing and extra:
            # The real tool's wording, both sides named and nothing else: what to do
            # about it is the skill's job to have said, not the error's.
            reject(
                name,
                mode,
                f"missing from the upload: {quoted(missing)}; "
                f"not in the table (would be added): {quoted(extra)}",
            )
        new_columns = table["columns"] + extra
        rows = table["rows"] + data_row_count(csv_text)

    added = [c for c in new_columns if not table or c not in table["columns"]]
    omitted = [c for c in new_columns if c not in columns]
    record("upload_result", tool="tables__upload", name=name, mode=mode, accepted=True,
           columns=new_columns, keyColumns=new_keys, rows=rows)
    message = f"Upload accepted for {UPLOAD_DATASET}.{name}."
    if mode == "append" and added:
        message += f" Columns added: {quoted(added)}."
    if mode == "append" and omitted:
        message += f" Columns omitted (empty for these rows): {quoted(omitted)}."
    return {
        "jobId": f"job_{uuid.uuid4().hex[:12]}",
        "name": f"{UPLOAD_DATASET}.{name}",
        "status": "running",
        "sourceLabel": source_label,
        "keyColumns": new_keys,
        "columnsAdded": added if mode == "append" else [],
        "columnsOmitted": omitted if mode == "append" else [],
        "message": message + ' Poll tables.status with kind:"uploaded".',
    }


def table_status(args):
    """Build status only — deliberately no column list.

    The real tool answers from the job row and the lake: name, state, exists,
    rowCount, and (for a calculated table) its defining SQL. A table's columns
    come from integrations.describe, not from here.
    """
    name = (args.get("name") or "").strip().split(".")[-1]
    kind = args.get("kind") or "calculated"
    table = uploaded_tables().get(name) if kind == "uploaded" else None
    if not table:
        return {
            "name": name,
            "found": False,
            "state": "gone",
            "exists": False,
            "rowCount": None,
            "definitionSql": None,
            "errorMessage": (
                f'No {kind} table named "{name}" has been created. '
                'An uploaded table needs kind:"uploaded".'
            ),
        }
    return {
        "name": name,
        "found": True,
        "status": "succeeded",
        "state": "ready",
        "inFlight": False,
        "exists": True,
        "table": f"{UPLOAD_DATASET}.{name}",
        "definitionSql": None,
        "rowCount": table["rows"],
        "message": f"{UPLOAD_DATASET}.{name} is ready ({table['rows']} rows).",
    }


def call_tool(name, args):
    if name == "proposals__evidence__begin":
        evidence_id = f"ev_{uuid.uuid4().hex[:12]}"
        uploads = [
            {
                "key": o["key"],
                "method": "PUT",
                "url": f"{SINK}/{evidence_id}/{o['key']}",
                "headers": {},
            }
            for o in args.get("objects", [])
        ]
        return {
            "evidenceId": evidence_id,
            "expiresAt": "2099-01-01T00:00:00Z",
            "uploads": uploads,
            "rejected": [],
            "message": f"{len(uploads)} object(s) ready to upload.",
        }

    if name == "proposals__propose":
        return {
            "reference": "PROP-TEST-1",
            "status": "pending_approval",
            "waitingOn": "any approver",
            "url": "https://example.invalid/action-gateway/proposals/test",
        }

    if name == "tables__upload":
        return upload(args)

    if name == "tables__status":
        return table_status(args)

    raise ValueError(f"unknown tool {name}")


def respond(message_id, result):
    sys.stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result}) + "\n"
    )
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue

        method = message.get("method")
        message_id = message.get("id")

        if method == "initialize":
            respond(
                message_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "dough", "version": "0.0.0-fake"},
                },
            )
        elif method == "notifications/initialized":
            continue  # a notification: no id, no response
        elif method == "tools/list":
            respond(message_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            record("tool_call", tool=name, args=args)
            try:
                result = call_tool(name, args)
            except Exception as exc:
                respond(
                    message_id,
                    {"content": [{"type": "text", "text": str(exc)}], "isError": True},
                )
                continue
            respond(
                message_id,
                {"content": [{"type": "text", "text": json.dumps(result)}]},
            )
        elif message_id is not None:
            respond(message_id, {})


if __name__ == "__main__":
    main()
