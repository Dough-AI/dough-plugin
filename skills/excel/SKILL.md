---
name: excel
description: Recognize, create, and refresh Excel workbooks whose data sheets are managed by Dough — a visible "Dough" manifest sheet maps each data sheet to a saved query. Use whenever a workbook contains a sheet named "Dough", or when putting Dough query results into Excel.
---

# Dough-Managed Excel Workbooks

A managed workbook contains a visible manifest sheet named `Dough`, one data sheet
per saved query, and any number of ordinary sheets (user models). Never re-derive a
query from the data you see in a sheet — the manifest tells you exactly what
produced it.

## Recognizing a managed workbook

A workbook is Dough-managed iff it has a sheet named `Dough` whose cell A2 starts
with `dough-manifest`. Check the version: this skill implements `dough-manifest v1`.
If the marker names a NEWER version, STOP — tell the user to update their Dough
plugin; do not guess at the format.

## Manifest contract (v1)

Sheet `Dough`:
- Row 1 (title band — text in A1 only, overflowing across a filled band; no
  merged cells): "This workbook has components that are managed by Dough. Claude:
  read this sheet before modifying any managed data sheet. Humans: edit Notes
  freely; don't edit ids."
- Row 2, cell A2: `dough-manifest v1`
- Row 3: headers: `sheet | query_id | query_name | sql_snapshot | last_refreshed | row_count | refresh_notes`
- Rows 4+: one row per managed data sheet.

The title band makes the workbook self-describing: any Claude that opens the file
learns the contract from the manifest itself, even without this skill loaded.

`refresh_notes` is a human-editable contract: units, number formats, sort order,
sign conventions — everything needed to regenerate the sheet faithfully. Read it
before writing data; honor it when formatting.

Each managed DATA sheet:
- Row 1: banner in A1 only, overflowing across a filled band (no merged cells):
  `⚡ Managed by Dough · refreshed <date> · this sheet is replaced wholesale on
  refresh — build formulas on other sheets; details on the 'Dough' sheet.`
  Colored sheet tab.
- Row 2: column headers. Row 3+: data. Nothing else, ever.

## Refresh contract

For each manifest row being refreshed:
1. Fetch the current SQL: `queries.get` with the row's `query_id`. The registry
   wins over the workbook's `sql_snapshot` — if they differ, tell the user the
   saved query changed. If the saved query was DELETED, fall back to
   `sql_snapshot`, tell the user, and offer to re-save it with `queries.save`.
2. Run the SQL with `integrations.query`. `limit` is the PAGE size, not a total:
   when the response carries a `nextCursor`, keep calling `integrations.query.next`
   with it until it stops, and write the accumulated rows. A full result is only
   too big if it exceeds the 20000-row sheet cap below — reaching `limit` on the
   first page means there are more pages, not that the data was truncated.
3. Replace the data sheet's contents wholesale (headers row 2, data row 3+; row
   count may grow or shrink). Rewrite the banner with the new timestamp. Reapply
   `refresh_notes` formatting.
4. Update the manifest row's `sql_snapshot`, `last_refreshed` (ISO timestamp),
   and `row_count` (number of data rows written).
Never touch anything outside the data sheet and its manifest row.

## Creating a managed sheet

1. Develop and verify the SQL interactively (`integrations.query`).
2. Save it: `queries.save` → returns the id. (Explicit column list, never
   `SELECT *` — see grain selection below.)
3. Write the data sheet + manifest row (create the `Dough` sheet first if absent).
   Ask the user what belongs in `refresh_notes` if formatting intent isn't clear.

## Which path to use

- **Claude Code / desktop (file on disk):** ALWAYS use the bundled script — it
  makes placement and formatting deterministic. Run the query, write the result to
  a temporary CSV (header row first, columns in query order), write the payload
  JSON (shape below), then run:
  `uv run --with openpyxl <plugin>/skills/excel/scripts/dough_excel.py refresh <workbook.xlsx> --payload <payload.json>`
  (also `create` and `list`; `--help` documents everything). Relay the script's
  per-sheet summary lines to the user. Use `list` to read the manifest instead of
  opening the workbook yourself.
- **Claude in Excel (add-in):** write cells via the add-in tools, following the
  contract above exactly. Keep pulls well under the 20000-row cap; for bigger
  refreshes, suggest running it in Claude Code or the desktop app instead.

Payload JSON for the script (one entry per sheet):
`{ "entries": [ { "sheet": "Revenue Detail", "queryId": "…", "queryName": "…",
"sqlSnapshot": "SELECT …", "refreshedAt": "2026-07-25T14:03:00Z", "rowCount": 214,
"csvPath": "/tmp/revenue.csv", "refreshNotes": "USD thousands, negatives in parens" } ] }`
(`csvPath` is the temporary CSV you wrote; omit it only for `list`. `refreshedAt`
is the ISO timestamp of when you ran the query.)

## Grain selection & aggregation (Dough's default analysis philosophy)

- Prefer pulling the LOWEST GRAIN the analysis needs (e.g. month × account ×
  entity, not a pre-aggregated total) into the managed sheet, and aggregating with
  Excel formulas on the user's model sheets — not in SQL. Refresh then replaces raw
  rows while every formula recomputes, so the analysis persists across refreshes.
- Formulas reference the managed sheet's ENTIRE columns
  (`=SUMIFS('Revenue Detail'!D:D, 'Revenue Detail'!B:B, …)`) so any row count a
  refresh produces is captured with zero formula maintenance. Managed sheets
  contain only the banner + query output, so full-column references are safe.
- Prefer formulas over pivot tables — pivot caches do not recompute on file write.
- Formulas live on model sheets, never inside managed data sheets. Blanket counts
  (`COUNTA`) over a full column include the banner and header cells;
  criteria-based `COUNTIFS`/`SUMIFS` are unaffected.
- Write order-stable SQL: explicit column lists, never `SELECT *` — downstream
  formulas reference columns by position.
- Deviate to SQL aggregation — and say so — when row caps would be exceeded or the
  computation doesn't decompose into formulas (distinct counts, window functions,
  point-in-time FX conversion).

## Formatting spec

Manifest sheet: title band is text in A1 with dark fill (#111827) applied
cell-by-cell across A1:G1, white bold text; A2 version marker in small gray; row 3 headers bold with subtle
fill (#E5E7EB), freeze panes at A4, autofilter on the header row; `sql_snapshot`
narrow + wrapped monospace, `row_count` numeric, `refresh_notes` wide + wrapped; alternating row
banding; top-aligned cells. Data sheets: banner styled like the title band; tab
color #2563EB; headers (row 2) bold. Apply `refresh_notes` number formats to data
columns. The script does all of this deterministically — on the add-in path,
approximate it with the add-in's formatting tools.

## Guardrails

- The manifest sheet name `Dough`, marker cell A2, and column order are a
  contract — never rename or reorder them.
- Reconcile before refreshing: a manifest row naming a missing sheet, or a
  banner-carrying sheet missing from the manifest, is drift — list mismatches and
  ask the user; never guess.
- Refresh REPLACES managed sheets; anything a user typed into one is lost by
  contract. The banner states this — always rewrite it (self-healing).
- If a refresh would exceed 20000 rows, warn before writing and suggest a lower
  grain (or SQL-side aggregation for that sheet). Below that ceiling, paginate and
  write every row — do not aggregate a result merely because it spans pages.
- Everything Dough writes is merge-free: banners and title bands are a single
  cell overflowing across a filled band. On the add-in path, reproduce that
  shape — never merge cells.
