"""Tests for skills/gws-connect/scripts/triage.py.

The skill dispatches on the verdict, so a wrong one silently sends it down the
wrong stage: installing over a working setup, or skipping a needed login.

The script is Python rather than shell so it runs on Windows, where bash is not
a given. That also makes these tests hermetic without PATH gymnastics — an
earlier bash version needed a synthesised PATH because CI runners ship the
Google Cloud SDK, leaving `gcloud` reachable no matter how PATH was stripped.
"""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).parent.parent / "skills" / "gws-connect" / "scripts" / "triage.py"
)


def load_triage():
    """Import triage.py as a module so its helpers can be tested directly."""
    spec = importlib.util.spec_from_file_location("triage", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def triage():
    return load_triage()


def run_main(triage, capsys) -> str:
    """Run main() and return the verdict (last line of stdout)."""
    assert triage.main() == 0
    return capsys.readouterr().out.strip().splitlines()[-1].strip()


# --- verdicts -------------------------------------------------------------


def test_branch_b_when_gws_absent(triage, monkeypatch, capsys):
    monkeypatch.setattr(triage, "find_gws", lambda: None)
    monkeypatch.setattr(triage.shutil, "which", lambda _: None)
    assert run_main(triage, capsys) == "BRANCH_B"


def test_branch_a_when_the_user_has_gcp_access(triage, monkeypatch, capsys):
    """No client config, but real GCP access in their own org — worth attempting
    `gws auth setup` before falling back to the Dough app."""
    monkeypatch.setattr(triage, "find_gws", lambda: "/fake/gws")
    monkeypatch.setattr(triage, "gws_json", lambda *_: {"client_config_exists": False})
    monkeypatch.setattr(triage.shutil, "which", lambda name: "/fake/" + name)
    monkeypatch.setattr(triage, "user_cloud_org", lambda: "145261633048")
    monkeypatch.setattr(
        triage, "run", lambda cmd, timeout=30: (0, "someone@example.com\nproj-a\n")
    )
    assert run_main(triage, capsys) == "BRANCH_A"


def test_branch_b_when_gcloud_is_absent(triage, monkeypatch, capsys):
    monkeypatch.setattr(triage, "find_gws", lambda: "/fake/gws")
    monkeypatch.setattr(triage, "gws_json", lambda *_: {"client_config_exists": False})
    monkeypatch.setattr(triage.shutil, "which", lambda _: None)
    assert run_main(triage, capsys) == "BRANCH_B"


def test_foreign_client_when_the_app_belongs_to_a_third_party(
    triage, monkeypatch, capsys
):
    """The case that must never overwrite: a config owned by someone else.

    gws hardcodes one config directory, so writing ours would break whatever
    installed theirs.
    """
    monkeypatch.setattr(triage, "find_gws", lambda: "/fake/gws")
    monkeypatch.setattr(
        triage,
        "gws_json",
        lambda _g, args: (
            {"client_config_exists": True, "has_refresh_token": True, "scopes": []}
            if args[-1] == "status"
            else {"client_id": "999999999999-other.apps.googleusercontent.com"}
        ),
    )
    monkeypatch.setattr(triage, "resolve_owning_org", lambda _: "111111111111")
    monkeypatch.setattr(triage, "user_cloud_org", lambda: "222222222222")
    assert run_main(triage, capsys) == "FOREIGN_CLIENT"


def test_foreign_client_when_ownership_cannot_be_resolved(
    triage, monkeypatch, capsys
):
    """Unresolvable ownership is treated as foreign, deliberately.

    Refusing to touch a config we cannot account for is the safe default; the
    cost is asking the user.
    """
    monkeypatch.setattr(triage, "find_gws", lambda: "/fake/gws")
    monkeypatch.setattr(
        triage,
        "gws_json",
        lambda _g, args: (
            {"client_config_exists": True, "has_refresh_token": True, "scopes": []}
            if args[-1] == "status"
            else {"client_id": "123-x.apps.googleusercontent.com"}
        ),
    )
    monkeypatch.setattr(triage, "resolve_owning_org", lambda _: None)
    monkeypatch.setattr(triage, "user_cloud_org", lambda: None)
    assert run_main(triage, capsys) == "FOREIGN_CLIENT"


def _own_app(triage, monkeypatch, scopes, has_token=True):
    monkeypatch.setattr(triage, "find_gws", lambda: "/fake/gws")
    monkeypatch.setattr(
        triage,
        "gws_json",
        lambda _g, args: (
            {
                "client_config_exists": True,
                "has_refresh_token": has_token,
                "scope_count": len(scopes),
                "scopes": scopes,
            }
            if args[-1] == "status"
            else {"client_id": "123-x.apps.googleusercontent.com"}
        ),
    )
    monkeypatch.setattr(triage, "resolve_owning_org", lambda _: "145261633048")
    monkeypatch.setattr(triage, "user_cloud_org", lambda: "145261633048")


def test_connected_when_the_org_owns_the_app_and_scopes_suffice(
    triage, monkeypatch, capsys
):
    _own_app(
        triage,
        monkeypatch,
        [triage.SCOPE_PREFIX + s for s in ("spreadsheets", "documents", "drive.file")],
    )
    assert run_main(triage, capsys) == "CONNECTED"


def test_connected_when_full_drive_stands_in_for_drive_file(
    triage, monkeypatch, capsys
):
    """Full `drive` is a superset — an org's own Internal app often has it."""
    _own_app(
        triage,
        monkeypatch,
        [triage.SCOPE_PREFIX + s for s in ("spreadsheets", "documents", "drive")],
    )
    assert run_main(triage, capsys) == "CONNECTED"


def test_login_only_when_a_scope_is_missing(triage, monkeypatch, capsys):
    """A scope shortfall means re-login, NOT falling back to a new app."""
    _own_app(
        triage,
        monkeypatch,
        [triage.SCOPE_PREFIX + s for s in ("spreadsheets", "drive.file")],
    )
    assert run_main(triage, capsys) == "LOGIN_ONLY"


def test_login_only_when_there_is_no_token(triage, monkeypatch, capsys):
    _own_app(triage, monkeypatch, [], has_token=False)
    assert run_main(triage, capsys) == "LOGIN_ONLY"


# --- helpers --------------------------------------------------------------


def test_project_number_is_the_client_id_prefix(triage):
    """This is what makes ownership computable at all."""
    assert (
        triage.project_number_of("487272055567-hv36.apps.googleusercontent.com")
        == "487272055567"
    )


def test_gws_json_strips_the_keyring_banner(triage, monkeypatch):
    """Every gws call prints a banner before any JSON. Parsing the whole stream
    is how this breaks."""
    monkeypatch.setattr(
        triage,
        "run",
        lambda *_a, **_k: (0, 'Using keyring backend: keyring\n{"account":"a@b.c"}'),
    )
    assert triage.gws_json("/fake/gws", ["auth", "status"]) == {"account": "a@b.c"}


def test_gws_json_returns_none_on_garbage(triage, monkeypatch):
    monkeypatch.setattr(triage, "run", lambda *_a, **_k: (1, "command not found"))
    assert triage.gws_json("/fake/gws", ["auth", "status"]) is None


def test_missing_scopes_reports_only_what_is_short(triage):
    granted = [triage.SCOPE_PREFIX + "spreadsheets"]
    assert triage.missing_scopes(granted) == ["documents", "drive.file"]
