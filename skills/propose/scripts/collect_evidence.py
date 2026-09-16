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
import posixpath
import re
import shlex
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

CHUNK = 1024 * 1024

FILE_TOOLS = {"Read", "Write", "Edit", "NotebookEdit"}

# Absolute paths only. A relative path in prose is ambiguous about which cwd it
# was relative to, and guessing wrong attaches the wrong file to an audit record.
#
# "Absolute" is three shapes, not one: POSIX `/a/b.csv`, Windows `C:\a\b.csv`
# (either separator), and UNC `\\server\share\b.csv`. All three are matched on
# every platform on purpose -- a pattern that only knows the host it runs on is
# a pattern nobody's CI ever tests the other half of, which is exactly how the
# Windows half stayed broken. Nothing is attached on the strength of the match
# alone: scan_transcript still requires the file to exist.
PATH_RE = re.compile(
    r"""(?:^|[\s"'`(])            # a delimiter, never captured
        (                         # the path itself, one of three shapes:
          (?: /                   # POSIX absolute
            | [A-Za-z]:[\\/]      # a Windows drive, either separator
            | \\\\[^\\/\s"'`)]+[\\/]  # a UNC server and share
          )
          [^\s"'`)]+\.[A-Za-z0-9]{1,8}   # the rest, ending in an extension
        )""",
    re.VERBOSE,
)

# A path that is absolute on Windows. Anchored form for testing one token,
# delimiter-led form for asking whether a whole command contains one -- without
# it, the `p:/` inside `http://` reads as a drive letter.
WINDOWS_ABS_TOKEN_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/])")
WINDOWS_ABS_IN_TEXT_RE = re.compile(r"""(?:^|[\s"'`(=])(?:[A-Za-z]:[\\/]|\\\\[^\\/\s])""")

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
    r"""Split on both separators rather than via Path: on POSIX a backslash is an
    ordinary character, so `C:\repo\.git\HEAD` would be a single part and sail
    straight through the exclusion."""
    return any(part in EXCLUDED_PARTS for part in re.split(r"[\\/]", path))


def _is_absolute(path):
    r"""Absolute on EITHER platform, whichever platform this happens to be.

    `os.path.isabs` cannot answer this: it answers for its host, and it is wrong
    in one direction on each. On POSIX it calls `C:\Users\a.pdf` relative. On
    Windows since 3.13 it calls `/work/a.csv` relative too -- that path is
    drive-RELATIVE there, since it never says which drive. Either way the token
    is joined to a cwd it never belonged to, and the evidence is quietly missed.

    So neither branch is the host's opinion: `posixpath.isabs` for POSIX roots,
    the drive/UNC pattern for Windows ones. `\work\a.csv` is deliberately NOT
    absolute -- drive-relative is ambiguous in exactly the way this rule exists
    to reject, and ntpath's own answer for it changed in 3.13, so deferring to
    it would make the result depend on the interpreter.

    `/work/a.csv` on a Windows host IS accepted, and that is the same
    drive-relative shape: it resolves against whatever drive the collector is
    running on. Accepted anyway, because that is what Windows itself does with
    it and what `Path.is_file()` will check regardless -- and because dropping
    it would put us back where this fix started, with git-bash and WSL evidence
    silently missing. `_dedupe_key` is what keeps the two spellings one file.
    """
    return posixpath.isabs(path) or bool(WINDOWS_ABS_TOKEN_RE.match(path))


def _dedupe_key(path):
    r"""One key per FILE, not per spelling.

    Two things make one file arrive under two names. Windows paths are
    case-insensitive, so `C:\x\Invoice.pdf` typed in prose and `C:\x\invoice.pdf`
    from a tool call are the same bytes. And a rooted path with no drive --
    `/work/a.csv` in a git-bash command, which normpath rewrites to `\work\a.csv`
    on a Windows host -- names the same file as `C:\work\a.csv` whenever the
    collector is running on C:.

    So the key is what `is_file()` below will actually resolve and check, which
    is exactly `abspath`. Attaching the same bytes to an audit record twice is
    not wrong, but it is noise on the one document that should not have any.
    """
    return os.path.normcase(os.path.abspath(path))


def _tokenize(command):
    r"""Split a shell command into tokens, without eating Windows separators.

    shlex's posix mode reads `\` as an escape, which turns `C:\Users\a.pdf` into
    `C:Usersa.pdf` -- a path that then matches nothing on disk, silently. Its
    non-posix mode keeps the backslashes but also keeps the quotes, so those are
    stripped here. Posix stays the default: it is right for every command that
    does not carry a Windows path, including the escaped spaces in POSIX ones.
    """
    posix = not WINDOWS_ABS_IN_TEXT_RE.search(command)
    try:
        tokens = shlex.split(command, posix=posix)
    except ValueError:
        # Unbalanced quotes, heredocs -- fall back to whitespace.
        return command.split()
    if posix:
        return tokens
    return [
        token[1:-1] if len(token) > 1 and token[0] == token[-1] and token[0] in "\"'"
        else token
        for token in tokens
    ]


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


def _bash_paths(command, cwd):
    """File arguments inside a shell command, resolved against the record's cwd.

    A file read with `cat invoice.csv` leaves no other trace in the transcript,
    so without this a genuinely-used file is silently absent from the evidence —
    which is the one failure an audit trail cannot have.

    Heuristic by necessity: shell is not parseable in general. It over-collects
    rather than under-collects, and the existence check downstream discards the
    noise.
    """
    found = []
    for token in _tokenize(command):
        if token.startswith("-") or "://" in token:
            continue  # a flag, or a URL that merely looks path-shaped
        if not re.search(r"[\\/]", token) and not re.search(r"\.[A-Za-z0-9]{1,8}$", token):
            continue
        path = token if _is_absolute(token) else os.path.join(cwd or "", token)
        found.append(os.path.normpath(path))
    return found


def _paths_in_record(record):
    """Every absolute path this record refers to, with how it was referred to."""
    found = []
    cwd = record.get("cwd")
    for block in _content_blocks(record):
        kind = block.get("type")
        if kind != "tool_use":
            if kind == "text":
                for match in PATH_RE.findall(block.get("text") or ""):
                    found.append((match, "user_prose"))
            continue

        name = block.get("name")
        payload = block.get("input") or {}
        if name in FILE_TOOLS:
            target = payload.get("file_path")
            if isinstance(target, str):
                found.append((target, "tool_call"))
        elif name == "Bash":
            command = payload.get("command")
            if isinstance(command, str):
                for path in _bash_paths(command, cwd):
                    found.append((path, "bash"))
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
            key = _dedupe_key(candidate)
            if key in seen or _excluded(candidate):
                continue
            file_path = Path(candidate)
            if not file_path.is_file():
                continue
            stat = file_path.stat()
            seen[key] = {
                "path": candidate,
                "bytes": stat.st_size,
                "mime": mimetypes.guess_type(candidate)[0] or "application/octet-stream",
                "mtime": int(stat.st_mtime),
                "first_turn": turn,
                "source": source,
            }
    return list(seen.values())


def unattended():
    """Whether anybody is there to answer a question.

    Dough writes DOUGH_UNATTENDED=1 into a hosted box's config.env, which is
    sourced with `set -a` before the agent starts. But an environment variable
    is invisible to a MODEL: Claude Code does not put the process environment
    into its context, so an agent cannot read this however carefully it is
    instructed to.

    Reporting it HERE is what makes it observable. The skill already requires
    the complete output of `scan` to be read, so the flag arrives through a
    channel the flow depends on rather than one the agent has to know to check.

    Exactly "1". An operator turning this off would write 0 or empty, and a
    truthiness test on the string would read both as on.
    """
    return os.environ.get("DOUGH_UNATTENDED", "") == "1"


def cmd_scan(args):
    path = find_transcript(args.cwd, args.session_id, args.home)
    stat = Path(path).stat()
    json.dump(
        {
            "sessionId": Path(path).stem,
            "unattended": unattended(),
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


def snapshot_transcript(transcript_path, into=None):
    """Freeze the transcript before hashing it.

    The session keeps writing to this file — the user confirming the upload adds
    to the very bytes being hashed. Hash the live file and the sha256 is already
    stale by the time the upload runs, so strict server-side verification refuses
    every proposal.

    Freezing is also the more truthful artifact: the evidence should be the
    session as it stood when the proposal was made, not one that has grown to
    include the approval conversation that followed.
    """
    directory = Path(into) if into else Path(tempfile.mkdtemp(prefix="dough-evidence-"))
    directory.mkdir(parents=True, exist_ok=True)
    frozen = directory / Path(transcript_path).name
    shutil.copyfile(transcript_path, frozen)
    return str(frozen)


def declare_objects(transcript_path, files, snapshot_dir=None):
    """The exact `objects[]` array proposals.evidence.begin expects.

    The transcript is always key "transcript"; curated files are f0, f1, ... in
    the order the agent listed them, so a manifest entry maps back to an upload.
    """
    frozen = snapshot_transcript(transcript_path, snapshot_dir)
    stat = Path(frozen).stat()
    objects = [
        {
            "key": "transcript",
            "role": "transcript",
            "filename": Path(frozen).name,
            "mime": "application/x-ndjson",
            "bytes": stat.st_size,
            "sha256": sha256_file(frozen),
        }
    ]
    paths = {"transcript": frozen}
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
    objects, paths = declare_objects(transcript_path, args.files, args.snapshot_dir)
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
    declare.add_argument(
        "--snapshot-dir",
        help="Where to freeze the transcript. Defaults to a fresh temp dir.",
    )
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
