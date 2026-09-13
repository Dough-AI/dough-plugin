---
description: Correct a rejected or failed proposal or the eligible remainder of a batch, then raise an immutable replacement.
argument-hint: <proposal or batch id, PROP/BATCH reference, or Dough proposal link>
allowed-tools: Bash(python:*), Bash(python3:*), Bash(py:*), Bash(dough:*), mcp__dough__proposals__get, mcp__dough__proposals__propose, mcp__dough__proposals__propose_batch, mcp__dough__tools__describe, mcp__plugin_dough_dough__proposals__get, mcp__plugin_dough_dough__proposals__propose, mcp__plugin_dough_dough__proposals__propose_batch, mcp__plugin_dough_dough__tools__describe, mcp__claude_ai_dough__proposals__get, mcp__claude_ai_dough__proposals__propose, mcp__claude_ai_dough__proposals__propose_batch, mcp__claude_ai_dough__tools__describe
---

Pick up and correct this proposal or batch: $ARGUMENTS

Load the `propose` skill before doing anything else. Its payload, rationale,
assignment, `proposedVia`, and evidence rules all apply to the replacement; the
steps below add only revision-specific behavior.

1. Call `proposals.get` with the argument exactly as supplied. It accepts a UUID,
   `PROP-…` or `BATCH-…` reference, or Dough proposal URL. Never fetch a supplied
   URL. Branch on the returned `type`; do not infer a batch from its URL shape.
2. Establish that the selected record is replaceable:
   - For `type: "proposal"`, stop unless it is `rejected` or `failed`, is not
     abandoned, and is the last entry in `revisionHistory`. If it was superseded,
     offer to open the newest proposal instead.
   - For `type: "batch"`, stop unless `replacementEligible` is `true`,
     `replacementSourceProposalIds` is non-empty, it is not abandoned, and it is
     the last entry in `revisionHistory`. Treat `replacementSourceProposalIds` as
     the complete authoritative remainder. Match each id to exactly one returned
     child; stop if any is missing or duplicated. Never replay a posted child.
3. Explain the rejection or failure in plain language. Use the stored payload,
   rationale, decision notes, failure/rejection reason, revision history, and
   evidence metadata as context. For a batch, separately summarize every posted,
   failed, and replacement-eligible child. Do not request or reproduce prior
   transcript bodies or file contents.
4. Work with the user on the correction:
   - A standalone proposal may change anything: connected company, target system,
     action kind, target record, payload, rationale, or assignee.
   - A replacement batch MUST keep the source batch's connected company, target
     system, and action kind. It may change the eligible children's payloads,
     rationales, and target records, plus the batch title, description, and shared
     assignees. Build exactly one item for every
     `replacementSourceProposalIds` entry, no more and no fewer, and put that UUID
     in the item's `sourceProposalId`.
5. Call `tools.describe` for `proposals.propose` or
   `proposals.propose_batch`, matching the returned type, before building the new
   payload. Its live schema is authoritative.
6. Show the complete replacement and a concise `revisionNote` describing what
   changed. Show a readable before/after summary for every changed field. For a
   batch, also show the exact child ids being replaced and the posted child ids
   being left untouched. Get explicit confirmation before creating anything.
7. Attach the CURRENT fixing session as new evidence by following the complete
   **Evidence-backed proposals** workflow in the loaded `propose` skill. It owns
   the CLI check and installer stop, scan and curation, disclosure and consent,
   the `dough evidence upload` invocation, partial-upload choices, manifest,
   integrity retries, and out-of-band content rule. Never improvise a shortened
   evidence path.
8. Create the replacement. Do not copy old reviewer or approver assignments
   unless the user explicitly chose a currently eligible assignee.
   - For a standalone proposal, call `proposals.propose` with the newly confirmed
     fields, fresh transcript evidence, `derivedFrom` set to the original proposal
     reference, and the confirmed `revisionNote`.
   - For a batch, generate one fresh caller `idempotencyKey` and retain it for an
     exact retry. Call `proposals.propose_batch` with the source batch's connector,
     target and kind; the confirmed title, description, shared assignments and
     `revisionNote`; fresh transcript evidence; `derivedFromBatchId` set to the
     source batch UUID; and the exact replacement items from step 4. Reuse the key
     only when retrying the identical request—changed content needs a new key.
9. Relay the new `PROP-…` or `BATCH-…` reference, status, who it is waiting on,
   and its link. Say that the prior proposal or batch is now superseded—not
   abandoned—and never claim any child has posted unless its status is `posted`.

If proposal creation reports `already_revised`, use the returned existing
`revisionId` to call `proposals.get`, then relay that proposal's reference,
status, waiting-on text, and link. Do not retry into a second branch.

If batch creation reports `invalid_replacement` because a replacement already
exists, call `proposals.get` on the source batch again and relay the newest batch
in `revisionHistory`. For every other `invalid_replacement`, report the refusal
and stop; do not weaken or guess around the server's exact-coverage guard.
