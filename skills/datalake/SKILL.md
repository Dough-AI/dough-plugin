---
name: datalake
description: Use when analyzing data in the Dough data lake — exploring tables, writing read-only SQL (your own or the org's saved queries), enriching data with dimension mappings, or materializing a result into a calculated table.
---

# Working the Dough data lake

The full analysis arc. Keep queries read-only and cheap while exploring; use the
named write operations only once a result is proven. For exact tool inputs call
`tools.describe`; for behaviors and gotchas read
`../references/dough-datalake-guide.md`.

## 1. Explore what's there
- `integrations.sources` — connected source systems and freshness.
- `integrations.tables` — lake tables grouped by dataset. Note: a raw table is
  shadowed by its `<dataset>_mapped` counterpart once that mapping is applied and
  its rebuild has succeeded (not on a draft save).
- `integrations.describe` — a table's columns. `integrations.preview` — a small
  sample of rows.

## 2. Analyze
- **Read the org's conventions first:** `queries.list` / `queries.get` show how
  the org already computes its metrics. Reuse those definitions.
- **Write your own SQL too.** `integrations.query` runs read-only SQL — a single
  `SELECT` or `WITH … SELECT` — so you can freely explore, join, and aggregate.
  It is not limited to saved queries. Respect the guardrails: `limit` ≤ 5000,
  `timeoutMs` ≤ 15000, a 20 GiB billed ceiling, `@name` scalar params. Iterate
  here until the result is right **before** you persist anything.

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
- **Materialize a table:** once a SELECT is proven, `tables.create` (bare
  `name`, no `dataset.`) writes it to `dough_calculated` as a background job;
  poll `tables.status`. No update/delete — recreate to change it.
- **Save the SQL:** `queries.save` stores the SQL (create with `name`+`sql`, or
  update by `id`) so the org can re-run it. It validates read-only and does not
  execute.
