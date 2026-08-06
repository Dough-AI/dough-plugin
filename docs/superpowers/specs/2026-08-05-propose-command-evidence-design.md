# `/dough:propose` with attached evidence

**Date:** 2026-08-05
**Repos:** `dough-plugin` (command, bundler script), `Dough-Alpha` (evidence channel, storage, viewer)

## Problem

`proposals.propose` is reachable today only when the `propose` skill's description
happens to fire. There is no deterministic way for a user to say "raise this for
approval". A slash command gives them one.

Separately, a proposal is only as good as the evidence behind it. An approver
looking at a five-figure journal entry wants the invoices and the derivation, not
a one-line summary. Today `proposals.propose` accepts a free-form `transcript`
object and nothing else — no attachments, no files.

## What gets attached

Three things, decided with the user:

1. **The files that back the entry** — whatever the user uploaded or pointed at
   during the session. Full bytes, verbatim.
2. **The full session transcript** — the Claude Code JSONL, raw and uncurated.
3. **The reasoning trail** — which lives *inside* the raw transcript rather than
   being separately synthesized.

The transcript is deliberately **not** curated. `proposals.propose` already has a
`rationale` field whose entire job is the human-readable why. Summarizing into
`transcript` as well would duplicate it and invite the two to disagree. Curated
summary in `rationale`, unedited record in `transcript`.

## The constraint that shapes everything

Tool call arguments are model output. Every byte placed in `transcript` is
generated token by token by the model. That makes inline evidence impossible at
realistic sizes:

| Evidence | Size | Output tokens required |
| - | - | - |
| A modest session's JSONL | 187 KB | ~55k |
| One 884 KB scanned PDF, base64 | 1.18 MB | ~390k |

Single-turn output budgets are tens of thousands of tokens, not hundreds of
thousands. Worse, model-emitted base64 is not trustworthy — one dropped character
corrupts the file and nothing in the path checksums it. For an artifact whose
purpose is to be provably what it claims, that is disqualifying.

`tables.upload` shows Dough has already met this wall from the other side: it
accepts CSV inline at a 5 MB cap, a limit no model-generated argument will reach.

**Therefore: bytes must travel over a channel the model is not in.**

## What already exists

Exploration of `Dough-Alpha` found most of this built:

- **A proven out-of-band byte channel.** `POST /api/registry/session-content`
  takes raw JSONL as the request body with `Bearer` auth and
  `x-dough-session-id` / `x-dough-content-offset` headers. The Python uploader at
  `src/features/registry/observability.ts:392` keeps a per-session byte-offset
  cursor and ships only new bytes; the server dedupes by record uuid. Cap is
  `MAX_CONTENT_BYTES = 25 MB`. Bytes go disk → HTTP → storage.
- **A schema already shaped for it.** `proposal_transcripts` carries both
  `content` and `objectPath` under a check constraint that exactly one is set
  (`src/features/proposals/repository.ts:393`). Nothing writes `objectPath` yet.
  The approval UI already branches on it and renders the placeholder
  `"Stored in object storage — no inline viewer yet."`
  (`src/app/action-gateway/proposals/[id]/page.tsx:370`).
- **Credential-free presigned uploads.** `createSignedUploadUrl`
  (`src/features/files/storage.ts:216`) returns
  `{ method: "PUT", url, requiredHeaders }` — usable by a plain
  `curl --upload-file` with no token of its own.
- **Text extraction.** `src/features/files/text-extract.ts` and
  `spreadsheets.ts` already pull text out of PDFs and workbooks.
- **Plugins can ship hooks and scripts.** `hooks/hooks.json` at the plugin root.

## Architecture

The division of labor is the core idea: **the model decides *which* files are
evidence; a script moves the *bytes*; the tool call carries only *references*.**

```
/dough:propose  ──▶  commands/propose.md
                       │
   ┌───────────────────┴─────────────────────────────┐
   │ 1. load skills/propose, build the entry          │  model
   │ 2. scan + curate the evidence list               │  model
   └───────────────────┬─────────────────────────────┘
                       ▼
   proposals.beginEvidence  { sessionId, objects[] }    ← OAuth'd MCP call, ~150 tok
        └──▶ presigned PUT per object
        ◀── { evidenceId, expiresAt, uploads[], rejected[] }
                       ▼
   ┌───────────────────┴─────────────────────────────┐
   │ 3. confirmation gate — shows rejections too      │  user
   └───────────────────┬─────────────────────────────┘
                       ▼
   scripts/collect_evidence.py --upload                  ← bash, NO model
        • curl --upload-file → storage, one PUT per object
        ◀── receipt: { uploaded[], failed[] }
                       ▼
   proposals.propose  { …, transcript: { evidenceId,
                        sessionId, manifest } }          ← ~200 tok
                       ▼
   server: verify → write proposal_transcripts.objectPath
                  + proposal_evidence rows
```

### Why `beginEvidence` mints presigned URLs

OAuth already works for MCP; nothing else in the plugin holds credentials. Minting
a short-lived presigned URL through an authenticated tool call keeps the bundler
credential-free and stateless — no separate login, no config file, no token
refresh in bash. It also gives the server a place to enforce caps and entitlement
*before* any bytes move.

### One PUT per object, not a tarball

Each file stays individually addressable and hashable, and server-side extraction
can pull searchable text on ingest. A tarball would hide all of that.

### Capture is on-demand only

Nothing leaves the machine until someone types `/dough:propose`. No ambient
session upload, no background hook. The command *is* the consent.

## Finding the evidence

Relying on model recall to list every file touched is the weak link — an evidence
set with silent gaps is exactly what an audit trail cannot have. The session JSONL
records them structurally, so the scan is deterministic:

```
collect_evidence.py --scan          ← no model, no network
  parses ~/.claude/projects/<slug>/<session>.jsonl
    • user message attachments        → paths
    • Read / Write / Edit tool calls  → file_path
    • Bash cwd-relative file args     → resolved paths
    • bare paths in user prose        → regex, existence-checked
  emits candidates.json: path, bytes, mime, mtime, first-seen turn
```

The model then curates `candidates.json` down to what genuinely backs the entry,
dropping plugin source it happened to read, scratchpad files, and anything under
`.git/`. The `note` on each kept file is the model's one-line justification and
travels into the manifest. After `beginEvidence` returns, the user sees the result
— including anything the server rejected — before a byte moves:

```
Evidence for this proposal — 3 files, 1.1 MB

  ✓ ~/close/sept/contractors.csv      4.2 KB   sourced the $8,400.50
  ✓ ~/close/sept/acme-inv.pdf         864 KB   invoice #4471
  ✓ session transcript                187 KB   verbatim, full session
  ✗ ~/code/dough-plugin/SKILL.md               (dropped: read, not evidence)
  ✗ ~/close/sept/scans.zip             31 MB   (rejected: over 25 MB cap)

Upload and propose?  [y / edit / cancel]
```

The gate is load-bearing. This command ships a user's local files and their entire
session — including anything that session touched — to a server. Making that
visible and refusable at the moment it happens is the honest design.

## The `beginEvidence` contract

This is what unblocks both repos; both build against it in parallel.

**Request.** Everything hashed locally before any byte moves:

```jsonc
{
  "sessionId": "440239c9-fca1-433c-968f-bfe670ed634d",
  "objects": [
    { "key": "transcript",  "role": "transcript",
      "filename": "session.jsonl", "mime": "application/x-ndjson",
      "bytes": 187829, "sha256": "a3f9…" },
    { "key": "f0", "role": "file",
      "filename": "contractors.csv", "mime": "text/csv",
      "bytes": 4210, "sha256": "7c21…" }
  ]
}
```

**Response.** A presigned PUT per object, plus explicit rejections:

```jsonc
{
  "evidenceId": "ev_01JQ…",
  "expiresAt": "2026-08-05T16:45:00Z",
  "uploads": [
    { "key": "transcript", "method": "PUT", "url": "https://…", "headers": {} },
    { "key": "f0", "method": "PUT", "url": "https://…", "headers": {} }
  ],
  "rejected": [
    { "key": "f2", "code": "over_object_cap",
      "message": "31 MB exceeds the 25 MB limit" }
  ]
}
```

Rejections arrive *before* upload, which is why `beginEvidence` is called ahead of
the confirmation gate rather than after it: the list the user approves is then
already truthful about what will actually land. Minting URLs the user may never
use is the cost, and the orphan TTL below is what pays it.

**Consumption.** `proposals.propose` gains no new top-level field; the reference
rides in the existing `transcript` object:

```jsonc
"transcript": {
  "evidenceId": "ev_01JQ…",
  "sessionId": "440239c9-…",
  "manifest": [
    { "key": "f0", "filename": "contractors.csv", "sha256": "7c21…",
      "bytes": 4210, "mime": "text/csv", "role": "file",
      "note": "sourced the $8,400.50" }
  ]
}
```

### Rules the two-phase flow forces

- **Single-use, org-bound.** An `evidenceId` is consumable exactly once, by the
  org that minted it. Otherwise it is a handle for attaching one org's session to
  another org's proposal.
- **Strict integrity.** On consume, the server streams each uploaded object and
  verifies `sha256` against what the client declared. Mismatch refuses the
  proposal. A transcript that silently truncated mid-upload is worse than none,
  because it looks complete to whoever reads it later. Cost is one streaming read
  per object, on a path already measured in human-approval hours.
- **The transcript must be frozen before it is hashed.** It is a live file the
  session is still appending to — the user answering the confirmation gate writes
  into the very bytes just hashed. An end-to-end run measured it growing 17,473
  bytes between declare and upload, which under strict verification refuses the
  proposal every time. The client snapshots it and uploads the frozen copy. This
  is also the more truthful artifact: evidence should be the session as it stood
  when the proposal was made, not one grown to include the approval conversation
  that followed.
- **Orphan TTL: 1 hour.** `beginEvidence` mints storage objects; a user who
  cancels at the confirmation gate leaves them unconsumed. Unconsumed evidence
  sets expire after 1 hour and a sweep collects them. One hour comfortably
  exceeds a propose flow and matches the presigned-URL lifetime.
- **Per-object cap: 25 MB**, following the `MAX_CONTENT_BYTES` precedent.

## Data model

- `proposal_transcripts` — unchanged shape. Finally gets an `objectPath` writer
  for the session JSONL. The one-of(`content`, `objectPath`) constraint stands.
- `proposal_evidence` — **new**. One row per file: `proposalId`, `key`,
  `filename`, `mime`, `bytes`, `sha256`, `storageObjectPath`, `role`,
  `extractedText`, `note`. Separate from `proposal_transcripts` because that
  table is shaped for a single blob. `extractedText` comes free from the existing
  extractors and is what makes evidence searchable later.
- `proposal_evidence_sets` — **new**. Pending sets minted by `beginEvidence`:
  `evidenceId`, `organizationId`, `userId`, `sessionId`, `expiresAt`,
  `consumedAt`, declared object manifest.
- `proposals.evidenceStatus` — **new column**. `complete` when every declared
  object landed and verified, `partial` when the user chose to proceed without
  one, `none` for proposals raised without evidence at all. The approval UI reads
  it to warn an approver that they are looking at an incomplete record.

## Failure handling

The bundler retries failed objects with exponential backoff. If objects still
fail, it does **not** decide — it surfaces the state and the user chooses:

```
✗ Evidence upload incomplete after 3 attempts.

  uploaded  session.jsonl      187 KB
  uploaded  contractors.csv    4.2 KB
  FAILED    acme-inv.pdf       864 KB  └─ connection reset

  [retry / propose without it / cancel]
```

"Propose without it" sets `evidenceStatus: partial` on the proposal, recording
exactly what is missing so the approver sees the gap. Partial is therefore always
a state a human chose, never one that happened silently.

Refusals from `proposals.propose` itself are unchanged and already documented in
`skills/propose/SKILL.md`.

## The command file

Thin. It orchestrates; the skill carries the accounting judgment.

```markdown
---
description: Raise a write to a connected accounting system for human
             approval, with the session and its evidence attached.
argument-hint: [what to propose, e.g. "accrue Sept contractor invoices"]
allowed-tools: Bash(python3:*), mcp__dough__proposals__*, mcp__dough__tools__describe
---

Load skills/propose and follow it. $ARGUMENTS

Additionally, before calling proposals.propose:
1. Run scripts/collect_evidence.py --scan
2. Curate candidates to what genuinely backs this entry, with a note on each
3. proposals.beginEvidence with the curated list
4. Show the user what will upload, including anything rejected; get confirmation
5. collect_evidence.py --upload
6. Attach { evidenceId, sessionId, manifest } as transcript
```

`allowed-tools` is scoped so the command cannot wander. `$ARGUMENTS` lets
`/dough:propose accrue September contractors` seed intent in one line; with no
argument it falls through to the skill's normal questioning.

## Viewer

Replace the placeholder at `page.tsx:370` with a real viewer: the transcript
streamed from object storage behind the existing admin-only permission gate, and
the evidence list with per-file name, size, hash, and a download link. The
permission gate stays exactly as narrow as it is today — a transcript can contain
anything the session touched.

## Testing

Deterministic at every seam; only one end-to-end needs live storage.

- **Scanner** — fixture JSONLs covering attachments, Read/Write/Edit calls,
  compacted sessions, and paths that no longer exist, asserted against expected
  candidate sets.
- **`beginEvidence`** — over-cap rejection, unknown mime, org binding, expiry
  stamping.
- **Consume path** — hash mismatch refuses; replayed `evidenceId` refuses;
  expired set refuses; happy path writes `objectPath` plus evidence rows.
- **Bundler** — fake presign server exercising retry, backoff, and the
  partial-failure prompt.
- **Sweep** — unconsumed sets past TTL collected; consumed sets untouched.
- **End-to-end** — one test through real storage: scan → begin → upload →
  propose → read back.

## Sequencing

1. Freeze the `beginEvidence` contract above. Both repos build against it.
2. `Dough-Alpha`: tables, `beginEvidence`, consume-path verification, sweep.
3. `dough-plugin`: `collect_evidence.py`, `commands/propose.md`.
4. `Dough-Alpha`: viewer.

`/dough:propose` ships when 2 and 3 are done. There is deliberately no
manifest-only interim release — full fidelity or nothing.

## Explicitly out of scope

- Ambient or continuous session capture.
- Evidence on anything other than `proposals.propose`.
- Web sources and Dough query lineage as first-class evidence types — both are
  already inside the raw transcript.
- Retrofitting evidence onto proposals raised before this exists.
