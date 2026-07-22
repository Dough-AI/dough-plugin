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

- **MCP connector** to the Dough datalake tools (`integrations`, `queries`, `mappings`, `tables`).
- **Skills:** `getting-started` and `datalake`.
- **Command:** `/dough:status`.
