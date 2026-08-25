---
description: Raise a write to a connected accounting system for human approval, with the session and its evidence attached.
argument-hint: [what to propose, e.g. "accrue Sept contractor invoices"]
allowed-tools: Bash(python3:*), Bash(dough:*), mcp__dough__proposals__propose, mcp__dough__proposals__actions, mcp__dough__proposals__evidence__begin, mcp__dough__tools__describe
---

Load the `propose` skill and follow it to build the entry. $ARGUMENTS

Attach the session and its evidence as well. The script lives at
`${CLAUDE_PLUGIN_ROOT}/skills/propose/scripts/collect_evidence.py`.

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

1. **Scan.** `python3 <script> scan` — reads the session transcript and lists
   every file this session touched. Structural, so nothing is missed.

2. **Curate.** Keep only what actually backs this entry. Drop source you happened
   to read, scratchpad files, and anything unrelated to the numbers. Write a
   one-line note for each file you keep saying what it establishes.

   Limits worth knowing while you choose: 25 MB per object, 100 MB per set, 64
   objects. The transcript alone is often 1–2 MB.

3. **Declare.** `python3 <script> declare --files <kept paths>` — hashes each
   file and emits the `objects[]` array plus a `paths` map. Pass `objects`
   straight to `proposals.evidence.begin` with the `sessionId` from the scan.

4. **Confirm.** Show the user what will upload — every kept file with its size
   and your note, the transcript, and anything the server returned in
   `rejected`. Relay each rejection's `message`; don't interpret its `code`.
   This ships their local files and their entire session to a server; they get
   to see that and say no. Wait for a clear yes.

5. **Upload.** Write a plan file combining the server's `uploads` array with the
   `paths` map from step 3:

   ```json
   { "uploads": [ ...from evidence.begin... ], "paths": { ...from declare... } }
   ```

   Then `python3 <script> upload --plan <plan>`.

   Each URL accepts exactly one successful PUT, but a *failed* attempt leaves
   nothing behind, so the script's retries are safe as written.

   If anything lands in `failed`, do not decide for them. Show what failed and
   offer: retry, propose without it, or cancel.

   If they choose to proceed without it, **do not re-declare.** Keep the failed
   object in the evidence set exactly as declared and propose as normal. The
   server records it as `missing` and names it on the proposal, so the approver
   sees the gap. Calling `proposals.evidence.begin` again without that object is
   the one action that would hide it.

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
