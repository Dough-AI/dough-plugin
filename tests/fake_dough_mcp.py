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
                "sourceWorkbook": {
                    "type": "object",
                    "description": (
                        "Optional. The file this CSV was DERIVED FROM. Upload it with "
                        "tables.source.prepare first and pass back the objectPath it "
                        "returned; do not base64 the file into this call."
                    ),
                    "properties": {
                        "objectPath": {"type": "string"},
                        "name": {"type": "string"},
                    },
                    "required": ["objectPath", "name"],
                },
                "columnTypes": {"type": "object"},
                "confirm": {"type": "boolean"},
            },
            "required": ["name", "csv", "mode", "sourceLabel"],
        },
    },
    {
        "name": "tables__source__prepare",
        "description": (
            "Get a URL to upload the file an uploaded table's CSV was derived from — "
            "typically the workbook you converted. Returns a short-lived PUT url and "
            "the objectPath it writes to; send the bytes there yourself and do NOT "
            "base64 them into a tool call. Then pass the SAME objectPath to "
            "tables.upload as sourceWorkbook. There is no second 'complete' call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Bare table name, no dataset."},
                "name": {"type": "string", "description": 'The file\'s name, e.g. "FY26 Plan.xlsx".'},
                "contentType": {"type": "string"},
            },
            "required": ["table", "name"],
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


def data_rows(csv_text):
    """The CSV's data rows as dicts keyed by the header.

    Blank lines are skipped wherever they fall, and a short row is padded, so a
    ragged export reads the same way `data_row_count` counts it.
    """
    rows = [r for r in csv.reader(io.StringIO(csv_text or "")) if any(c.strip() for c in r)]
    if not rows:
        return []
    header = [cell.strip() for cell in rows[0]]
    parsed = []
    for row in rows[1:]:
        padded = list(row) + [""] * (len(header) - len(row))
        parsed.append({name: (padded[i] or "").strip() for i, name in enumerate(header)})
    return parsed


def data_row_count(csv_text):
    rows = [r for r in csv.reader(io.StringIO(csv_text or "")) if any(c.strip() for c in r)]
    return max(len(rows) - 1, 0)


def replay():
    """Every event in the log, in the order it happened.

    Claude Code respawns this server every turn, so nothing survives in memory —
    the log is the only state that outlives a process, and every derived fact
    below is rebuilt from it on demand.
    """
    if not LOG.exists():
        return []
    events = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def uploaded_tables():
    """Every uploaded table's current shape AND the rows it holds, replayed from
    the log.

    Each accepted upload records the table's shape after it and the rows THAT
    upload carried; the rows accumulate on an append and are discarded by a
    create or a replace, which is what makes "is this key already in the table"
    answerable at all.

    Whole rows are kept rather than pre-computed key values because the key can
    widen on a later upload: the tuples to compare are not known until the
    upload that asks the question arrives.
    """
    tables = {}
    for event in replay():
        if event.get("kind") != "upload_result" or not event.get("accepted"):
            continue
        name = event["name"]
        incoming = list(event.get("rowsData") or [])
        held = tables[name]["rowValues"] + incoming if (
            event.get("mode") == "append" and name in tables
        ) else incoming
        tables[name] = {
            "columns": list(event["columns"]),
            "keyColumns": list(event["keyColumns"]),
            "rows": event["rows"],
            "rowValues": held,
        }
    return tables


# How many `tables.status` polls a load takes to land. Two, so that the first
# poll answers "still running" and only the second reports it ready: an agent
# told to poll UNTIL IT SUCCEEDS behaves differently from one that pings status
# once and moves on, and with a single-poll model the two are indistinguishable.
POLLS_TO_LAND = 2


def polls_since_upload(name):
    """How many times this table's status has been polled since its most recent
    accepted upload. Resets to zero on every new upload, because each one starts
    a load of its own."""
    seen = 0
    for event in replay():
        if event.get("name") != name:
            continue
        if event.get("kind") == "upload_result" and event.get("accepted"):
            seen = 0
        elif event.get("kind") == "status_poll":
            seen += 1
    return seen


def is_loading(name):
    """Whether this table's most recent load is still running.

    Answered from what the last status poll REPORTED, not by re-deriving it, so
    that the tool which refuses an upload and the tool which tells an agent the
    table is ready can never disagree: whatever `tables.status` last said is what
    `tables.upload` acts on. A load nobody has polled since is still running.

    A load lands after it has been POLLED, not after wall-clock time. The real
    thing lands in a few seconds, and a fake that slept would make every test
    slow and flaky for no gain in fidelity — what is modelled here is the
    ORDERING rule, that a second append to one table must not be sent while the
    first is still loading, and polling is exactly how an agent is meant to find
    out that it has finished.

    Note this is not the call counter the module docstring warns against.
    Acceptance is still decided from state the agent can observe and act on: one
    that polls to ready gets through, one that does not is refused, and the
    refusal is escaped by doing the right thing rather than by trying again.
    """
    if name not in uploaded_tables():
        return False
    for event in reversed(replay()):
        if event.get("name") != name:
            continue
        if event.get("kind") == "status_poll":
            return event.get("state") != "ready"
        if event.get("kind") == "upload_result" and event.get("accepted"):
            return True
    return True


class Rejected(Exception):
    """A refusal derived from the arguments — what the real tool would say."""


def reject(name, mode, reason):
    record("upload_result", tool="tables__upload", name=name, mode=mode,
           accepted=False, reason=reason)
    raise Rejected(f"tables.upload rejected: {reason}")


def quoted(columns):
    return ", ".join(f'"{c}"' for c in sorted(columns))


# How many offending keys a rejection names before it stops listing them. Enough
# to see the pattern, few enough that the message stays readable.
KEYS_NAMED = 5


def key_of(row, keys):
    """One row's key, as a tuple in `keyColumns` order. A column the row does not
    carry reads as empty rather than raising — an append may legitimately omit a
    non-key column, and this has to survive the key widening onto one later."""
    return tuple((row.get(k) or "").strip() for k in keys)


def named_keys(keys, tuples):
    """The offending keys, written the way a person would have to read them back:
    the column names alongside the values, since a bare tuple of three strings
    says nothing about which is which."""
    shown = [
        "(" + ", ".join(f'{k}="{v}"' for k, v in zip(keys, values)) + ")"
        for values in tuples[:KEYS_NAMED]
    ]
    if len(tuples) > KEYS_NAMED:
        shown.append(f"and {len(tuples) - KEYS_NAMED} more")
    return "; ".join(shown)


def prepared_workbook_paths():
    """Every objectPath this server has ever minted, replayed from the log.

    The real server can HEAD the object to see whether the bytes arrived. This
    one cannot see the sink at all, so it checks the half it CAN know: that the
    path came from a prepare call rather than from the agent's imagination. That
    is the failure this refusal exists to catch — a path invented, guessed from
    the pattern, or carried over from another table.
    """
    return {
        event["objectPath"]: event["table"]
        for event in replay()
        if event.get("kind") == "source_prepare"
    }


def source_prepare(args):
    """Mint a PUT url and the path it writes to. One call, no 'complete'."""
    table = (args.get("table") or "").strip()
    name = (args.get("name") or "").strip()
    if not table:
        raise Rejected("tables.source.prepare rejected: `table` is required.")
    if not name:
        raise Rejected("tables.source.prepare rejected: `name` is required.")

    extension = name.rsplit(".", 1)[-1] if "." in name else "bin"
    object_path = f"organizations/org_fake/uploaded-table-workbooks/{table}/{uuid.uuid4().hex}.{extension}"
    record("source_prepare", table=table, name=name, objectPath=object_path)
    return {
        "url": f"{SINK}/{object_path}",
        "requiredHeaders": {"content-type": args.get("contentType") or "application/octet-stream"},
        "objectPath": object_path,
        "message": (
            "PUT the bytes to `url`, then pass `objectPath` to tables.upload as "
            "sourceWorkbook. There is no second call."
        ),
    }


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

    workbook = args.get("sourceWorkbook")
    if workbook:
        path = (workbook.get("objectPath") or "").strip()
        prepared = prepared_workbook_paths()
        if path not in prepared:
            reject(name, mode, "unknown_source_file — no file was prepared at that "
                               "objectPath. Call tables.source.prepare, PUT the bytes "
                               "to the url it returns, then pass back the objectPath "
                               "it gave you.")
        if prepared[path] != name:
            reject(name, mode, f"unknown_source_file — that objectPath was prepared for "
                               f'table "{prepared[path]}", not "{name}".')
        if not (workbook.get("name") or "").strip():
            reject(name, mode, "`sourceWorkbook.name` is required — name the file.")

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

    # The key has to identify one row, so it is checked LAST — after the column
    # rules, because a key whose column is missing from the upload has already
    # been refused above and its values could not be read here anyway.
    incoming = data_rows(csv_text)
    seen = {}
    repeated = []
    for row in incoming:
        key = key_of(row, new_keys)
        if key in seen and key not in repeated:
            repeated.append(key)
        seen[key] = True
    if repeated:
        reject(name, mode, f"duplicate key(s) within this upload: "
                           f"{named_keys(new_keys, repeated)}. The key "
                           f"{quoted(new_keys)} must identify exactly one row.")

    # Only an append meets rows that are already there: a create has no table
    # behind it, and a replace throws the old rows away.
    if mode == "append":
        # The check below compares against the rows already in the table, and
        # that is only possible once the previous upload's load has finished. So
        # this refusal sits exactly where that check would have been and stands
        # in for it — which is also where the real tool puts it, AFTER the column
        # rules and the within-upload key check above, so an append that is both
        # malformed and early is told about the malformed part.
        #
        # Deliberately not applied to create or replace: neither compares
        # against rows already loaded, so neither has anything to wait for. A
        # create has no predecessor and a replace discards it.
        #
        # Unlike every other refusal here, this one says what to do — because
        # unlike every other refusal here, the answer is "the same payload, in a
        # moment". The add-and-omit rejection deliberately offers no remedy
        # because resending is futile; here resending is the remedy, and an
        # agent that treats this as a hard failure has lost data it was told to
        # load.
        if is_loading(name):
            reject(name, mode, f'an earlier upload to "{name}" is still loading, so this '
                               "CSV's keys could not be checked against the rows already "
                               "in the table. Nothing was recorded and nothing was "
                               'loaded — poll tables.status with kind:"uploaded" until it '
                               "is ready, then send this upload again unchanged.")

        # Named and explained, with no remedy offered — same principle as the
        # add-and-omit refusal below it: what to DO about this is the skill's job
        # to have said in advance, and an error that hands over the fix would let
        # a test pass on prose that never taught it.
        stored = {key_of(row, new_keys) for row in table["rowValues"]}
        collisions = [key for key in seen if key in stored]
        if collisions:
            reject(name, mode, f"duplicate key(s) already in the table: "
                               f"{named_keys(new_keys, collisions)}. Rows are matched on "
                               f"{quoted(new_keys)}, so these would collide with rows "
                               "already loaded.")

    added = [c for c in new_columns if not table or c not in table["columns"]]
    omitted = [c for c in new_columns if c not in columns]
    record("upload_result", tool="tables__upload", name=name, mode=mode, accepted=True,
           columns=new_columns, keyColumns=new_keys, rows=rows, rowsData=incoming,
           sourceLabel=source_label, sourceUrl=(args.get("sourceUrl") or "").strip(),
           sourceWorkbook=workbook or None)
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

    # The load lands as a CONSEQUENCE of being polled, and the poll records what
    # it reported so that an append is accepted from exactly the moment an agent
    # was told the table was ready. This is the only place the threshold is
    # compared; `is_loading` reads the result rather than re-deriving it.
    #
    # Only a poll that could actually observe this load counts — asking with the
    # default kind:"calculated" never finds an uploaded table and returned above,
    # which is the documented footgun and must not advance anything.
    landed = polls_since_upload(name) + 1 >= POLLS_TO_LAND
    record("status_poll", tool="tables__status", name=name,
           state="ready" if landed else "loading")
    if not landed:
        return {
            "name": name,
            "found": True,
            "status": "running",
            "state": "loading",
            "inFlight": True,
            "exists": True,
            "table": f"{UPLOAD_DATASET}.{name}",
            "definitionSql": None,
            "rowCount": None,
            "message": f"{UPLOAD_DATASET}.{name} is still loading.",
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

    if name == "tables__source__prepare":
        return source_prepare(args)

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
