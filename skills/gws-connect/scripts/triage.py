#!/usr/bin/env python3
"""Decide which gws connection path this machine needs. macOS and Windows.

Read-only: never installs, authenticates, or writes to ~/.config/gws. Prints
one verdict on the last line.

    CONNECTED       working, acceptable app, sufficient scopes -- stop
    LOGIN_ONLY      client config present; token missing or scopes short
    FOREIGN_CLIENT  a THIRD PARTY's OAuth app owns ~/.config/gws -- stop, ask
    BRANCH_A        the user's org has its own Google Cloud setup -- try `gws auth setup`
    BRANCH_B        the common case -- install, fetch config, log in

Python rather than shell because this has to run on Windows too, where bash is
not a given. Google exposes no API for listing OAuth clients, so BRANCH_A is a
hypothesis to attempt, never a fact to detect.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Scopes the skill needs. Full `drive` is accepted in place of `drive.file`.
NEEDED_SCOPES = ("spreadsheets", "documents", "drive.file")
SCOPE_PREFIX = "https://www.googleapis.com/auth/"
DRIVE_FULL = SCOPE_PREFIX + "drive"

# Set when the Dough-owned client is known, so it is recognised as ours rather
# than as a third party's.
DOUGH_CLIENT_ID_ENV = "DOUGH_GWS_CLIENT_ID"


def say(label: str, value: str) -> None:
    print(f"  {label:<34}{value}")


def run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    """Run a command, returning (exit code, stdout). Never raises."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return proc.returncode, proc.stdout
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def find_gws() -> str | None:
    """Locate the gws binary.

    `shutil.which` covers PATH on both platforms (and PATHEXT resolves `gws.exe`
    on Windows). It is also checked at the install locations the skill uses,
    because a binary may be present while absent from the PATH a non-interactive
    shell inherits.
    """
    found = shutil.which("gws")
    if found:
        return found
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / "gws",
        home / "AppData" / "Local" / "Programs" / "gws" / "gws.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def gws_json(gws: str, args: list[str]) -> dict | None:
    """Parse JSON from a gws command.

    Every gws invocation prints `Using keyring backend: ...` before any JSON, so
    the body is taken from the first brace onward rather than parsed whole.
    """
    _, out = run([gws, *args])
    start = out.find("{")
    if start < 0:
        return None
    try:
        parsed = json.loads(out[start:])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def project_number_of(client_id: str) -> str:
    """A client_id's numeric prefix IS its owning GCP project number."""
    return client_id.split("-", 1)[0]


def resolve_owning_org(project_number: str) -> str | None:
    """Walk project -> parent folders -> root organization id, or None."""
    if not project_number or not shutil.which("gcloud"):
        return None
    code, out = run(
        ["gcloud", "projects", "describe", project_number,
         "--format=value(parent.type,parent.id)"]
    )
    if code != 0 or not out.strip():
        return None
    parts = out.split()
    if len(parts) < 2:
        return None
    node_type, node_id = parts[0], parts[1]
    for _ in range(6):
        if node_type in ("organization", "organizations"):
            return node_id
        if node_type not in ("folder", "folders"):
            return None
        code, out = run(
            ["gcloud", "resource-manager", "folders", "describe", node_id,
             "--format=value(parent)"]
        )
        if code != 0 or "/" not in out:
            return None
        node_type, _, node_id = out.strip().partition("/")
    return None


def user_cloud_org() -> str | None:
    if not shutil.which("gcloud"):
        return None
    code, out = run(["gcloud", "organizations", "list", "--format=value(name)"])
    if code != 0:
        return None
    lines = [line for line in out.splitlines() if line.strip()]
    return lines[0].strip() if lines else None


def missing_scopes(granted: list[str]) -> list[str]:
    have = set(granted)
    short = []
    for name in NEEDED_SCOPES:
        if SCOPE_PREFIX + name in have:
            continue
        if name == "drive.file" and DRIVE_FULL in have:
            continue
        short.append(name)
    return short


def verdict(name: str) -> int:
    print()
    print(name)
    return 0


def main() -> int:
    print("gws connection triage")
    print()

    gws = find_gws()
    if not gws:
        say("gws binary", "absent")
        say("platform", sys.platform)
        return verdict("BRANCH_B")
    say("gws binary", gws)

    status = gws_json(gws, ["auth", "status"]) or {}
    has_client = bool(status.get("client_config_exists"))
    has_token = bool(status.get("has_refresh_token"))
    say("client_secret.json", "present" if has_client else "absent")
    say("refresh token", f"{has_token} ({status.get('scope_count', 0)} scopes)")

    if not has_client:
        return verdict("BRANCH_A" if branch_a_possible() else "BRANCH_B")

    # WHOSE app is this? gws hardcodes one config directory, so we reuse it or
    # replace it -- never both, and never silently. The question is not "is this
    # client id ours" but "is this app in the user's OWN Cloud org": if it is,
    # reuse is legitimate (their app, their employee) and writes nothing.
    #
    # `auth status` ABBREVIATES client_id; only `auth export` carries the full
    # value, and the naive check fails silently on the abbreviated one.
    exported = gws_json(gws, ["auth", "export"]) or {}
    client_id = str(exported.get("client_id") or "")
    app_org = resolve_owning_org(project_number_of(client_id))
    own_org = user_cloud_org()
    say("app owning org", app_org or "<unresolved>")
    say("user cloud org", own_org or "<none>")

    is_dough_app = bool(
        client_id and client_id == os.environ.get(DOUGH_CLIENT_ID_ENV, "").strip()
    )
    if is_dough_app:
        say("verdict", "the Dough app - reuse")
    elif app_org and own_org and app_org == own_org:
        say("verdict", "app belongs to the user's own org - reuse")
    else:
        say("verdict", "THIRD PARTY or unresolvable - do not overwrite")
        return verdict("FOREIGN_CLIENT")

    if not has_token:
        return verdict("LOGIN_ONLY")

    short = missing_scopes([str(s) for s in status.get("scopes", [])])
    if short:
        say("scopes", "INSUFFICIENT - missing: " + ",".join(short))
        return verdict("LOGIN_ONLY")
    say("scopes", "sufficient for sheets + docs + drive")
    return verdict("CONNECTED")


def branch_a_possible() -> bool:
    """Real GCP access in the user's own Cloud org makes `gws auth setup` worth
    attempting. Capability, not proof -- nothing can detect an existing client."""
    if not shutil.which("gcloud"):
        say("gcloud", "absent")
        return False
    code, out = run(["gcloud", "auth", "list", "--filter=status:ACTIVE",
                     "--format=value(account)"])
    account = out.strip().splitlines()[0].strip() if (code == 0 and out.strip()) else ""
    org = user_cloud_org()
    code, out = run(["gcloud", "projects", "list", "--format=value(projectId)"])
    projects = len([line for line in out.splitlines() if line.strip()]) if code == 0 else 0
    say("gcloud", "present")
    say("authenticated as", account or "<none>")
    say("projects visible", str(projects))
    say("cloud organization", org or "<none>")
    return bool(account and org and projects)


if __name__ == "__main__":
    sys.exit(main())
