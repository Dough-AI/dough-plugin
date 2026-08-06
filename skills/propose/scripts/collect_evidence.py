#!/usr/bin/env python3
"""Gather the evidence behind a Dough proposal.

Bytes never travel through a model-generated tool argument: this script hashes
files locally, uploads them to presigned URLs, and prints a small manifest the
agent can cite by reference.

Stdlib only, deliberately — it runs on whatever Python a user happens to have.
"""

import argparse
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CHUNK = 1024 * 1024

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


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def declare_objects(transcript_path, files):
    """The exact `objects[]` array beginEvidence expects.

    The transcript is always key "transcript"; curated files are f0, f1, ... in
    the order the agent listed them, so a manifest entry maps back to an upload.
    """
    stat = Path(transcript_path).stat()
    objects = [
        {
            "key": "transcript",
            "role": "transcript",
            "filename": Path(transcript_path).name,
            "mime": "application/x-ndjson",
            "bytes": stat.st_size,
            "sha256": sha256_file(transcript_path),
        }
    ]
    paths = {"transcript": transcript_path}
    for index, file_path in enumerate(files):
        key = f"f{index}"
        file_stat = Path(file_path).stat()
        objects.append(
            {
                "key": key,
                "role": "file",
                "filename": Path(file_path).name,
                "mime": mimetypes.guess_type(file_path)[0] or "application/octet-stream",
                "bytes": file_stat.st_size,
                "sha256": sha256_file(file_path),
            }
        )
        paths[key] = file_path
    return objects, paths


def cmd_declare(args):
    transcript_path = find_transcript(args.cwd, args.session_id, args.home)
    missing = [f for f in args.files if not Path(f).is_file()]
    if missing:
        print(f"Not a file: {', '.join(missing)}", file=sys.stderr)
        return 2
    objects, paths = declare_objects(transcript_path, args.files)
    json.dump(
        {"sessionId": Path(transcript_path).stem, "objects": objects, "paths": paths},
        sys.stdout,
    )
    print()
    return 0


def _put(url, headers, path):
    with open(path, "rb") as handle:
        body = handle.read()
    request = urllib.request.Request(url, data=body, method="PUT")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    request.add_header("content-length", str(len(body)))
    with urllib.request.urlopen(request, timeout=120) as response:
        if response.status >= 300:
            raise urllib.error.HTTPError(url, response.status, "upload failed", {}, None)


def cmd_upload(args):
    """Upload every object, retrying with backoff.

    On unrecoverable failure this reports and exits non-zero. It deliberately
    does NOT decide whether to propose anyway — that is a human's call, and
    partial evidence should never happen silently.
    """
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    paths = plan.get("paths", {})
    uploaded, failed = [], []
    attempts = max(1, args.retries)

    for upload in plan.get("uploads", []):
        key = upload["key"]
        path = paths.get(key)
        if not path:
            failed.append({"key": key, "error": "no local path for this key"})
            continue
        last_error = None
        for attempt in range(attempts):
            try:
                _put(upload["url"], upload.get("headers"), path)
                last_error = None
                uploaded.append({"key": key, "path": path})
                break
            except Exception as exc:  # network, HTTP, filesystem — all retryable
                last_error = str(exc)
                if attempt + 1 < attempts and args.backoff > 0:
                    time.sleep(args.backoff * (2**attempt))
        if last_error is not None:
            failed.append({"key": key, "path": path, "error": last_error})

    json.dump({"uploaded": uploaded, "failed": failed}, sys.stdout)
    print()
    return 1 if failed else 0


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

    declare = sub.add_parser("declare", parents=[common])
    declare.add_argument("--session-id")
    declare.add_argument("--files", nargs="*", default=[])
    declare.set_defaults(func=cmd_declare)

    upload = sub.add_parser("upload", parents=[common])
    upload.add_argument("--plan", required=True)
    upload.add_argument("--retries", type=int, default=3)
    upload.add_argument("--backoff", type=float, default=1.0)
    upload.set_defaults(func=cmd_upload)

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
