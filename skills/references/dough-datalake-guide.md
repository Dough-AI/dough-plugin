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
  `mappings.save` (enrich a table), `tables.create` (materialize a SELECT),
  `tables.upload` (load a CSV as a table), and `tables.annotate` (record what the
  team knows about a table). All cross the same gated, audited door the web UI uses.

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

### Aliases name a table; they don't address it
- An alias is what the team **calls** a table ("deals" → `salesforce_mapped.opportunity`).
  It exists so you can resolve a human name to the right table and so people can
  find it in the app. It is **not** a queryable identifier: SQL always uses the
  real `dataset.table`, and there is no rewriting behind your back.
- An alias is unique per org **and** can never be the name of a real table — both
  are enforced when it's saved. So an alias match is unambiguous by construction:
  there is no precedence rule to apply, because the collision can't exist.
- Aliases are shaped like table names (letters, numbers, underscores; no spaces
  or dots), so `deals` and `open_pipeline` are valid, `open pipeline` is not.

### Notes are the org's word, and they outrank your inference
- `integrations.describe` returns a table's `notes` whole. They exist precisely to
  say the things a schema can't: which rows are junk, what a column really holds,
  which of two similar tables is authoritative. Follow them.
- `integrations.tables` deliberately does **not** inline notes — it returns
  `hasNotes` instead. A truncated caveat ("exclude rows where…") is worse than no
  caveat, so the full text only ever comes from `describe`. Call it before you
  query a table flagged `hasNotes`.
- Column-level knowledge lives in the table's notes too; there is no per-column
  field. Read the whole note, not a keyword match within it.

### When to use which write
- **Mapping** (`mappings.save`): you want to add derived columns by mapping the
  distinct values of one column (a dimension) to new output columns
  (e.g. `billing_country → region`). Materializes `<dataset>_mapped.<table>`.
- **Calculated table** (`tables.create`): you want to persist the result of a
  join/aggregate SELECT as its own table in `dough_calculated`. `tables.update`
  replaces that query later.
- **Uploaded table** (`tables.upload`): the data is not in any connected system and
  has to come from a CSV — a budget, a plan, an allocation key. Lands in
  `dough_uploaded`. Use this rather than encoding the numbers as a giant
  `SELECT … UNION ALL` literal: an uploaded table is a real table people can see,
  append to, map and join.
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
3. **`tables.annotate` replaces a field, it doesn't append to it.** Omit a field to
   leave it alone; pass `aliases` and you overwrite the whole list (read the
   current values with `integrations.describe` first if you mean to add one).
   `aliases: []` / `notes: null` clear. An alias that another table already uses,
   or that is the name of a real table, is **rejected** — not merged, not
   silently dropped. Reading the whole result back tells you what stuck.
4. **`tables.upload` declares types, and checks values against them.** Anything you
   leave out of `columnTypes` is `STRING`; types are never inferred from the data
   (a column that looks numeric for 5,000 rows and then holds `N/A` would break a
   later append). Values are validated BEFORE the load, so a bad cell comes back
   with its row and column named. Two consequences worth knowing:
   - **An empty cell is NULL**, not an empty string.
   - **An append must have exactly the same columns.** Order does not matter — they
     are matched by name — and the types you declare are ignored in favour of the
     table's own. A missing or extra column is **rejected**, not null-filled, and the
     error names which. Money keeps full precision (`NUMERIC` holds 38 digits).
5. **`tables.status` defaults to `dough_calculated`.** Polling an upload needs
   `dataset:"dough_uploaded"`; without it the tool looks in the wrong dataset and
   reports the table as missing.
6. **Applied mappings, `tables.create` and `tables.upload` are asynchronous.**
   `mappings.save` with `status:"applied"` schedules a rebuild; `tables.create` and
   `tables.upload` return a `jobId` immediately. Poll `mappings.status` (pass the
   **source** table) or `tables.status`
   — the data isn't there the instant the call returns. A `<dataset>_mapped` table
   only shadows its raw source in `integrations.tables` once that rebuild has
   **succeeded**, not on a draft save.
