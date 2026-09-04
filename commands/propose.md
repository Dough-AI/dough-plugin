---
description: Raise a write to a connected accounting system for human approval, with the session and its evidence attached.
argument-hint: [what to propose, e.g. "accrue Sept contractor invoices"]
allowed-tools: Write, Bash(python:*), Bash(python3:*), Bash(py:*), Bash(dough:*), mcp__dough__proposals__propose, mcp__dough__proposals__actions, mcp__dough__proposals__evidence__begin, mcp__dough__tools__describe, mcp__plugin_dough_dough__proposals__propose, mcp__plugin_dough_dough__proposals__actions, mcp__plugin_dough_dough__proposals__evidence__begin, mcp__plugin_dough_dough__tools__describe, mcp__claude_ai_dough__proposals__propose, mcp__claude_ai_dough__proposals__actions, mcp__claude_ai_dough__proposals__evidence__begin, mcp__claude_ai_dough__tools__describe
---

Load the `propose` skill and follow it to build the entry. $ARGUMENTS

Attach the session and its evidence as well. The script lives at
`${CLAUDE_PLUGIN_ROOT}/skills/propose/scripts/collect_evidence.py`.

**How to run the script.** Every `<py> <script>` below means:

- **Windows: `python`.** There is normally no `python3` on Windows. The name is
  an App Execution Alias that prints "Python was not found; run without
  arguments to install from the Microsoft Store" and exits WITHOUT running
  anything — it looks like a missing interpreter but it is a stub, and it is not
  a reason to conclude Python is unavailable. Use `python`.
- **macOS and Linux: `python3`.**

Run it **bare**: `<py> <script> <args>`. Do NOT prefix it with `cd … &&`, and do
NOT pipe it into `head` or anything else. A permission rule matches one command;
a compound line is several, and each part has to be separately permitted or the
whole thing is refused. On a session in auto mode that refusal is a hard stop —
no prompt, no approval, the evidence simply never uploads and the proposal goes
out with the audit trail missing. Keep the invocation to a single command and
read the script's full output rather than truncating it.

0. **Refresh the plugin first.** Run `dough plugin refresh`. What a proposal
   may contain changes with the server, and this file is pinned on disk — so the
   copy you are reading can be older than the catalog you are proposing into.
   The refresh is best-effort and must never block the proposal. If `dough` is
   missing, is too old to know the `plugin` command, or the download fails or is
   slow, note it in one line and go straight to step 1 — do not wait on it, and
   do not let a slow network hold up the proposal.

   If it reports a refresh (`X -> Y`), the new guidance is **not** what you are
   following right now — a refresh never applies to the session that ran it. Do
   not restart mid-flow and do not abandon the entry you are building;
   `tools.describe` in step 1 is live and remains the authority on what is
   proposable either way. Say two things when you report at the end: that **the
   plugin was updated on disk** — you changed something the user did not ask you
   to change — and that it is worth loading before the next proposal:
   `/reload-plugins` in the terminal, or fully quitting and reopening the
   desktop app, where that command does not exist.

1. **Scan.** `<py> <script> scan` — reads the session transcript and lists
   every file this session touched. Structural, so nothing is missed.

2. **Curate.** Keep only what actually backs this entry. Drop source you happened
   to read, scratchpad files, and anything unrelated to the numbers. Write a
   one-line note for each file you keep saying what it establishes.

   Limits worth knowing while you choose: 25 MB per object, 100 MB per set, 64
   objects. The transcript alone is often 1–2 MB.

3. **Confirm.** Show the user what will be attached — every kept file with its
   size and your note, plus the transcript. This ships their local files and
   their entire session to a server; they get to see that and say no. Wait for a
   clear yes.

   The caps are checked by the SERVER, not here, so an oversized object is not
   refused until step 4. Say the sizes you know; do not promise what will be
   accepted.

4. **Attach.** One command does the whole thing:

   ```
   dough evidence upload --session <sessionId from the scan> --file <kept path> --file <kept path>
   ```

   Run it **bare**, like the script — no `cd … &&`, no pipe. It hashes each
   file, freezes the transcript, declares every object to the server, uploads
   them, and prints:

   ```json
   { "evidenceId": "ev_…", "uploaded": [...], "failed": [...], "rejected": [...] }
   ```

   You never see an upload URL, and there is no plan file to write. That is the
   point: those URLs carry a credential, and a shell command that carries one
   toward a remote host is refused outright in auto mode.

   Every object is declared BEFORE any bytes move, so one that fails to upload
   is recorded against the proposal as `missing` and the approver is shown the
   gap. Each URL accepts exactly one successful PUT; a *failed* attempt leaves
   nothing behind, so the retries are safe as written.

   **If `dough` does not know the `evidence` command**, the binary predates it.
   Say so in one line and use the older path instead — it still works:

   - `<py> <script> declare --files <kept paths>` — emits `objects[]` and a
     `paths` map.
   - Pass `objects` to `proposals.evidence.begin` with the `sessionId`.
   - Write `{ "uploads": [ …from evidence.begin… ], "paths": { …from declare… } }`
     to a file **with the Write tool.** Do NOT build it with a shell heredoc
     (`cat > … <<'EOF'`): the plan embeds a signed URL and token, and that
     command is refused by the auto-mode classifier — which is the whole reason
     the `dough evidence upload` path exists.
   - Then `<py> <script> upload --plan <plan>`.

   Either way, tell the user at the end that the CLI is out of date and
   reinstalling it removes this detour.

5. **Relay what did not land.** Before you propose, show the user anything in
   `rejected` or `failed`. Relay each rejection's `message`; don't interpret its
   `code`. Objects over 25 MB, or past the 100 MB total, come back in `rejected`
   and will NOT be attached.

   Do not decide for them. Offer: retry, propose without it, or cancel.

   If they choose to proceed without it, **do not re-run the attach step.** Keep
   the evidence set exactly as declared and propose as normal. The server records
   the absent object as `missing` and names it on the proposal, so the approver
   sees the gap. Attaching again without that object is the one action that would
   hide it.

6. **Propose.** Call `proposals.propose` as the skill directs, with:

   `transcript: { evidenceId, sessionId, manifest }`

   where `manifest` carries one entry per kept file — `key`, `filename`,
   `sha256`, `bytes`, `mime`, `role`, and your `note`.

   **Send the `rationale` as well.** The evidence shows what the numbers came
   from; the rationale says why this write. The queue shows both, and the
   evidence does not stand in for it — least of all on the path below where the
   user asks you to proceed without any. Keep `privateNote` to the short memo
   the descriptor asks for — it is posted into the customer's books.

   Status is derived by the server from what actually reached storage, so do not
   claim it yourself. If the call is refused with `invalid_evidence` the set was
   unknown, expired, or already used — start again from step 3. If it is refused
   with `evidence_integrity`, an object's size disagrees with what was declared;
   re-declare and re-upload rather than retrying the same set.

Never inline file contents or transcript text into the tool call itself. The
whole point of this flow is that the bytes travel out of band; pasting them back
into the payload defeats it and will not fit.

If `proposals.evidence.begin` is not in your tool list, **stop and say so.** Any
org that can call `proposals.propose` can attach evidence — the two ship together
— so its absence means something is out of date, not that this org lacks the
feature. Most likely the plugin is newer than the Dough server it is talking to,
or the MCP connection is stale and needs reconnecting.

Do not quietly propose without the evidence. An unbacked proposal is
indistinguishable from a backed one to whoever approves it, which is the failure
this command exists to prevent. Report what is missing, say what you think is
wrong, and let the user decide — if they ask you to proceed without evidence,
do that.
