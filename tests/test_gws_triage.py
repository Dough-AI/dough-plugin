"""Tests for skills/gws-connect/scripts/triage.sh.

The skill dispatches on triage's verdict, so a wrong one silently sends it down
the wrong stage: installing over a working setup, or skipping a needed login.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

TRIAGE = Path(__file__).parent.parent / "skills" / "gws-connect" / "scripts" / "triage.sh"

# Coreutils triage.sh shells out to. Anything absent from here must be
# unreachable during the test.
NEEDED = ("bash", "python3", "sed", "awk", "head", "tr", "wc")

CLIENT_ID = "487272055567-x.apps.googleusercontent.com"


@pytest.fixture
def hermetic_path(tmp_path):
    """A PATH holding ONLY the coreutils above — deliberately no gws, no gcloud.

    Stripping PATH to /usr/bin:/bin is not enough. CI runners commonly ship the
    Google Cloud SDK on the system path, so `gcloud` stays reachable, runs, and
    creates ~/.config/gcloud — which made an earlier version of this test pass
    on a laptop and fail in CI. Symlinking exactly what is needed makes the
    environment identical everywhere.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for cmd in NEEDED:
        found = shutil.which(cmd)
        if found:
            (bin_dir / cmd).symlink_to(found)
    return str(bin_dir)


def run_triage(home: Path, path: str) -> str:
    """Run triage and return its verdict (the last line of stdout)."""
    result = subprocess.run(
        ["bash", str(TRIAGE)],
        env={
            "HOME": str(home),
            "PATH": path,
            # Belt and braces: if a gcloud ever does become reachable, keep its
            # first-run config out of the HOME these tests assert on.
            "CLOUDSDK_CONFIG": str(home / ".gcloud-scratch"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"triage exited {result.returncode}: {result.stderr}"
    return result.stdout.strip().splitlines()[-1].strip()


def write_client_config(home: Path, config: dict) -> None:
    cfg_dir = home / ".config" / "gws"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "client_secret.json").write_text(json.dumps(config), encoding="utf-8")


def test_branch_b_when_nothing_installed(tmp_path, hermetic_path):
    home = tmp_path / "home"
    home.mkdir()
    assert run_triage(home, hermetic_path) == "BRANCH_B"


def test_branch_b_when_gws_absent_even_with_a_client_config(tmp_path, hermetic_path):
    # Without the binary there is no `auth status`, so ownership can never be
    # established and a config file on disk proves nothing.
    home = tmp_path / "home"
    home.mkdir()
    write_client_config(home, {"installed": {"client_id": CLIENT_ID}})
    assert run_triage(home, hermetic_path) == "BRANCH_B"


def test_never_creates_or_modifies_gws_state(tmp_path, hermetic_path):
    """Triage runs before the user has agreed to anything.

    The invariant is that it never installs, authenticates, or touches
    ~/.config/gws — NOT that it writes nothing at all. Merely probing `gcloud`
    makes gcloud initialise its own config dir, which is gcloud's side effect on
    first use, not triage's.
    """
    home = tmp_path / "home"
    home.mkdir()
    before = sorted(os.listdir(home))
    run_triage(home, hermetic_path)
    assert not (home / ".config" / "gws").exists()
    after = sorted(e for e in os.listdir(home) if not e.startswith(".gcloud"))
    assert after == before
