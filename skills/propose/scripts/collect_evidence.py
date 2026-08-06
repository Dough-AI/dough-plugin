#!/usr/bin/env python3
"""Gather the evidence behind a Dough proposal.

Bytes never travel through a model-generated tool argument: this script hashes
files locally, uploads them to presigned URLs, and prints a small manifest the
agent can cite by reference.

Stdlib only, deliberately — it runs on whatever Python a user happens to have.
"""

import argparse
import json
import mimetypes
import os
import re
import sys
from pathlib import Path

FILE_TOOLS = {"Read", "Write", "Edit", "NotebookEdit"}

# Absolute paths only. A relative path in prose is ambiguous about which cwd it
# was relative to, and guessing wrong attaches the wrong file to an audit record.
PATH_RE = re.compile(r"(?:^|[\s\"'`(])(/[^\s\"'`)]+\.[A-Za-z0-9]{1,8})")

# Never evidence, and noisy enough to bury the real files if left in.
EXCLUDED_PARTS = {".git", "node_modules", "__pycache__", ".next", ".venv"}


class TranscriptNotFound(Exception):
    pass


def project_slug(cwd):
    """Claude Code names a project dir by replacing every non-alphanumeric
    character in the absolute cwd with a hyphen."""
    return "".join(c if c.isalnum() else "-" for c in cwd)


def find_transcript(cwd, session_id, home):
    """Locate the session JSONL. Without an explicit id, take the most recently
    modified one — a session writes to its transcript continuously, so the
    newest file is the live one."""
    directory = Path(home) / ".claude" / "projects" / project_slug(cwd)
    if session_id:
        candidate = directory / f"{session_id}.jsonl"
        if not candidate.exists():
            raise TranscriptNotFound(f"No session transcript at {candidate}")
        return str(candidate)

    if not directory.is_dir():
        raise TranscriptNotFound(f"No session transcript directory at {directory}")
    transcripts = sorted(
        directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not transcripts:
        raise TranscriptNotFound(f"No session transcript found in {directory}")
    return str(transcripts[0])


def cmd_locate(args):
    path = find_transcript(args.cwd, args.session_id, args.home)
    json.dump({"transcript": path, "sessionId": Path(path).stem}, sys.stdout)
    print()
    return 0


def _excluded(path):
    return any(part in EXCLUDED_PARTS for part in Path(path).parts)


def _iter_records(path):
    """Yield (turn_index, record). A malformed line is skipped rather than fatal:
    a transcript is an append-only log that may be mid-write."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        for index, line in enumerate(handle):
            line = line.strip()
            if not line:
                continue
            try:
                yield index, json.loads(line)
            except ValueError:
                continue


def _content_blocks(record):
    message = record.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    return []


def _paths_in_record(record):
    """Every absolute path this record refers to, with how it was referred to."""
    found = []
    for block in _content_blocks(record):
        kind = block.get("type")
        if kind == "tool_use" and block.get("name") in FILE_TOOLS:
            target = (block.get("input") or {}).get("file_path")
            if isinstance(target, str):
                found.append((target, "tool_call"))
        elif kind == "text":
            for match in PATH_RE.findall(block.get("text") or ""):
                found.append((match, "user_prose"))
    return found


def scan_transcript(path):
    """Candidate evidence files, in the order the session first touched them.

    Structural rather than recalled: an evidence set with silent gaps is exactly
    what an audit trail cannot have, so this reads the log instead of asking the
    model what it remembers.
    """
    seen = {}
    for turn, record in _iter_records(path):
        for candidate, source in _paths_in_record(record):
            if candidate in seen or _excluded(candidate):
                continue
            file_path = Path(candidate)
            if not file_path.is_file():
                continue
            stat = file_path.stat()
            seen[candidate] = {
                "path": candidate,
                "bytes": stat.st_size,
                "mime": mimetypes.guess_type(candidate)[0] or "application/octet-stream",
                "mtime": int(stat.st_mtime),
                "first_turn": turn,
                "source": source,
            }
    return list(seen.values())


def cmd_scan(args):
    path = find_transcript(args.cwd, args.session_id, args.home)
    stat = Path(path).stat()
    json.dump(
        {
            "sessionId": Path(path).stem,
            "transcript": {
                "path": path,
                "bytes": stat.st_size,
                "mime": "application/x-ndjson",
            },
            "candidates": scan_transcript(path),
        },
        sys.stdout,
    )
    print()
    return 0


def build_parser():
    # --home and --cwd hang off a shared parent rather than the top-level
    # parser: argparse will not accept a top-level option that appears AFTER
    # the subcommand, and every caller writes `scan --cwd ...`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", default=os.path.expanduser("~"))
    common.add_argument("--cwd", default=os.getcwd())

    parser = argparse.ArgumentParser(prog="collect_evidence.py")
    sub = parser.add_subparsers(dest="command", required=True)

    locate = sub.add_parser("locate", parents=[common])
    locate.add_argument("--session-id")
    locate.set_defaults(func=cmd_locate)

    scan = sub.add_parser("scan", parents=[common])
    scan.add_argument("--session-id")
    scan.set_defaults(func=cmd_scan)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except TranscriptNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
