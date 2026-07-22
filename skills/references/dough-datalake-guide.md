# Dough Datalake Guide

Good practice and hard-won gotchas for working the Dough datalake. Exact tool
input schemas are **not** duplicated here — call `tools.list` for the catalog (or
`tools.describe` for a single tool's schema) for the current, org-scoped schema. This
guide holds the behaviors and judgment the schema can't tell you.

## Good practice

### The datalake is read-only to you; writes go through named operations
- `integrations.query` runs **read-only** SQL only: a single `SELECT` or
  `WITH … SELECT`. It is wrapped as `SELECT * FROM (<your sql>) AS _q LIMIT n`.
  There is no `INSERT`/`UPDATE`/`DELETE`/DDL path — and that's deliberate.
- The only ways to create durable data are the named write operations:
  `mappings.save` (enrich a table) and `tables.create` (materialize a SELECT).
  Both cross the same gated, audited door the web UI uses.

### Read the org's conventions before writing new SQL
- Call `queries.list` / `queries.get` first. Saved queries are how the org
  already computes its metrics — reuse their logic instead of reinventing it.
- You can absolutely write your **own custom SQL** with `integrations.query`;
  saved queries are a starting point and a source of truth for definitions, not
  a fence.

### Verify before you promote
- Prove an analysis with `integrations.query` (bounded, cheap) **before**
  materializing it with `tables.create` or saving it with `queries.save`. A
  calculated table is a background job over the full dataset; don't spend that
  until the SELECT is right.

### When to use which write
- **Mapping** (`mappings.save`): you want to add derived columns by mapping the
  distinct values of one column (a dimension) to new output columns
  (e.g. `billing_country → region`). Materializes `<dataset>_mapped.<table>`.
- **Calculated table** (`tables.create`): you want to persist the result of a
  join/aggregate SELECT as its own table in `dough_calculated`.
- **Saved query** (`queries.save`): you want to persist SQL (not its result) to
  the org library so people can re-run it in the app.

### "Not set up" is a state, not an error
- Reads like `integrations.sources` report readiness in a **normal payload**, not by
  throwing. `status: "not_provisioned"` means the org holds the datalake SKU but has
  no provisioned tenant yet — a provisioning step (operator-side), not something to
  retry. `status: "ready"` with `needsSetup: true` means the tenant exists but no
  integration is connected yet. Neither is an error to work around.

## Gotchas (the ones that bite)

The exact caps and inputs live in each tool's own description — call `tools.describe`
for those. These are the behaviors that surprise people:

1. **`mappings.save` treats the CSV as complete state, and it's destructive.** The
   CSV you save *is* the mapping: a blank cell unmaps, and removing a row **deletes**
   that assignment. Always `dryRun:true` first to see the diff before applying.
2. **A mapping's identity `(dataset, table, dimension)` is immutable, and there is
   no delete tool.** Deleting a mapping is a deliberate action in the Dough app —
   you can't do it from here. So you can't "rename" a dimension via the tools; edit
   its values with `mappings.save`, or remove the mapping in the app.
3. **Applied mappings and `tables.create` are asynchronous.** `mappings.save` with
   `status:"applied"` schedules a rebuild; `tables.create` returns a `jobId`
   immediately. Poll `mappings.status` (pass the **source** table) or `tables.status`
   — the data isn't there the instant the call returns. A `<dataset>_mapped` table
   only shadows its raw source in `integrations.tables` once that rebuild has
   **succeeded**, not on a draft save.
