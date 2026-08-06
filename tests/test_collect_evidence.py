import importlib.util
import json
import os
import subprocess
import sys
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
