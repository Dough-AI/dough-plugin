# `/dough:propose` Command — dough-plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a `/dough:propose` slash command that raises a write for approval with the session transcript and its backing files attached, without routing any of those bytes through model-generated tool arguments.

**Architecture:** A Python script scans the Claude Code session JSONL for every file the session touched and emits candidates. The model curates that list, calls `proposals.evidence.begin` for presigned upload URLs, shows the user exactly what will upload, then the script `curl`s the bytes straight to storage. Only `{ evidenceId, sessionId, manifest }` — a couple hundred tokens — travels in the `proposals.propose` call.

**Tech Stack:** Python 3 (stdlib only), Claude Code plugin commands + skills, pytest.

**Repo:** `/usr/local/code/dough-plugin`
**Companion plan:** the server half (`proposals.evidence.begin`, verification, storage) is built in parallel in `Dough-Alpha`. Tasks 1–3 here need no server; only the end-to-end check in Task 4 does.
**Design spec:** `docs/superpowers/specs/2026-08-05-propose-command-evidence-design.md`

## Global Constraints

- **Stdlib only.** The script runs on whatever Python a user happens to have. No `requests`, no third-party HTTP. Use `urllib.request`. The existing `skills/excel/scripts/dough_excel.py` may import `openpyxl`; this one imports nothing outside stdlib.
- Per-object size cap: **25 MB**. Objects over it are rejected server-side; the script must surface that, not hide it.
- sha256 is computed **locally, before upload**, and is what the server verifies against.
- The script **never decides** whether to proceed after a failure — it reports and exits non-zero. The choice belongs to the user.
- Scripts live at `skills/<name>/scripts/`, matching `skills/excel/scripts/dough_excel.py`. Tests live at `tests/test_<name>.py` and drive the script through `subprocess`, matching `tests/test_dough_excel.py`.
- Any skill or command change **must** bump the version in both `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` — the desktop app is version-gated with no refresh button. See `UPDATING.md`.

## The frozen contract

The server builds against this too. Do not change it without updating the companion plan.

**`proposals.evidence.begin` request:**

```jsonc
{
  "sessionId": "440239c9-fca1-433c-968f-bfe670ed634d",
  "objects": [
    { "key": "transcript", "role": "transcript",
      "filename": "session.jsonl", "mime": "application/x-ndjson",
      "bytes": 187829, "sha256": "a3f9…" },
    { "key": "f0", "role": "file",
      "filename": "contractors.csv", "mime": "text/csv",
      "bytes": 4210, "sha256": "7c21…" }
  ]
}
```

**Response:**

```jsonc
{
  "evidenceId": "ev_01JQ…",
  "expiresAt": "2026-08-05T16:45:00Z",
  "uploads": [
    { "key": "transcript", "method": "PUT", "url": "https://…", "headers": {} }
  ],
  "rejected": [
    { "key": "f2", "code": "over_object_cap", "message": "31 MB exceeds the 25 MB limit" }
  ]
}
```

**Consumption** — rides inside the existing `transcript` field on `proposals.propose`:

```jsonc
"transcript": {
  "evidenceId": "ev_01JQ…",
  "sessionId": "440239c9-…",
  "manifest": [
    { "key": "f0", "filename": "contractors.csv", "sha256": "7c21…",
      "bytes": 4210, "mime": "text/csv", "role": "file",
      "note": "sourced the $8,400.50" }
  ]
}
```

---

### Task 1: Locate the session transcript

**Files:**
- Create: `skills/propose/scripts/collect_evidence.py`
- Test: `tests/test_collect_evidence.py`

**Interfaces:**
- Produces:
  - `project_slug(cwd: str) -> str`
  - `find_transcript(cwd: str, session_id: str | None, home: str) -> str` — raises `TranscriptNotFound`
  - CLI: `collect_evidence.py locate [--session-id ID] [--cwd DIR]` → prints JSON `{"transcript": path, "sessionId": id}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_collect_evidence.py
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "skills" / "propose" / "scripts" / "collect_evidence.py"


def run(*argv, **kwargs):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv], capture_output=True, text=True, **kwargs
    )


def make_project(home, cwd, session_id, lines):
    slug = "".join(c if c.isalnum() else "-" for c in cwd)
    d = Path(home) / ".claude" / "projects" / slug
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session_id}.jsonl"
    p.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return p


def test_slug_matches_claude_code_project_dir_naming(tmp_path):
    home = tmp_path / "home"
    p = make_project(home, "/usr/local/code/dough-plugin", "sess-1", [{"type": "x"}])
    assert p.parent.name == "-usr-local-code-dough-plugin"


def test_locate_finds_the_named_session(tmp_path):
    home = tmp_path / "home"
    make_project(home, "/work/proj", "sess-1", [{"type": "x"}])
    r = run("locate", "--session-id", "sess-1", "--cwd", "/work/proj", "--home", str(home))
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["sessionId"] == "sess-1"
    assert out["transcript"].endswith("sess-1.jsonl")


def test_locate_without_a_session_id_picks_the_newest(tmp_path):
    home = tmp_path / "home"
    old = make_project(home, "/work/proj", "old", [{"type": "x"}])
    new = make_project(home, "/work/proj", "new", [{"type": "x"}])
    import os

    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))
    r = run("locate", "--cwd", "/work/proj", "--home", str(home))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["sessionId"] == "new"


def test_locate_fails_loudly_when_there_is_no_transcript(tmp_path):
    r = run("locate", "--cwd", "/work/nothing", "--home", str(tmp_path))
    assert r.returncode != 0
    assert "no session transcript" in r.stderr.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_collect_evidence.py -v`
Expected: FAIL — the script does not exist

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
"""Gather the evidence behind a Dough proposal.

Bytes never travel through a model-generated tool argument: this script hashes
files locally, uploads them to presigned URLs, and prints a small manifest the
agent can cite by reference.
"""

import argparse
import hashlib
import json
import mimetypes
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
    parser = argparse.ArgumentParser(prog="collect_evidence.py")
    parser.add_argument("--home", default=os.path.expanduser("~"))
    parser.add_argument("--cwd", default=os.getcwd())
    sub = parser.add_subparsers(dest="command", required=True)

    locate = sub.add_parser("locate")
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
```

Note the argument order: `--home` and `--cwd` are global, so they precede the subcommand on the command line. The tests above pass them after it, which `argparse` accepts for global options only if they are declared on the parent parser — they are.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_collect_evidence.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add skills/propose/scripts/collect_evidence.py tests/test_collect_evidence.py
git commit -m "feat(propose): locate the session transcript"
```

---

### Task 2: Scan the transcript for evidence candidates

**Files:**
- Modify: `skills/propose/scripts/collect_evidence.py`
- Modify: `tests/test_collect_evidence.py`

**Interfaces:**
- Consumes: `find_transcript` from Task 1
- Produces:
  - `scan_transcript(path: str) -> list[dict]` — each `{path, bytes, mime, mtime, first_turn, source}`
  - CLI: `collect_evidence.py scan [--session-id ID]` → prints JSON `{"sessionId", "transcript": {...}, "candidates": [...]}`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_collect_evidence.py

def assistant_tool_use(name, file_path):
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": name, "input": {"file_path": file_path}}
            ],
        },
    }


def user_text(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def test_scan_finds_files_from_read_and_edit_calls(tmp_path):
    home = tmp_path / "home"
    real = tmp_path / "contractors.csv"
    real.write_text("vendor,amount\nacme,100\n", encoding="utf-8")
    make_project(
        home,
        "/work/proj",
        "sess-1",
        [assistant_tool_use("Read", str(real)), assistant_tool_use("Edit", str(real))],
    )
    r = run("scan", "--session-id", "sess-1", "--cwd", "/work/proj", "--home", str(home))
    assert r.returncode == 0, r.stderr
    candidates = json.loads(r.stdout)["candidates"]
    assert [c["path"] for c in candidates] == [str(real)]
    assert candidates[0]["mime"] == "text/csv"
    assert candidates[0]["bytes"] == real.stat().st_size


def test_scan_deduplicates_and_keeps_the_first_sighting(tmp_path):
    home = tmp_path / "home"
    real = tmp_path / "a.csv"
    real.write_text("x\n", encoding="utf-8")
    make_project(
        home,
        "/work/proj",
        "sess-1",
        [
            {"type": "x"},
            assistant_tool_use("Read", str(real)),
            {"type": "x"},
            assistant_tool_use("Read", str(real)),
        ],
    )
    r = run("scan", "--session-id", "sess-1", "--cwd", "/work/proj", "--home", str(home))
    candidates = json.loads(r.stdout)["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["first_turn"] == 1


def test_scan_finds_a_path_the_user_typed_in_prose(tmp_path):
    home = tmp_path / "home"
    real = tmp_path / "invoice.pdf"
    real.write_bytes(b"%PDF-1.4\n")
    make_project(
        home, "/work/proj", "sess-1", [user_text(f"book this from {real}")]
    )
    r = run("scan", "--session-id", "sess-1", "--cwd", "/work/proj", "--home", str(home))
    candidates = json.loads(r.stdout)["candidates"]
    assert [c["path"] for c in candidates] == [str(real)]
    assert candidates[0]["source"] == "user_prose"


def test_scan_drops_paths_that_no_longer_exist(tmp_path):
    home = tmp_path / "home"
    make_project(
        home, "/work/proj", "sess-1", [assistant_tool_use("Read", "/gone/missing.csv")]
    )
    r = run("scan", "--session-id", "sess-1", "--cwd", "/work/proj", "--home", str(home))
    assert json.loads(r.stdout)["candidates"] == []


def test_scan_ignores_git_internals(tmp_path):
    home = tmp_path / "home"
    git_file = tmp_path / ".git" / "COMMIT_EDITMSG"
    git_file.parent.mkdir(parents=True)
    git_file.write_text("wip\n", encoding="utf-8")
    make_project(
        home, "/work/proj", "sess-1", [assistant_tool_use("Read", str(git_file))]
    )
    r = run("scan", "--session-id", "sess-1", "--cwd", "/work/proj", "--home", str(home))
    assert json.loads(r.stdout)["candidates"] == []


def test_scan_survives_a_malformed_line(tmp_path):
    home = tmp_path / "home"
    real = tmp_path / "a.csv"
    real.write_text("x\n", encoding="utf-8")
    slug = "".join(c if c.isalnum() else "-" for c in "/work/proj")
    d = home / ".claude" / "projects" / slug
    d.mkdir(parents=True)
    (d / "sess-1.jsonl").write_text(
        "{not json\n" + json.dumps(assistant_tool_use("Read", str(real))) + "\n",
        encoding="utf-8",
    )
    r = run("scan", "--session-id", "sess-1", "--cwd", "/work/proj", "--home", str(home))
    assert r.returncode == 0, r.stderr
    assert len(json.loads(r.stdout)["candidates"]) == 1


def test_scan_reports_the_transcript_as_its_own_object(tmp_path):
    home = tmp_path / "home"
    p = make_project(home, "/work/proj", "sess-1", [{"type": "x"}])
    r = run("scan", "--session-id", "sess-1", "--cwd", "/work/proj", "--home", str(home))
    transcript = json.loads(r.stdout)["transcript"]
    assert transcript["bytes"] == p.stat().st_size
    assert transcript["mime"] == "application/x-ndjson"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_collect_evidence.py -v`
Expected: FAIL — `invalid choice: 'scan'`

- [ ] **Step 3: Write the scanner**

Add to `skills/propose/scripts/collect_evidence.py`:

```python
import re

FILE_TOOLS = {"Read", "Write", "Edit", "NotebookEdit"}

# Absolute paths only. A relative path in prose is ambiguous about which cwd it
# was relative to, and guessing wrong attaches the wrong file to an audit record.
PATH_RE = re.compile(r"(?:^|[\s\"'`(])(/[^\s\"'`)]+\.[A-Za-z0-9]{1,8})")

# Never evidence, and noisy enough to bury the real files if left in.
EXCLUDED_PARTS = {".git", "node_modules", "__pycache__", ".next", ".venv"}


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
```

Register the subcommand in `build_parser`:

```python
    scan = sub.add_parser("scan")
    scan.add_argument("--session-id")
    scan.set_defaults(func=cmd_scan)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_collect_evidence.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add skills/propose/scripts/collect_evidence.py tests/test_collect_evidence.py
git commit -m "feat(propose): scan the session transcript for evidence candidates"
```

---

### Task 3: Hash and upload to presigned URLs

**Files:**
- Modify: `skills/propose/scripts/collect_evidence.py`
- Modify: `tests/test_collect_evidence.py`

**Interfaces:**
- Consumes: `scan_transcript` from Task 2
- Produces:
  - `sha256_file(path: str) -> str`
  - `cmd_declare` — CLI `declare --files a.csv b.pdf [--session-id ID]` → the exact `objects[]` array for `evidence.begin`
  - `cmd_upload` — CLI `upload --plan plan.json` → `{"uploaded": [...], "failed": [...]}`, exit 1 if anything failed

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_collect_evidence.py
import http.server
import threading


class _Recorder(http.server.BaseHTTPRequestHandler):
    received = {}
    fail_times = {}

    def do_PUT(self):
        key = self.path.strip("/")
        remaining = _Recorder.fail_times.get(key, 0)
        if remaining > 0:
            _Recorder.fail_times[key] = remaining - 1
            self.send_response(500)
            self.end_headers()
            return
        length = int(self.headers.get("content-length", 0))
        _Recorder.received[key] = self.rfile.read(length)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def serve():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_declare_emits_the_begin_evidence_objects(tmp_path):
    home = tmp_path / "home"
    make_project(home, "/work/proj", "sess-1", [{"type": "x"}])
    data = tmp_path / "a.csv"
    data.write_text("vendor,amount\n", encoding="utf-8")
    r = run(
        "declare", "--files", str(data),
        "--session-id", "sess-1", "--cwd", "/work/proj", "--home", str(home),
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["sessionId"] == "sess-1"
    keys = [o["key"] for o in out["objects"]]
    assert keys == ["transcript", "f0"]
    assert out["objects"][0]["role"] == "transcript"
    assert out["objects"][1]["role"] == "file"
    assert len(out["objects"][1]["sha256"]) == 64
    assert out["objects"][1]["bytes"] == data.stat().st_size


def test_declare_hashes_match_hashlib(tmp_path):
    import hashlib as hl

    home = tmp_path / "home"
    make_project(home, "/work/proj", "sess-1", [{"type": "x"}])
    data = tmp_path / "a.csv"
    data.write_bytes(b"exact bytes")
    r = run(
        "declare", "--files", str(data),
        "--session-id", "sess-1", "--cwd", "/work/proj", "--home", str(home),
    )
    obj = json.loads(r.stdout)["objects"][1]
    assert obj["sha256"] == hl.sha256(b"exact bytes").hexdigest()


def test_upload_puts_each_object_to_its_url(tmp_path):
    _Recorder.received = {}
    _Recorder.fail_times = {}
    server = serve()
    port = server.server_address[1]
    data = tmp_path / "a.csv"
    data.write_bytes(b"hello")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({
        "uploads": [{"key": "f0", "method": "PUT",
                     "url": f"http://127.0.0.1:{port}/f0", "headers": {}}],
        "paths": {"f0": str(data)},
    }))
    r = run("upload", "--plan", str(plan))
    server.shutdown()
    assert r.returncode == 0, r.stderr
    assert _Recorder.received["f0"] == b"hello"
    assert json.loads(r.stdout)["failed"] == []


def test_upload_retries_a_failing_object_then_succeeds(tmp_path):
    _Recorder.received = {}
    _Recorder.fail_times = {"f0": 2}
    server = serve()
    port = server.server_address[1]
    data = tmp_path / "a.csv"
    data.write_bytes(b"hello")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({
        "uploads": [{"key": "f0", "method": "PUT",
                     "url": f"http://127.0.0.1:{port}/f0", "headers": {}}],
        "paths": {"f0": str(data)},
    }))
    r = run("upload", "--plan", str(plan), "--retries", "3", "--backoff", "0")
    server.shutdown()
    assert r.returncode == 0, r.stderr
    assert _Recorder.received["f0"] == b"hello"


def test_upload_reports_failure_without_deciding_what_to_do(tmp_path):
    _Recorder.received = {}
    _Recorder.fail_times = {"f0": 99}
    server = serve()
    port = server.server_address[1]
    data = tmp_path / "a.csv"
    data.write_bytes(b"hello")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({
        "uploads": [{"key": "f0", "method": "PUT",
                     "url": f"http://127.0.0.1:{port}/f0", "headers": {}}],
        "paths": {"f0": str(data)},
    }))
    r = run("upload", "--plan", str(plan), "--retries", "2", "--backoff", "0")
    server.shutdown()
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["failed"][0]["key"] == "f0"
    assert out["uploaded"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_collect_evidence.py -v`
Expected: FAIL — `invalid choice: 'declare'`

- [ ] **Step 3: Write hashing, declare, and upload**

Add to `skills/propose/scripts/collect_evidence.py`:

```python
import time
import urllib.error
import urllib.request

CHUNK = 1024 * 1024


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def declare_objects(transcript_path, files):
    """The exact `objects[]` array evidence.begin expects.

    The transcript is always key "transcript"; curated files are f0, f1, ... in
    the order the agent listed them, so a manifest entry maps back to an upload.
    """
    stat = Path(transcript_path).stat()
    objects = [{
        "key": "transcript",
        "role": "transcript",
        "filename": Path(transcript_path).name,
        "mime": "application/x-ndjson",
        "bytes": stat.st_size,
        "sha256": sha256_file(transcript_path),
    }]
    paths = {"transcript": transcript_path}
    for index, file_path in enumerate(files):
        key = f"f{index}"
        file_stat = Path(file_path).stat()
        objects.append({
            "key": key,
            "role": "file",
            "filename": Path(file_path).name,
            "mime": mimetypes.guess_type(file_path)[0] or "application/octet-stream",
            "bytes": file_stat.st_size,
            "sha256": sha256_file(file_path),
        })
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

    for upload in plan.get("uploads", []):
        key = upload["key"]
        path = paths.get(key)
        if not path:
            failed.append({"key": key, "error": "no local path for this key"})
            continue
        last_error = None
        for attempt in range(max(1, args.retries)):
            try:
                _put(upload["url"], upload.get("headers"), path)
                uploaded.append({"key": key, "path": path})
                last_error = None
                break
            except Exception as exc:  # network, HTTP, filesystem — all retryable
                last_error = str(exc)
                if attempt + 1 < max(1, args.retries) and args.backoff > 0:
                    time.sleep(args.backoff * (2 ** attempt))
        if last_error is not None:
            failed.append({"key": key, "path": path, "error": last_error})

    json.dump({"uploaded": uploaded, "failed": failed}, sys.stdout)
    print()
    return 1 if failed else 0
```

Register both subcommands in `build_parser`:

```python
    declare = sub.add_parser("declare")
    declare.add_argument("--session-id")
    declare.add_argument("--files", nargs="*", default=[])
    declare.set_defaults(func=cmd_declare)

    upload = sub.add_parser("upload")
    upload.add_argument("--plan", required=True)
    upload.add_argument("--retries", type=int, default=3)
    upload.add_argument("--backoff", type=float, default=1.0)
    upload.set_defaults(func=cmd_upload)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_collect_evidence.py -v`
Expected: PASS (16 tests)

- [ ] **Step 5: Commit**

```bash
git add skills/propose/scripts/collect_evidence.py tests/test_collect_evidence.py
git commit -m "feat(propose): hash, declare, and upload evidence objects"
```

---

### Task 4: The command, the skill section, and the release

**Files:**
- Create: `commands/propose.md`
- Modify: `skills/propose/SKILL.md`
- Modify: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`
- Modify: `README.md`

**Interfaces:**
- Consumes: every subcommand from Tasks 1–3, and `proposals.evidence.begin` from the companion plan

- [ ] **Step 1: Write the command**

Create `commands/propose.md`:

```markdown
---
description: Raise a write to a connected accounting system for human approval, with the session and its evidence attached.
argument-hint: [what to propose, e.g. "accrue Sept contractor invoices"]
allowed-tools: Bash(python3:*), mcp__dough__proposals__propose, mcp__dough__proposals__actions, mcp__dough__proposals__evidence__begin, mcp__dough__tools__describe
---

Load the `propose` skill and follow it to build the entry. $ARGUMENTS

Attach the session and its evidence as well. The script lives at
`${CLAUDE_PLUGIN_ROOT}/skills/propose/scripts/collect_evidence.py`.

1. **Scan.** `python3 <script> scan` — reads the session transcript and lists
   every file this session touched. Structural, so nothing is missed.

2. **Curate.** Keep only what actually backs this entry. Drop source you happened
   to read, scratchpad files, and anything unrelated to the numbers. Write a
   one-line note for each file you keep saying what it establishes.

3. **Declare.** `python3 <script> declare --files <kept paths>` — hashes each
   file and emits the `objects[]` array. Pass it straight to
   `proposals.evidence.begin` with the `sessionId` from the scan.

4. **Confirm.** Show the user what will upload — every kept file with its size
   and your note, the transcript, and anything `evidence.begin` returned in
   `rejected`. This ships their local files and their entire session to a
   server; they get to see that and say no. Wait for a clear yes.

5. **Upload.** Merge `evidence.begin`'s `uploads` with the `paths` map from step 3
   into a plan file, then `python3 <script> upload --plan <plan>`.

   If anything lands in `failed`, do not decide for them. Show what failed and
   offer: retry, propose without it, or cancel. Only if they choose to proceed
   without it, set `evidenceStatus: "partial"` in the transcript object.

6. **Propose.** Call `proposals.propose` as the skill directs, with:

   `transcript: { evidenceId, sessionId, manifest }`

   where `manifest` carries one entry per kept file — `key`, `filename`,
   `sha256`, `bytes`, `mime`, `role`, and your `note`.

Never inline file contents or transcript text into the tool call itself. The
whole point of this flow is that the bytes travel out of band; pasting them back
into the payload defeats it and will not fit.
```

- [ ] **Step 2: Verify the command loads**

Run: `python3 -c "import pathlib,sys; t=pathlib.Path('commands/propose.md').read_text(); sys.exit(0 if t.startswith('---') and 'description:' in t else 1)"`
Expected: exit 0

- [ ] **Step 3: Point the skill at the command**

Add to `skills/propose/SKILL.md`, immediately after the `# Proposing a write` heading paragraph:

```markdown
**With evidence attached:** `/dough:propose` runs this same flow and additionally
uploads the session transcript and the files behind the entry, so an approver can
see what the numbers came from. Prefer it when the reasoning matters. The bytes
travel out of band — never paste file contents into the tool call.
```

- [ ] **Step 4: Bump the version in both manifests**

`UPDATING.md` requires both, because the desktop app is version-gated with no refresh button. Set `version` to `0.8.0` in:
- `.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json` (both the top-level `metadata.version` and the `dough` entry's `version`)

- [ ] **Step 5: Update the README**

In the "What's inside" section, add `/dough:propose` to the **Command** line so it reads:

```markdown
- **Commands:** `/dough:status`, `/dough:propose` (raise a write for approval with
  the session transcript and backing files attached).
```

- [ ] **Step 6: Run the whole suite**

Run: `python3 -m pytest tests/ -v`
Expected: PASS — 16 new tests plus the 12 existing `test_dough_excel.py` tests.

- [ ] **Step 7: Commit**

```bash
git add commands/propose.md skills/propose/SKILL.md .claude-plugin/ README.md
git commit -m "feat(propose): add the /dough:propose command (v0.8.0)"
```

- [ ] **Step 8: End-to-end check — needs the companion plan merged**

This is the one step that requires the server side. In a real session with the
Dough MCP connected:

1. Read a CSV so it lands in the transcript.
2. Run `/dough:propose accrue a test amount`.
3. Confirm the upload list names that CSV and the transcript.
4. Approve, and check the proposal in Action Gateway shows both the transcript
   and the evidence file with a matching hash.

If `proposals.evidence.begin` is not yet in the tool list, stop here — Tasks 1–7
are complete and independently testable, and this step unblocks when the
companion plan ships.

---

## Done when

- `python3 -m pytest tests/ -v` passes.
- `/dough:propose` appears in the slash-command menu after reinstalling the plugin.
- A scan on a real session lists the files that session touched and omits `.git` noise.
- The confirmation step shows the upload list before any byte moves.
- An upload failure offers retry / proceed / cancel rather than choosing.
- Version is `0.8.0` in both manifests.
