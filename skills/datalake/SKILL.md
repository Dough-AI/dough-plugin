---
name: datalake
description: Use when analyzing data in the Dough data lake or answering a question about the org's numbers — revenue, margins, metrics, KPIs, "how much did we make", any figure that comes from the data. Also use when loading data that has no integration behind it (a budget, a plan, an allocation key) from a CSV. Covers reusing the org's calculated tables and saved queries first, exploring tables, writing read-only SQL, enriching data with dimension mappings, uploading a CSV as a table, and materializing a result into a calculated table.
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

### The org's own words for a table beat your reading of its schema
Tables carry two things the org wrote itself, and both come back from the same
tools you already call:
- **Aliases** (`aliases` in `integrations.tables`) — the names the team actually
  uses. When someone says "deals" or "the billings table", look for a matching
  alias and use **that** table; don't guess from table names. An alias is unique
  per org and is never a real table's name, so a match is unambiguous. An alias
  is a label, never SQL — query the real `dataset.table`.
- **Notes** (`notes` in `integrations.describe`; flagged by `hasNotes` in
  `integrations.tables`) — what the schema can't say: rows that must be excluded,
  a column whose name lies about its contents, units, who owns the system.
  **Read the notes before writing SQL against a table**, and treat them as
  authoritative — they come from the people who own the data, and ignoring one
  produces a confidently wrong number. Say so when a note changes the answer.

When the user tells you something durable about a table that isn't recorded yet,
offer to save it with `tables.annotate` (see step 4) so the next person — and the
next session — gets it for free.

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

### Verify the data, not just the query
Confirm a field means what you think before you build on it — a wrong assumption
here silently corrupts every number downstream. Cheap checks that pay off:
- **Meaning:** is an amount signed or an unsigned magnitude? Count negatives, count
  nulls, compare candidate columns for mismatches, and confirm a formula holds on
  all rows before trusting it.
- **Dates:** group periods by the economic / posting date, not system timestamps
  like `created_at` / `last_modified_at` (those are sync metadata). When several
  date fields exist, verify which is the real economic date.
- **Column choice:** when two columns could be the same measure, prefer the one
  that's correctly signed **and** most complete (best null coverage); verify
  magnitude-equivalence, and when swapping, add the new column alongside → verify →
  then remove the old (never blind-swap).

## 1. Explore the tables
- `integrations.sources` — connected source systems and freshness.
- `integrations.tables` — tables grouped by dataset, including the
  `dough_calculated` calculated tables. A raw table is shadowed by its
  `<dataset>_mapped` counterpart once that mapping is applied and its rebuild has
  succeeded (not on a draft save) — prefer the `_mapped` one.
  This listing is also the org's **alias directory** (`aliases` per table) and
  flags which tables have notes (`hasNotes`).
- `integrations.describe` — a table's columns, **plus its aliases and the full
  team notes**. `integrations.preview` — a small sample of rows.

## 1b. Bring in data that has no integration (optional)
When the numbers you need are not in any connected system — a budget, a headcount
plan, an allocation key, a mapping someone keeps in a spreadsheet — load them:
- **`tables.upload`.** Send the CSV inline with a bare `name` — Dough decides where
  it lands and reports the full name back. It behaves like any other lake table
  from then on.
- **`mode` is required.** `create` fails if the name is taken, `append` adds rows,
  `replace` overwrites and additionally requires `confirm:true`.
- **`sourceLabel` is required** — where the CSV came from, in words a person would
  recognise ("FY26 budget v3, from Finance"). Recorded against the upload and
  stamped on every row it writes, so a generic filler ("upload", "data") is worth
  no more than the field not existing. `sourceUrl` is optional: a link to the file,
  stored and displayed but never fetched — a claim about origin, not a verified one.
- **`keyColumns` says what identifies one row** (`["period","cost_center"]`) and is
  **required when `mode` is `create`**; later uploads inherit it. Dough matches rows
  between versions on it, so an edited value reads as a change rather than a delete
  plus an insert. Duplicate keys are rejected, and a key column may never be empty.
  You may ADD columns to the key on any upload. REMOVING one is only allowed in a
  `replace` (a narrower key can collide with rows already in the table) — and so is
  widening the key onto a column this upload is introducing, since every existing
  row would have no value for it.
- **Columns on an `append` need not match the table.** A new column is added
  (existing rows have no value for it); a non-key column you omit is simply empty
  for the appended rows; the response names whatever was added or omitted. But
  **doing both in one upload is rejected**, naming both sides — and omitting a
  **key** column always is. Adding-plus-omitting is exactly what a mistyped header
  looks like (`regoin` for `region`), and it is indistinguishable from a deliberate
  drop-and-add, so Dough refuses rather than guessing: **resending the same payload
  fails identically.** Fix the header and resend; or, if the rename is genuinely
  intended, split it into two uploads — the first carrying both the old and the new
  column, the second dropping the old one.
- **Declare the column types** in `columnTypes` (`{"amount":"NUMERIC","period":"DATE"}`);
  anything you omit is `STRING`. Types are never guessed from the data, and values
  are checked against them BEFORE the load — so a stray `N/A` in an amount column
  comes back naming the row and column, not as a failed job later.
- Asynchronous, like the others: poll `tables.status` with `kind:"uploaded"` (it
  defaults to `"calculated"`, which will not find an upload). The response tells you
  the full name to query. **One table loads one upload at a time** — an append
  sent while the previous load is still running is refused, so a second file to
  the same table has to wait for the first (see the uploads skill).
- Ask the person for the data's types and meaning rather than inferring them — then
  record what you were told with `tables.annotate`.
- **More than one file landing in one table** — scenario tabs from a planning
  week, monthly extracts, one file per region — is decided before the first
  upload, not discovered on the second: which header to create from, what tells
  the files apart, and how long to wait between them. Read the **uploads** skill's
  "Several files into one table" before sending any of them. The obvious flow —
  create from file 1, then send 2 and 3 — is refused partway through, and by then
  the table exists with the wrong key.
- If the source is a spreadsheet or document whose structure isn't settled —
  summary rows mixed into detail, wide month columns, formatting that carries
  meaning — use the **uploads** skill first to shape and verify the data.

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
  `tables.status`. There is no delete.
- **Changing that table's query later → `tables.update`.** Use it instead of
  creating `..._v2`. It replaces the table from a new SELECT, so treat it as
  editing shared infrastructure rather than your own scratch table:
  - **Read the current query first.** `tables.status` returns `definitionSql` —
    replace a query you have actually seen, not one you assumed.
  - **The rebuild is CREATE OR REPLACE, so the schema is replaced too.** Drop or
    rename a column and anything selecting it — saved queries, mappings, other
    calculated tables — breaks at its next run, with nothing here detecting it.
  - **`confirm:true` is required, and it stands for a person's approval.** Set it
    only after they explicitly agreed to replace THIS table's query, never from a
    general instruction to fix or improve something. Nothing can verify a human
    agreed, so the flag is only as good as your restraint — if you are inferring
    consent rather than remembering it, ask.
  - Verify the new SELECT with `integrations.query` first, then poll
    `tables.status`. Until the rebuild succeeds the table still serves its
    **previous** result.
- **A query to re-run → `queries.save`.** Store the SQL (create with `name`+`sql`,
  or update by `id`); validates read-only and does not execute.
- **Knowledge about a table → `tables.annotate`.** Record the names the team calls
  it (`aliases`) and what it can't infer from the schema (`notes`). Org-wide and
  durable, so it's there for the next person and the next session. Aliases are
  table-name-shaped (letters, numbers, underscores) and must be free — one
  already in use, or matching a real table's name, is rejected. An omitted field
  is left unchanged; a provided one replaces the stored value. Record what you
  were **told**, not what you inferred — ask before writing a guess.
- These become the calculated tables and saved queries that the **reuse-first**
  step at the top should find next time — so name them clearly.
