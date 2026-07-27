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
  presentation), `excel` (Dough-managed Excel workbooks: a visible `Dough`
  manifest sheet maps data sheets to saved queries, so Claude can refresh them
  without re-deriving anything — includes a bundled openpyxl script for
  deterministic workbook writes), and `propose` (raise a write to a connected
  accounting system for human approval instead of performing it).
- **Command:** `/dough:status`.

## Updating

Publishing a change and refreshing clients (CLI first, then the desktop app) is
documented in **[UPDATING.md](./UPDATING.md)**. Note: the desktop app is
version-gated and has no refresh button, so every skill change must bump the
version in `plugin.json` **and** `marketplace.json` — see the runbook.
