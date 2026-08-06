#!/usr/bin/env python3
"""Gather the evidence behind a Dough proposal.

Bytes never travel through a model-generated tool argument: this script hashes
files locally, uploads them to presigned URLs, and prints a small manifest the
agent can cite by reference.

Stdlib only, deliberately — it runs on whatever Python a user happens to have.
"""

import argparse
import json
import os
import sys
from pathlib import Path


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
