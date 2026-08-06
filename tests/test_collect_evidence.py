import http.server
import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "skills" / "propose" / "scripts" / "collect_evidence.py"


def load_module():
    """Import the script directly so unit-level helpers can be tested without
    going through the CLI."""
    spec = importlib.util.spec_from_file_location("collect_evidence", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_slug_matches_claude_code_project_dir_naming():
    project_slug = load_module().project_slug
    assert project_slug("/usr/local/code/dough-plugin") == "-usr-local-code-dough-plugin"
    assert project_slug("/work/proj_1") == "-work-proj-1"


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
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))
    r = run("locate", "--cwd", "/work/proj", "--home", str(home))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["sessionId"] == "new"


def test_locate_fails_loudly_when_there_is_no_transcript(tmp_path):
    r = run("locate", "--cwd", "/work/nothing", "--home", str(tmp_path))
    assert r.returncode != 0
    assert "no session transcript" in r.stderr.lower()


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
    make_project(home, "/work/proj", "sess-1", [user_text(f"book this from {real}")])
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
    make_project(home, "/work/proj", "sess-1", [assistant_tool_use("Read", str(git_file))])
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


def upload_plan(tmp_path, port, key="f0", body=b"hello"):
    data = tmp_path / "a.csv"
    data.write_bytes(body)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "uploads": [
                    {
                        "key": key,
                        "method": "PUT",
                        "url": f"http://127.0.0.1:{port}/{key}",
                        "headers": {},
                    }
                ],
                "paths": {key: str(data)},
            }
        )
    )
    return str(plan)


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
    assert [o["key"] for o in out["objects"]] == ["transcript", "f0"]
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


def test_declare_rejects_a_path_that_is_not_a_file(tmp_path):
    home = tmp_path / "home"
    make_project(home, "/work/proj", "sess-1", [{"type": "x"}])
    r = run(
        "declare", "--files", "/gone/missing.csv",
        "--session-id", "sess-1", "--cwd", "/work/proj", "--home", str(home),
    )
    assert r.returncode != 0
    assert "not a file" in r.stderr.lower()


def test_upload_puts_each_object_to_its_url(tmp_path):
    _Recorder.received, _Recorder.fail_times = {}, {}
    server = serve()
    plan = upload_plan(tmp_path, server.server_address[1])
    r = run("upload", "--plan", plan)
    server.shutdown()
    assert r.returncode == 0, r.stderr
    assert _Recorder.received["f0"] == b"hello"
    assert json.loads(r.stdout)["failed"] == []


def test_upload_retries_a_failing_object_then_succeeds(tmp_path):
    _Recorder.received, _Recorder.fail_times = {}, {"f0": 2}
    server = serve()
    plan = upload_plan(tmp_path, server.server_address[1])
    r = run("upload", "--plan", plan, "--retries", "3", "--backoff", "0")
    server.shutdown()
    assert r.returncode == 0, r.stderr
    assert _Recorder.received["f0"] == b"hello"


def test_upload_reports_failure_without_deciding_what_to_do(tmp_path):
    _Recorder.received, _Recorder.fail_times = {}, {"f0": 99}
    server = serve()
    plan = upload_plan(tmp_path, server.server_address[1])
    r = run("upload", "--plan", plan, "--retries", "2", "--backoff", "0")
    server.shutdown()
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["failed"][0]["key"] == "f0"
    assert out["uploaded"] == []
