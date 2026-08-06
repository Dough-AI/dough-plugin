---
description: Raise a write to a connected accounting system for human approval, with the session and its evidence attached.
argument-hint: [what to propose, e.g. "accrue Sept contractor invoices"]
allowed-tools: Bash(python3:*), mcp__dough__proposals__propose, mcp__dough__proposals__actions, mcp__dough__proposals__beginEvidence, mcp__dough__tools__describe
---

Load the `propose` skill and follow it to build the entry. $ARGUMENTS

Attach the session and its evidence as well. The script lives at
`${CLAUDE_PLUGIN_ROOT}/skills/propose/scripts/collect_evidence.py`.

1. **Scan.** `python3 <script> scan` — reads the session transcript and lists
   every file this session touched. Structural, so nothing is missed.

2. **Curate.** Keep only what actually backs this entry. Drop source you happened
   to read, scratchpad files, and anything unrelated to the numbers. Write a
   one-line note for each file you keep saying what it establishes.

3. **Declare.** `python3 <script> declare --files <kept paths>` — hashes each
   file and emits the `objects[]` array plus a `paths` map. Pass `objects`
   straight to `proposals.beginEvidence` with the `sessionId` from the scan.

4. **Confirm.** Show the user what will upload — every kept file with its size
   and your note, the transcript, and anything `beginEvidence` returned in
   `rejected`. This ships their local files and their entire session to a
   server; they get to see that and say no. Wait for a clear yes.

5. **Upload.** Write a plan file combining `beginEvidence`'s `uploads` array with
   the `paths` map from step 3:

   ```json
   { "uploads": [ ...from beginEvidence... ], "paths": { ...from declare... } }
   ```

   Then `python3 <script> upload --plan <plan>`.

   If anything lands in `failed`, do not decide for them. Show what failed and
   offer: retry, propose without it, or cancel. Only if they choose to proceed
   without it, set `evidenceStatus: "partial"` in the transcript object.

6. **Propose.** Call `proposals.propose` as the skill directs, with:

   `transcript: { evidenceId, sessionId, manifest }`

   where `manifest` carries one entry per kept file — `key`, `filename`,
   `sha256`, `bytes`, `mime`, `role`, and your `note`.

Never inline file contents or transcript text into the tool call itself. The
whole point of this flow is that the bytes travel out of band; pasting them back
into the payload defeats it and will not fit.

If `proposals.beginEvidence` is not in your tool list, this org is not on a build
that supports evidence yet. Say so, and follow the `propose` skill without the
attachment steps rather than failing.
