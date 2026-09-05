"""Cover the fake `dough` the propose e2e depends on.

The e2e is opt-in (DOUGH_E2E=1), costs tokens and needs auth, so it is not run
on most changes. A fake that silently broke would take the e2e down with it and
nobody would find out until someone paid for a run — so the stand-in gets its
own coverage here, in the suite that actually runs.

This is not testing the real CLI. It pins the INTERFACE the e2e leans on: the
subcommands the command file invokes, the exit codes it branches on, and the
stdout shape it parses.
"""

import hashlib
import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
FAKE = REPO / "tests" / "fake_dough.py"


def run(args, env=None, cwd=None):
    return subprocess.run(
        [sys.executable, str(FAKE), *args],
        capture_output=True, text=True, timeout=60, env=env, cwd=cwd,
    )


def test_help_succeeds_and_names_the_transcript():
    """The gate in propose.md probes `dough evidence --help` and branches on its
    exit code, so a non-zero here would make every run report an old CLI."""
    result = run(["evidence", "--help"])
    assert result.returncode == 0, result.stderr
    assert "SESSION TRANSCRIPT" in result.stdout


@pytest.mark.parametrize("argv", [["bogus"], ["evidence", "frobnicate"], []])
def test_unknown_commands_exit_2(argv):
    """The other side of that branch. An old binary answers `unknown command`
    and a non-zero exit; if the fake ever returned 0 the e2e would sail past the
    gate it is meant to exercise."""
    assert run(argv).returncode == 2


def _sink(received):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_PUT(self):
            body = self.rfile.read(int(self.headers.get("content-length", 0)))
            received[self.path] = body
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_upload_declares_then_puts_every_object(tmp_path):
    """The whole contract in one run: it finds the transcript from --session,
    declares the transcript PLUS each --file, and the bytes that arrive match
    what it declared."""
    work = tmp_path / "work"
    work.mkdir()
    home = tmp_path / "home"
    slug = "".join(c if c.isalnum() else "-" for c in str(work))
    projects = home / ".claude" / "projects" / slug
    projects.mkdir(parents=True)
    (projects / "sess.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    backing = work / "backing.csv"
    backing.write_text("vendor,amount\nAcme,10\n", encoding="utf-8")

    log = tmp_path / "calls.jsonl"
    received = {}
    sink = _sink(received)
    env = {
        "DOUGH_FAKE_LOG": str(log),
        "DOUGH_FAKE_SINK": f"http://127.0.0.1:{sink.server_address[1]}",
        "PATH": "/usr/bin:/bin",
    }
    result = run(
        ["evidence", "upload", "--session", "sess",
         "--home", str(home), "--cwd", str(work), "--file", str(backing)],
        env=env, cwd=str(work),
    )
    sink.shutdown()
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    printed = json.loads(result.stdout)
    assert printed["evidenceId"].startswith("ev_")
    assert {u["key"] for u in printed["uploaded"]} == {"transcript", "f0"}
    assert printed["failed"] == []

    declared = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    objects = declared[0]["objects"]
    assert [o["key"] for o in objects] == ["transcript", "f0"]
    assert [o["role"] for o in objects] == ["transcript", "file"]

    # What arrived is what was declared — the assertion the e2e inherits.
    assert len(received) == 2
    for obj in objects:
        body = received[f"/{printed['evidenceId']}/{obj['key']}"]
        assert hashlib.sha256(body).hexdigest() == obj["sha256"]
        assert len(body) == obj["bytes"]


def test_upload_refuses_a_missing_file_before_declaring_anything(tmp_path):
    log = tmp_path / "calls.jsonl"
    result = run(
        ["evidence", "upload", "--session", "sess", "--file", str(tmp_path / "nope.csv")],
        env={"DOUGH_FAKE_LOG": str(log), "DOUGH_FAKE_SINK": "http://127.0.0.1:1",
             "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 2
    assert not log.exists(), "declared something despite refusing the input"


def test_stdout_is_pure_json_and_the_notice_goes_to_stderr(tmp_path):
    """propose.md parses stdout. A stray line there reads as a failed upload."""
    work = tmp_path / "w"
    work.mkdir()
    home = tmp_path / "h"
    slug = "".join(c if c.isalnum() else "-" for c in str(work))
    projects = home / ".claude" / "projects" / slug
    projects.mkdir(parents=True)
    (projects / "s.jsonl").write_text('{"a":1}\n', encoding="utf-8")

    received = {}
    sink = _sink(received)
    result = run(
        ["evidence", "upload", "--session", "s", "--home", str(home), "--cwd", str(work)],
        env={"DOUGH_FAKE_LOG": str(tmp_path / "l.jsonl"),
             "DOUGH_FAKE_SINK": f"http://127.0.0.1:{sink.server_address[1]}",
             "PATH": "/usr/bin:/bin"},
        cwd=str(work),
    )
    sink.shutdown()
    json.loads(result.stdout)  # raises if anything else landed on stdout
    assert "Attaching 1 object" in result.stderr
