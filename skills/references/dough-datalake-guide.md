# Dough Datalake Guide

Good practice and hard-won gotchas for working the Dough datalake. Exact tool
input schemas are **not** duplicated here — call `tools.describe` (or
`tools.describe` for a single tool) for the current, org-scoped schema. This
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
- If a read returns *"Data Lake is not set up for this organization,"* the org
  holds the datalake SKU but has no provisioned tenant yet. That is a
  provisioning step (operator-side), not something to retry or work around.

## Gotchas (learned the hard way)

1. **`mappings.save` treats the CSV as complete state.** The CSV you save *is*
   the mapping: a blank cell means unmapped, and removing a row **deletes** that
   assignment. Always `dryRun:true` first to see the diff before applying.
2. **A mapping's identity `(dataset, table, dimension)` is immutable.** To
   "rename" a dimension you delete and recreate.
3. **Dimensions cap at 1000 distinct values.** `mappings.dimension.values`
   tells you whether a column is `mappable`; above 1000 it isn't.
4. **`applied` mappings and `tables.create` are asynchronous.** `mappings.save`
   with `status:"applied"` schedules a BigQuery rebuild; `tables.create` returns
   a `jobId` immediately. Poll `mappings.status` (pass the **source** table) or
   `tables.status` — the data isn't there the instant the call returns.
5. **`integrations.query` caps.** `limit` defaults to 500, max 5000;
   `timeoutMs` defaults to 5000ms, max 15000ms; a 20 GiB max-bytes-billed
   ceiling fails runaway scans instead of billing them. Params are `@name`
   placeholders, scalars only.
6. **Calculated tables take a bare name, no `dataset.`** They always land in
   `dough_calculated`, cap at 100 GB billed / 30-min, and have no update/delete —
   recreate to change one.
7. **Mapped tables shadow their raw source.** In `integrations.tables`, once
   `<dataset>_mapped.<table>` exists it stands in for the raw table; query the
   mapped one to get the derived columns.
8. **`queries.save` never executes.** It validates the SQL is read-only and
   stores it; running it is a separate `integrations.query` / app action.
