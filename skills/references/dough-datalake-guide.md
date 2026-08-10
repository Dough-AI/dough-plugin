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
  `tables.update` (replace an existing calculated table's SELECT), `tables.upload`
  (load a CSV as a table), and `tables.annotate` (record what the team knows about
  a table). All cross the same gated, audited door the web UI uses.

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
  has to come from a CSV — a budget, a plan, an allocation key. Use this rather
  than encoding the numbers as a giant
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
   - On an append, columns are matched **by name** (order is irrelevant) and the
     types you declare are ignored in favour of the table's own. Money keeps full
     precision (`NUMERIC` holds 38 digits).
5. **Every upload has to say where the data came from, and what identifies a row.**
   `sourceLabel` is **required** — the origin in words a person would recognise
   ("FY26 budget v3, from Finance"). It is recorded against the upload and stamped
   on every row it writes, which is what lets someone reading a number six months
   later find the file behind it; "upload" or "data" throws that away as surely as
   an empty field would. `sourceUrl` is optional and is stored and displayed but
   never fetched — treat it as a claim about origin, not a verified fact.
   `keyColumns` (e.g. `["period","cost_center"]`) is **required when `mode` is
   `create`** and is inherited by later uploads. Dough matches rows between versions
   on it, so a corrected value reads as a change to one row instead of a deletion
   and an unrelated insert. Duplicate keys are rejected and a key column may never
   be empty. You may **add** columns to the key on any upload; **removing** one is
   allowed only during a `replace`, because a narrower key can collide with rows
   already in the table. Widening the key onto a column the same upload is
   introducing is likewise a `replace`, since every row already stored would have
   no value for it.
6. **An append does NOT have to bring exactly the table's columns — but it may not
   add and omit in the same upload.** A brand-new column is added, and existing rows
   simply have no value for it. A non-key column you leave out is allowed too, and
   is empty for the rows you appended; either way the response names what changed.
   Omitting a **key** column is always rejected. And doing both at once — bringing a
   new column *and* dropping one — is rejected naming both sides, because that is
   precisely what a typo produces: `regoin` for `region` is one new column plus one
   missing one, and nothing distinguishes it from a deliberate drop-and-add. Dough
   refuses rather than guess which you meant, so **retrying the identical payload
   fails identically** — the fix is either to correct the header and resend, or, if
   the rename is real, to send two uploads: the first carrying both the old and the
   new column, the second dropping the old one.
7. **Several files that belong in one table need one decision made before the
   first upload, not after the first rejection.** A planning week produces one
   extract per scenario; a monthly close produces one per month. They arrive as
   separate files and belong in a single table.
   - **Union the headers first, and create with all of them.** The files will not
     agree — one scenario modelled headcount, another added a `risk_note` column
     later. A file that omits columns the table has is fine; a file that both adds
     one and omits one is rejected (item 6). Creating from the union means no
     later file ever has to add anything, so that case never arises.
   - **Expect the files to collide on the natural key.** Three scenarios of one
     planning week carry the same periods and the same cost centres — that is what
     makes them comparable, and it means every row of the second file duplicates a
     row of the first under `(period, cost_center, account)`. The upload is
     rejected. They need a discriminator column, and the important part is that it
     is usually **not in the data at all**: "upside" is the name of a tab, not a
     value in any column. It has to be synthesised from each file's identity and
     added to `keyColumns`. `_dough_source` does not serve here — it records origin
     per row, but Dough stamps it and it is not a column you can declare in a key.
   - **Matching headers are not matching meaning.** Whether these files really are
     one table is the person's judgement; ask before merging them.
   - **One file per `tables.upload` call**, each with its own `sourceLabel` and
     `sourceUrl`. Concatenating them locally would work and is the wrong move: every
     row would then carry the same origin, and per-row provenance exists precisely so
     that "which extract is this number from" survives the merge.
   - **And one at a time: let each load finish before sending the next file to
     that table.** Poll `tables.status` with `kind:"uploaded"` until it reports the
     table ready; a load takes a few seconds. Sent early, the next append is refused
     as `upload_in_flight`, because its keys are checked against the rows already
     stored and those rows have not landed yet — Dough refuses rather than accept
     rows whose keys nothing checked, which is the same principle as item 6.
     Nothing is recorded and nothing is loaded, so the fix is to wait and **send
     the identical payload again**. It is the only refusal in this whole section
     that a retry resolves: item 6's fails identically however many times it is
     sent, and reading either one as the other loses a file or loops on it.

8. **`tables.status` defaults to `kind:"calculated"`.** Polling an upload needs
   `kind:"uploaded"`; without it the tool looks at the wrong set of tables and
   reports yours as missing.
9. **Applied mappings, `tables.create`, `tables.update` and `tables.upload` are
   asynchronous.** `mappings.save` with `status:"applied"` schedules a rebuild;
   the three `tables.*` writes return a `jobId` immediately. Poll `mappings.status`
   (pass the **source** table) or `tables.status`
   — the data isn't there the instant the call returns. A `<dataset>_mapped` table
   only shadows its raw source in `integrations.tables` once that rebuild has
   **succeeded**, not on a draft save.
10. **A failed rebuild leaves the table queryable with STALE rows.** This is the
   `tables.update` case to watch, and it is why failure here is not "nothing
   happened": `tables.status` reports `state: "recreate_failed"`, the table still
   exists and still answers queries, but its rows came from the **previous** build,
   not from the query now stored. Never present those numbers as the new result —
   report that the rebuild failed. `state` is the field to switch on (`building`,
   `ready`, `recreate_failed`, `failed`, `gone`) and it never contradicts the rest
   of the payload, so trust it over inferring from the other fields.
