---
name: gws-connect
description: Connect this machine to Google Workspace so you can read and write Sheets, Docs and Drive files. Use when a task needs a Google Sheet or Doc, when `gws` is missing or reports 401/403, or when the user asks to connect Google, Sheets, Docs or Drive.
---

# Connect Google Workspace (`gws`)

Gets the `gws` CLI installed and authorised so this session can read and write
the user's Google Sheets, Docs, and Drive files.

**Run `scripts/triage.sh` first.** It tells you which of the four stages you
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

## Stage 1 — install the binary

**Supported: macOS and Windows.** Skip this stage if triage already found `gws`.

Show the command before running it. Releases live at
https://github.com/googleworkspace/cli/releases/latest — about 6MB, and every
asset ships a `.sha256` beside it. **Verify the checksum before running the
binary.**

### macOS

Fast path, if brew is present:

```sh
brew install googleworkspace-cli
```

Otherwise download the asset for the architecture — `aarch64-apple-darwin` for
Apple Silicon, `x86_64-apple-darwin` for Intel — and unpack it:

```sh
mkdir -p ~/.local/bin
curl -fsSL -o /tmp/gws.tar.gz "<asset url>"
curl -fsSL -o /tmp/gws.sha256 "<asset url>.sha256"
shasum -a 256 -c /tmp/gws.sha256      # must print OK
tar -xzf /tmp/gws.tar.gz -C ~/.local/bin
chmod +x ~/.local/bin/gws
```

### Windows

Asset: `google-workspace-cli-x86_64-pc-windows-msvc.zip`.

```powershell
$dir = "$env:LOCALAPPDATA\Programs\gws"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Invoke-WebRequest -Uri "<asset url>" -OutFile "$env:TEMP\gws.zip"
Invoke-WebRequest -Uri "<asset url>.sha256" -OutFile "$env:TEMP\gws.sha256"
# compare against the .sha256 contents before continuing
(Get-FileHash "$env:TEMP\gws.zip" -Algorithm SHA256).Hash
Expand-Archive -Path "$env:TEMP\gws.zip" -DestinationPath $dir -Force
```

Add `$dir` to the **user** PATH (no admin needed):

```powershell
[Environment]::SetEnvironmentVariable(
  "PATH", "$([Environment]::GetEnvironmentVariable('PATH','User'));$dir", "User")
```

A new PATH entry is not visible to already-running processes — if `gws` is not
found immediately afterwards, **invoke it by absolute path** rather than asking
the user to restart their terminal mid-task.

### Do not install via npm

It works, but nvm/fnm shims are absent from non-interactive shells, so `gws`
then vanishes on the next tool call. The release binary has no such problem.

### Verify

Run `gws --version` **in a fresh non-interactive shell** (`bash -c 'gws --version'`
on macOS). If that fails while an interactive shell succeeds, the install
directory is not on the non-interactive `PATH` — use the absolute path for the
rest of the session and say so.

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

```sh
OAUTHLIB_INSECURE_TRANSPORT=1 gws auth login --scopes \
  https://www.googleapis.com/auth/spreadsheets,\
https://www.googleapis.com/auth/documents,\
https://www.googleapis.com/auth/drive.file
```

`OAUTHLIB_INSECURE_TRANSPORT=1` must be in the **same** invocation — the loopback
redirect needs it. The CLI binds an OS-assigned ephemeral port, not 8080.

Before opening the browser, tell the user in plain language what they are about
to approve: read and write their Sheets and Docs, and create files in Drive.
Mention that they can revoke any time at https://myaccount.google.com/permissions.

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
