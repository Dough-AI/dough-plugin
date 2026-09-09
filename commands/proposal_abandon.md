---
description: Close a rejected or failed proposal that will not be replaced.
argument-hint: <proposal id, PROP reference, or Dough proposal link> <reason>
allowed-tools: mcp__dough__proposals__get, mcp__dough__proposals__abandon, mcp__plugin_dough_dough__proposals__get, mcp__plugin_dough_dough__proposals__abandon, mcp__claude_ai_dough__proposals__get, mcp__claude_ai_dough__proposals__abandon
---

Abandon the proposal identified in: $ARGUMENTS

1. Separate the proposal identifier from the required reason. If the reason is
   missing or ambiguous, ask for it rather than inventing one.
2. Call `proposals.get` with the identifier exactly as supplied. Never fetch a
   supplied URL.
3. Stop unless it is rejected or failed, not already abandoned, and the newest
   entry in its revision history. A superseded proposal cannot be abandoned.
4. Show the proposal reference, its existing outcome, and the abandonment reason.
   Explain that abandonment is irreversible, preserves the rejection/failure,
   and prevents a future replacement from being derived from this proposal.
5. Ask for explicit confirmation, then call `proposals.abandon` with the same
   identifier and confirmed reason.
6. Relay the recorded actor-independent result: proposal reference, unchanged
   rejected/failed status, abandonment reason, and timestamp.

Only the latest proposal's proposer or an organization admin can do this. Never
substitute a different proposal if the server refuses authorization.
