---
name: datalake
description: Use when analyzing data in the Dough data lake — exploring tables, writing read-only SQL (your own or the org's saved queries), enriching data with dimension mappings, or materializing a result into a calculated table.
---

# Working the Dough data lake

The full analysis arc. Keep queries read-only and cheap while exploring; use the
named write operations only once a result is proven. For exact tool inputs call
`tools.describe`; for behaviors and gotchas read
`../references/dough-datalake-guide.md`.

## Working rules
Apply these throughout — before and during the mechanics below.

### Reuse the org's existing work first
Before writing SQL against raw tables, look for an answer the org has already
built — in this order:
1. **Calculated tables** — the `dough_calculated.*` entries in `integrations.tables`.
   Materialized, vetted results; if one already answers the question, query it
   directly with `integrations.query`.
2. **Saved queries** — `queries.list` / `queries.get`: the org's vetted SQL for how
   it computes its metrics (which table is authoritative, how revenue is filtered).
   Reuse the definition and run it with `integrations.query`.
3. **Mapped tables, then base tables** — only when neither of the above fits.
   Explore (below) and write your own read-only SQL.

Reusing calculated tables and saved queries keeps your answer consistent with how
the org actually computes things, instead of re-deriving it (and getting it subtly
wrong).

### When the source is ambiguous, ask — don't guess
If more than one table, saved query, calculated table, column, or mapping could
plausibly answer the question and the choice would change the numbers, ask a short
clarifying question instead of picking one — and name the specific options you
found so the user can choose. Examples:
- Several P&L tables (which entity, period, or basis?).
- Several amount or mapping columns you could aggregate in a P&L table (which
  measure — gross vs net? which mapping — audit vs board?).
A confident single match needs no question; a coin-flip between options that would
give different answers always does.

### Show results as a table first
Default to presenting analysis as a plain Markdown table (or a short text summary).
Do **not** create an HTML page or artifact for results unless the user asks for
one. If a result might genuinely warrant a chart, dashboard, or downloadable
report, offer it and ask first ("want this as an HTML report?") rather than
kicking off that flow automatically.

## 1. Explore the tables
- `integrations.sources` — connected source systems and freshness.
- `integrations.tables` — tables grouped by dataset, including the
  `dough_calculated` calculated tables. A raw table is shadowed by its
  `<dataset>_mapped` counterpart once that mapping is applied and its rebuild has
  succeeded (not on a draft save) — prefer the `_mapped` one.
- `integrations.describe` — a table's columns. `integrations.preview` — a small
  sample of rows.

## 2. Analyze with your own SQL
When no calculated table or saved query fits, `integrations.query` runs read-only
SQL — a single `SELECT` or `WITH … SELECT` — so you can explore, join, and
aggregate across mapped and base tables. Respect the guardrails: `limit` ≤ 5000,
`timeoutMs` ≤ 15000, a 20 GiB billed ceiling, `@name` scalar params. Iterate here
until the result is right **before** you persist anything.

## 3. Enrich with a mapping (optional)
When you need derived columns from one column's values (e.g. `billing_country →
region`):
1. `mappings.tables` — pick a source table.
2. `mappings.dimension.values` — confirm the column is `mappable` (≤ 1000 values).
3. `mappings.get` — get the CSV of every dimension value (blank for a new one).
4. Edit the CSV. Remember: the CSV is the **complete state** — blanks unmap,
   deletions are destructive.
5. `mappings.save` with `dryRun:true` — review the diff.
6. `mappings.save` with `status:"applied"` — schedules the rebuild of
   `<dataset>_mapped.<table>`.
7. Poll `mappings.status` (pass the **source** table) until it succeeds. Then
   query the `_mapped` table for the derived columns.

## 4. Persist a result
Choose by how people will use it:
- **A table to build on → `tables.create`.** Materialize a proven SELECT (bare
  `name`, no `dataset.`) into `dough_calculated` as a background job; poll
  `tables.status`. No update/delete — recreate to change it.
- **A query to re-run → `queries.save`.** Store the SQL (create with `name`+`sql`,
  or update by `id`); validates read-only and does not execute.
- These become the calculated tables and saved queries that the **reuse-first**
  step at the top should find next time — so name them clearly.
