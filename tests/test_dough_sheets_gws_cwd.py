"""gws must never write into the folder dough_sheets.py is run from.

gws saves a response that isn't JSON as download.<ext> in its working folder,
and Drive answers a delete with an empty one. Run from the caller's folder, the
staging spreadsheet's delete left a download.html in an agent's own folder, which
Agent Factory then reported as the build changing the agent (USE-734).

Nothing here calls Google: `subprocess.run` is replaced with a stand-in that does
what gws does with an empty response, writing download.html into its cwd.
"""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "skills" / "google-sheets" / "scripts" / "dough_sheets.py"
GWS_CONNECT = ROOT / "skills" / "gws-connect" / "SKILL.md"


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sheets(monkeypatch):
    """The script, with a fake gws: every call writes download.html into the
    folder it runs in and prints `stdout`. Records each call's cwd."""
    module = load(SCRIPT)
    calls = []

    def fake_run(argv, **kwargs):
        where = Path(kwargs.get("cwd") or os.getcwd())
        (where / "download.html").write_text("", encoding="utf-8")
        calls.append({"argv": argv, "cwd": kwargs.get("cwd")})
        return subprocess.CompletedProcess(argv, 0, stdout=fake_run.stdout, stderr="Using keyring backend: x\n")

    fake_run.stdout = ""
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module.fake_run, module.calls = fake_run, calls
    return module


@pytest.fixture
def agent(tmp_path, monkeypatch):
    """An agent folder, as the caller's working directory."""
    folder = tmp_path / "agent"
    folder.mkdir()
    (folder / "build.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(folder)
    return folder


def gws(module):
    instance = object.__new__(module.Gws)
    instance.binary = "gws"
    return instance


def test_a_call_with_no_cwd_leaves_the_callers_folder_untouched(sheets, agent):
    gws(sheets).call("drive", "files", "delete", params={"fileId": "x"})
    assert sorted(p.name for p in agent.iterdir()) == ["build.py"]
    assert sheets.calls[0]["cwd"] not in (None, str(agent))


def test_the_throwaway_folder_is_removed(sheets, agent):
    gws(sheets).call("drive", "files", "delete", params={"fileId": "x"})
    assert not Path(sheets.calls[0]["cwd"]).exists()


def test_closing_the_staging_spreadsheet_leaves_the_callers_folder_untouched(sheets, agent):
    staging = object.__new__(sheets.Staging)
    staging.gws, staging.sid = gws(sheets), "staging-id"
    staging.close()
    assert sheets.calls[0]["argv"][1:4] == ["drive", "files", "delete"]
    assert sorted(p.name for p in agent.iterdir()) == ["build.py"]
    assert staging.sid is None


def test_a_failed_staging_delete_is_a_note_not_a_failure(sheets, agent, monkeypatch, capsys):
    monkeypatch.setattr(sheets.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom"))
    staging = object.__new__(sheets.Staging)
    staging.gws, staging.sid = gws(sheets), "staging-id"
    staging.close()
    assert "delete it from Drive by hand" in capsys.readouterr().err


def test_an_explicit_cwd_is_still_used(sheets, agent, tmp_path):
    """Uploads pass their own folder: gws only uploads a file from its working directory."""
    upload_dir = tmp_path / "upload"
    upload_dir.mkdir()
    sheets.fake_run.stdout = json.dumps({"id": "new"})
    assert gws(sheets).call("drive", "files", "create", upload="staging.xlsx", cwd=str(upload_dir)) == {"id": "new"}
    assert sheets.calls[0]["cwd"] == str(upload_dir)


def test_every_gws_subprocess_goes_through_run_gws():
    """A new raw subprocess.run would bring the stray file back."""
    text = SCRIPT.read_text(encoding="utf-8")
    body = text.split("def run_gws", 1)[1].split("\nclass Gws", 1)[0]
    assert text.count("subprocess.run(") == body.count("subprocess.run(")


def test_gws_connect_runs_its_delete_from_a_scratch_folder_and_says_why():
    text = " ".join(GWS_CONNECT.read_text(encoding="utf-8").split())
    assert "gws drive files delete" in text
    assert "scratch folder" in text and "download.html" in text
