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

import json
import os
import sys
import threading
import uuid
from pathlib import Path

LOG = Path(os.environ["DOUGH_FAKE_LOG"])
SINK = os.environ["DOUGH_FAKE_SINK"].rstrip("/")

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
        "name": "proposals__beginEvidence",
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
]


def call_tool(name, args):
    if name == "proposals__beginEvidence":
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
        }

    if name == "proposals__propose":
        return {
            "reference": "PROP-TEST-1",
            "status": "pending_approval",
            "waitingOn": "any approver",
            "url": "https://example.invalid/action-gateway/proposals/test",
        }

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
