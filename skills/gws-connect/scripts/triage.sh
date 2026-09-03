#!/usr/bin/env bash
# gws-triage.sh — decide which gws connection path this client can use.
#
# Read-only. Never writes, never authenticates, never installs.
# Prints exactly one verdict on the last line:
#
#   CONNECTED   already working — nothing to do
#   LOGIN_ONLY  client_secret.json present, just needs `gws auth login`
#   BRANCH_A    has GCP access in their own Cloud org — try `gws auth setup`
#   BRANCH_B    no GCP path — use the Dough-owned OAuth app
#
# Detection note: Google exposes NO API for listing OAuth clients, so "does an
# OAuth client already exist" is undetectable. This probes CAPABILITY instead,
# and Branch A is confirmed by attempting `gws auth setup`, not by a heuristic.

set -uo pipefail

say() { printf '  %-34s %s\n' "$1" "$2"; }

# gws prints "Using keyring backend: ..." on stderr before any JSON, so parse
# from the first brace and never trust a naive read of stdout.
gws_status_json() {
  command -v gws >/dev/null 2>&1 || return 1
  gws auth status 2>/dev/null | sed -n '/{/,$p'
}

json_field() {  # $1 = json, $2 = key
  printf '%s' "$1" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
v = d.get(sys.argv[1])
print("" if v is None else v)
' "$2" 2>/dev/null
}

echo "gws connection triage"
echo

# ---- 1. Is gws installed, and does it work in a NON-INTERACTIVE shell? -------
# The non-interactive check is the one that matters: Claude Code's Bash tool
# never sources ~/.zshrc, so a binary on an interactive-only PATH is invisible.
if bash -c 'command -v gws' >/dev/null 2>&1; then
  say "gws binary" "present ($(gws --version 2>/dev/null | head -1))"
  HAVE_GWS=1
else
  say "gws binary" "absent (or not on non-interactive PATH)"
  HAVE_GWS=0
fi

# ---- 2. Already connected? --------------------------------------------------
STATUS="$(gws_status_json || true)"
HAVE_CLIENT=0
if [ -n "$STATUS" ]; then
  [ "$(json_field "$STATUS" client_config_exists)" = "True" ] && HAVE_CLIENT=1
  HAS_REFRESH="$(json_field "$STATUS" has_refresh_token)"
  SCOPES="$(json_field "$STATUS" scope_count)"
  say "client_secret.json" "$([ "$HAVE_CLIENT" = 1 ] && echo present || echo absent)"
  say "refresh token" "${HAS_REFRESH:-unknown} (${SCOPES:-0} scopes)"

  # WHOSE app is this? gws hardcodes ~/.config/gws, so there is exactly one
  # client config per machine: we reuse it or overwrite it, never coexist.
  #
  # The question is NOT "is this client id ours" but "is this app in the
  # client's OWN Cloud org" -- if it is, reuse is legitimate (it is their app,
  # their employee) and writes nothing. Verified on a Groupon machine, where
  # the installed app resolved to prj-grp-foundryai-dev-7c37 under
  # groupon.com's own org, NOT to the vendor that provisioned it.
  #
  # A client_id's numeric prefix is the owning GCP project number, so this is
  # computable. Note `auth status` ABBREVIATES client_id -- the full value is
  # only in `auth export`.
  if [ "$HAVE_CLIENT" = 1 ]; then
    FULL_ID="$(gws auth export 2>/dev/null | sed -n '/{/,$p' \
      | python3 -c 'import sys,json;print(json.load(sys.stdin).get("client_id",""))' 2>/dev/null)"
    PNUM="${FULL_ID%%-*}"
    APP_ORG=""
    if [ -n "$PNUM" ] && command -v gcloud >/dev/null 2>&1; then
      # Walk project -> parent folders -> root organization.
      NODE="$(gcloud projects describe "$PNUM" --format='value(parent.type,parent.id)' 2>/dev/null)"
      TYPE="$(echo "$NODE" | awk '{print $1}')"; ID="$(echo "$NODE" | awk '{print $2}')"
      for _ in 1 2 3 4 5; do
        [ "$TYPE" = "organization" ] && { APP_ORG="$ID"; break; }
        [ "$TYPE" = "folder" ] || break
        NEXT="$(gcloud resource-manager folders describe "$ID" --format='value(parent)' 2>/dev/null)"
        TYPE="${NEXT%%/*}"; ID="${NEXT##*/}"
        [ "$TYPE" = "organizations" ] && { APP_ORG="$ID"; break; }
        TYPE="folder"
      done
    fi
    USER_ORG="$(gcloud organizations list --format='value(name)' 2>/dev/null | head -1)"
    say "app owning org" "${APP_ORG:-<unresolved>}"
    say "user cloud org" "${USER_ORG:-<none>}"
    if [ -n "$APP_ORG" ] && [ "$APP_ORG" = "$USER_ORG" ]; then
      say "verdict" "app belongs to the client's own org — reuse"
    elif [ -n "${DOUGH_GWS_CLIENT_ID:-}" ] && [ "$FULL_ID" = "$DOUGH_GWS_CLIENT_ID" ]; then
      say "verdict" "the Dough app — reuse"
    else
      say "verdict" "THIRD PARTY or unresolvable — do not overwrite"
      echo; echo "FOREIGN_CLIENT"; exit 0
    fi
  fi

  # Second gate: ownership says reuse is LEGITIMATE; scopes say it is SUFFICIENT.
  # Judged by what the grant carries, never by what the project is called -- a
  # project name predicts nothing. A shortfall means re-login with --scopes,
  # not a fallback to Branch B.
  #
  # Note this is a fast DIAGNOSIS, not proof: a scope can be granted while the
  # API is disabled on the project. The Stage 3 probes are the proof.
  if [ "$HAVE_CLIENT" = 1 ] && [ "$HAS_REFRESH" = "True" ]; then
    MISSING="$(printf '%s' "$STATUS" | python3 -c '
import sys, json
NEED = ["spreadsheets", "documents", "drive.file"]  # Chat uses chat_webhook, not gws
have = set(json.load(sys.stdin).get("scopes", []))
short = [n for n in NEED
         if "https://www.googleapis.com/auth/" + n not in have
         and not (n == "drive.file"
                  and "https://www.googleapis.com/auth/drive" in have)]
print(",".join(short))' 2>/dev/null)"
    if [ -n "$MISSING" ]; then
      say "scopes" "INSUFFICIENT — missing: $MISSING"
      echo; echo "LOGIN_ONLY"; exit 0
    fi
    say "scopes" "sufficient for sheets + docs + drive"
    echo; echo "CONNECTED"; exit 0
  fi
fi

# ---- 3. Branch A capability: real GCP access in their OWN Cloud org ---------
GCLOUD=0; ACCOUNT=""; NPROJ=0; ORG=""
if command -v gcloud >/dev/null 2>&1; then
  GCLOUD=1
  ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -1)"
  NPROJ="$(gcloud projects list --format='value(projectId)' 2>/dev/null | wc -l | tr -d ' ')"
  ORG="$(gcloud organizations list --format='value(displayName)' 2>/dev/null | head -1)"
fi
say "gcloud" "$([ "$GCLOUD" = 1 ] && echo present || echo absent)"
say "authenticated as" "${ACCOUNT:-<none>}"
say "projects visible" "${NPROJ:-0}"
say "cloud organization" "${ORG:-<none>}"

echo
if [ "$HAVE_CLIENT" = 1 ]; then
  echo "LOGIN_ONLY"
elif [ "$GCLOUD" = 1 ] && [ -n "$ACCOUNT" ] && [ -n "$ORG" ] && [ "${NPROJ:-0}" -gt 0 ]; then
  echo "BRANCH_A"
else
  echo "BRANCH_B"
fi
