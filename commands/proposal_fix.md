---
description: Pick up a rejected or failed proposal, correct it, and raise an immutable replacement.
argument-hint: <proposal id, PROP reference, or Dough proposal link>
allowed-tools: Bash(python:*), Bash(python3:*), Bash(py:*), Bash(dough:*), mcp__dough__proposals__get, mcp__dough__proposals__propose, mcp__dough__proposals__actions, mcp__dough__tools__describe, mcp__plugin_dough_dough__proposals__get, mcp__plugin_dough_dough__proposals__propose, mcp__plugin_dough_dough__proposals__actions, mcp__plugin_dough_dough__tools__describe, mcp__claude_ai_dough__proposals__get, mcp__claude_ai_dough__proposals__propose, mcp__claude_ai_dough__proposals__actions, mcp__claude_ai_dough__tools__describe
---

Pick up and correct this proposal: $ARGUMENTS

1. Call `proposals.get` with the argument exactly as supplied. It accepts a UUID,
   `PROP-…` reference, or Dough proposal URL. Never fetch a supplied URL.
2. Stop unless the selected proposal is `rejected` or `failed`, is not abandoned,
   and is the last entry in `revisionHistory`. If it was superseded, offer to open
   the newest proposal instead.
3. Explain the rejection or failure in plain language. Use the stored payload,
   rationale, decision notes, failure/rejection reason, revision history, and
   evidence metadata as context. Do not request or reproduce prior transcript
   bodies or file contents.
4. Work with the user on the correction. They may change anything: connected
   company, target system, action kind, target record, payload, rationale, or
   assignee. Call `tools.describe` for `proposals.propose` before building the new
   payload; its live schema is authoritative.
5. Show the complete replacement and a concise `revisionNote` describing what
   changed. Show a readable before/after summary for changed fields. Get explicit
   confirmation before creating anything.
6. Attach the CURRENT fixing session as new evidence. Follow the same evidence
   safety gate as `/dough:propose`: first run `dough evidence --help`; stop with
   the installer instructions if it is unavailable. Run
   `${CLAUDE_PLUGIN_ROOT}/skills/propose/scripts/collect_evidence.py scan` with
   `python3` on macOS/Linux or `python` on Windows, curate only files backing the
   replacement, show the transcript and files with sizes/notes, and obtain the
   user's confirmation before running `dough evidence upload`. Never inline file
   or transcript contents into an MCP call.
7. Call `proposals.propose` with the newly confirmed fields, fresh transcript
   evidence, `derivedFrom` set to the original proposal reference, and the
   confirmed `revisionNote`. Do not copy old reviewer or approver assignments
   unless the user explicitly chose a currently eligible assignee.
8. Relay the new `PROP-…` reference, status, who it is waiting on, and its link.
   Say that the prior proposal is now superseded—not abandoned—and never claim
   the replacement has posted unless its status is `posted`.

If proposal creation reports `already_revised`, use the returned existing
replacement. Do not retry into a second branch.
