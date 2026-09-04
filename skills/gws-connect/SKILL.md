---
name: gws-connect
description: Connect this machine to Google Workspace so you can read and write Sheets, Docs and Drive files. Use when a task needs a Google Sheet or Doc, when `gws` is missing or reports 401/403, or when the user asks to connect Google, Sheets, Docs or Drive.
---

# Connect Google Workspace (`gws`)

Gets the `gws` CLI installed and authorised so this session can read and write
the user's Google Sheets, Docs, and Drive files.

**Run `scripts/triage.py` first.** It tells you which of the four stages you
actually need, and it is read-only — it never installs, writes, or authenticates.

```sh
python3 scripts/triage.py      # Windows: py -3 scripts\triage.py
```

Its last line is one of five verdicts. Do exactly what the verdict says:

| Verdict | Meaning | Do |
|---|---|---|
| `CONNECTED` | working, correct app, sufficient scopes | **Stop.** Report the account and get on with the task. |
| `LOGIN_ONLY` | client config present, token missing or scopes short | Stage 3 only |
| `FOREIGN_CLIENT` | a **third party's** OAuth app owns `~/.config/gws` | **Stop. Ask the user.** See "Never overwrite" below. |
| `BRANCH_A` | the user's org has its own Google Cloud setup | Try `gws auth setup`; on failure fall through to Stage 2 |
| `BRANCH_B` | the common case | Stages 1 → 2 → 3 |

## Never overwrite someone else's config

`gws` hardcodes `~/.config/gws/` and **ignores `XDG_CONFIG_HOME`**, so there is
exactly one client config per machine. You can reuse it or replace it — never
both, and never silently.

- If it belongs to **the user's own organisation** (their Cloud org owns the
  OAuth app), triage says `CONNECTED` — reuse it, write nothing.
- If it belongs to **a third party**, triage says `FOREIGN_CLIENT`. Overwriting
  would break whatever installed it. **Stop and ask the user which they want.**

## Stage 1 — the binary

**The Dough installer provides `gws`.** It is installed as part of setup, but
deliberately **not connected** — connecting is this skill's job, because every
Google consent permanently consumes one of 100 lifetime slots on our OAuth app
and should be spent only by someone who actually wants a Sheet.

So there is nothing to install here. If triage reports no binary, the machine
was set up before gws was added, or the step was skipped. Re-running the
installer is the fix — it is idempotent, and re-running is also how it updates:

**macOS**
```sh
curl -fsSL https://raw.githubusercontent.com/Dough-AI/dough-installer/main/install.sh | sh
```

**Windows**
```powershell
irm https://raw.githubusercontent.com/Dough-AI/dough-installer/main/install.ps1 | iex
```

Then re-run triage. Do **not** download or install `gws` yourself: the installer
verifies the published checksum, proves the binary runs before placing it, and
handles PATH on both platforms. Reproducing that here would be a second,
untested copy of logic that already exists.

In particular, **do not install via `npm`** even if it looks quicker. It works,
but nvm/fnm shims are absent from non-interactive shells, so `gws` then vanishes
on the next tool call.

If `gws` is present but not on the PATH a non-interactive shell sees, use its
absolute path for the rest of the session and say so — do not ask the user to
restart their terminal mid-task.

## Stage 2 — fetch the client config

```sh
curl -fsS -H "authorization: Bearer $DOUGH_TOKEN" \
  "$DOUGH_ORIGIN/api/gws/client-config" -o ~/.config/gws/client_secret.json
mkdir -p ~/.config/gws && chmod 700 ~/.config/gws
chmod 600 ~/.config/gws/client_secret.json
```

Origin defaults to `https://app.usedough.ai`; the token is the one `dough login`
saved. A **401** means `dough login` is needed; a **403** means the org lacks the
SKU — say which, do not retry.

On Windows the same file goes to `%USERPROFILE%\\.config\\gws\\client_secret.json` —
`gws` uses `~/.config/gws` on every platform — and is locked down with
`icacls <path> /inheritance:r /grant:r "$env:USERNAME:F"` instead of `chmod`.

Never print the file. It is not a high-value secret (Google treats installed-app
client secrets as non-confidential) but there is no reason to put it in a
transcript.

## Stage 3 — authorise

**macOS**

```sh
OAUTHLIB_INSECURE_TRANSPORT=1 gws auth login --scopes \
  https://www.googleapis.com/auth/spreadsheets,\
https://www.googleapis.com/auth/documents,\
https://www.googleapis.com/auth/drive.file
```

**Windows** — PowerShell continues lines with a backtick, not a backslash, so
the macOS form above is malformed there. Simplest is one line:

```powershell
$env:OAUTHLIB_INSECURE_TRANSPORT = "1"
gws auth login --scopes "https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/documents,https://www.googleapis.com/auth/drive.file"
```

`OAUTHLIB_INSECURE_TRANSPORT=1` must be set for the same invocation — the
loopback redirect needs it. The CLI binds an OS-assigned ephemeral port, not
8080.

### You must open the URL yourself

**`gws` does NOT open a browser, on any platform.** Its `--help` says "(opens
browser)", but the binary only prints:

```
Open this URL in your browser to authenticate:

  https://accounts.google.com/o/oauth2/auth?...
```

and then blocks on its loopback server. Nothing else happens. If you wait for a
browser that never appears, the command simply hangs until it times out — which
is exactly how this failed on a clean Windows machine.

So capture that URL and open it:

- **macOS:** `open "<url>"`
- **Windows:** `Start-Process "<url>"`

Both use the user's **default** browser — Edge, Chrome, whatever they have set.
Never name a specific browser: a vanilla Windows machine has no Chrome, and
hardcoding one is how this breaks.

**Also print the URL** so the user can paste it if the launch is blocked (a
locked-down desktop, a remote session, an unusual default-handler setup). Do not
ask them to retype it — the scope query string is long and wraps.

### While the tab loads

Tell the user in plain language what they are approving: read and write their
Sheets and Docs, and create files in Drive. Mention they can revoke any time at
https://myaccount.google.com/permissions.

Expect a **"Google hasn't verified this app"** screen → Advanced → Continue.
That is normal and expected; do not treat it as an error.

`gws` also appends `openid`, `userinfo.email` and `userinfo.profile`. Those are
non-sensitive and it needs them to report the account — not a problem.

## Stage 4 — verify, and report

Verify with a **write round-trip**, not a read. A read only proves a token
exists; the capability being sold is writing.

```sh
gws drive files create --json '{"name":"Dough","mimeType":"application/vnd.google-apps.folder"}'
gws drive files create --json '{"name":"connectivity-check","mimeType":"application/vnd.google-apps.spreadsheet","parents":["<folderId>"]}'
gws drive files list   --params '{"q":"'"'"'<folderId>'"'"' in parents"}'
gws drive files delete --params '{"fileId":"<checkFileId>"}'
```

**Judge by exit code, never by parsing stdout** — every `gws` call prints
`Using keyring backend: …` on stderr before any JSON. If you must read a value,
strip to the first `{`.

Keep the `Dough` folder; delete only the check file. If the create succeeds and
the delete fails, **say so** rather than reporting a clean run.

```
✅ Google Workspace ready — connected as <email>. Sheets, Docs and Drive verified.
❌ Google Workspace setup failed: <stage>, exit <code>. <one-line fix hint>.
```

## What this grant can and cannot do

Deliberate, and worth stating plainly when a user asks for something outside it:

- ✅ Read and write **any** Sheet or Doc the user can already open, by URL or ID.
- ✅ Create files and folders; list and search **inside** folders this app created.
- ❌ **Browse or search the user's wider Drive.** `drive.file` is per-file access.
  A file the user has not named and this app did not create is invisible.
- ❌ Write into a folder a human created in the Drive UI — it is not
  app-accessible. Put Dough's output in the `Dough` folder from Stage 4 instead.

If a user asks you to "find" a file, ask them for the URL. Do not report the
grant as broken.

## Rules

- **Idempotent.** Safe to re-run; triage short-circuits.
- **Never lower TLS verification.** A TLS error has a real cause — corporate
  proxy, internal CA, clock skew — and hiding it hides the cause.
- **Stop on an unfamiliar error.** Report the exact stderr rather than
  improvising a workaround.
- **Never echo tokens, credentials, or file contents** to the transcript.
