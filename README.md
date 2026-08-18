# Dough plugin for Claude Code

Datalake analysis with Dough, from your own Claude Code session.

## Install

```
/plugin marketplace add Dough-AI/dough-plugin
/plugin install dough@dough-plugins
```

On first use, Claude Code will prompt you to sign in to Dough (OAuth). Then run
`/dough:status` to confirm your connection and what your org can do.

## What's inside

- **MCP connector** to the Dough tools (`integrations`, `queries`, `mappings`,
  `tables`, `proposals`).
- **Skills:** `getting-started`, `datalake`, `pnl` (consistent income-statement
  presentation), `dashboard` (build a page of widgets over the org's saved
  queries, at a URL anyone in the org can open), `uploads` (shape a
  human-formatted spreadsheet into an uploaded table and prove the parse ties
  out), `excel` (Dough-managed Excel workbooks: a visible `Dough`
  manifest sheet maps data sheets to saved queries, so Claude can refresh them
  without re-deriving anything — includes a bundled openpyxl script for
  deterministic workbook writes), and `propose` (raise a write to a connected
  accounting system for human approval instead of performing it).
- **Commands:** `/dough:status`, and `/dough:propose` — raises a write for
  approval with the session transcript and the files behind it attached, so an
  approver can see what the numbers came from. Evidence is hashed locally and
  uploaded straight to storage; only a reference travels in the tool call.

## Updating

Publishing a change and refreshing clients (CLI first, then the desktop app) is
documented in **[UPDATING.md](./UPDATING.md)**. Note: the desktop app is
version-gated and has no refresh button, so every skill change must bump the
version in `plugin.json` **and** `marketplace.json` — see the runbook.
