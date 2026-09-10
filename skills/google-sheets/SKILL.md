---
name: google-sheets
description: Use when putting Dough query results into a Google Sheet, when a spreadsheet at a Google Sheets URL or id carries a tab named "Dough", or when a Google Sheet built from saved queries needs refreshing. Same manifest contract as the excel skill, on Google Sheets through the gws CLI.
---

# Dough-Managed Google Sheets

A managed spreadsheet contains a visible manifest tab named `Dough`, one data
tab per saved query, and any number of ordinary tabs (user models). Never
re-derive a query from the data you see in a tab — the manifest tells you exactly
what produced it.

This is the Google Sheets twin of the `excel` skill: the manifest is the same
`dough-manifest v1` contract, so one spreadsheet reads the same whether it is an
`.xlsx` on disk or a Google Sheet. What differs is the transport — the bundled
script talks to Google through the `gws` CLI.

## Before anything: is `gws` connected?

The script needs `gws` installed and authorised. If it is missing, or a call
fails with 401/403, load `dough:gws-connect` and follow it. Do not install or
authorise from here, and never pull credentials out of `gws` (`gws auth export`)
to call Google's APIs yourself — the script exists so that no session ever
handles a token.

## Recognizing a managed spreadsheet

A spreadsheet is Dough-managed iff it has a tab named `Dough` whose cell A2
starts with `dough-manifest`. Check the version: this skill implements
`dough-manifest v1`. If the marker names a NEWER version, STOP — tell the user to
update their Dough plugin; do not guess at the format.

Use the script's `list` command to read the manifest. Do not read cells by hand.

## Manifest contract (v1)

Tab `Dough`:
- Row 1 (title band — text in A1 only, overflowing across a filled band; no
  merged cells): "This workbook has components that are managed by Dough. Claude:
  read this sheet before modifying any managed data sheet. Humans: edit Notes
  freely; don't edit ids."
- Row 2, cell A2: `dough-manifest v1`
- Row 3: headers: `sheet | query_id | query_name | sql_snapshot | last_refreshed | row_count | refresh_notes`
- Rows 4+: one row per managed data tab.

`refresh_notes` is a human-editable contract: units, number formats, sort order,
sign conventions — everything needed to regenerate the tab faithfully. For a
parameterised saved query it also records the parameter values the rows were run
with (`params: period_start=2025-08-01, period_end=2026-07-01`), because v1 has
no parameters column. Read it before writing data; honor it on refresh.

Each managed DATA tab:
- Row 1: banner in A1 only, overflowing across a filled band (no merged cells):
  `⚡ Managed by Dough · refreshed <date> · this sheet is replaced wholesale on
  refresh — build formulas on other sheets; details on the 'Dough' sheet.`
  Blue tab colour.
- Row 2: column headers. Row 3+: data. Nothing else, ever.

## Refresh contract

For each manifest row being refreshed:
1. Fetch the current SQL: `queries.get` with the row's `query_id`. The registry
   wins over the spreadsheet's `sql_snapshot` — if they differ, tell the user the
   saved query changed. If the saved query was DELETED, fall back to
   `sql_snapshot`, tell the user, and offer to re-save it with `queries.save`.
2. Run the SQL with `integrations.query`, with the parameters `refresh_notes`
   records. `limit` is the PAGE size, not a total: set `maxRows` to the whole
   result and, when the response carries a `nextCursor`, keep calling
   `integrations.query.next` until it stops. Write every row to the CSV.
3. Run the script's `refresh`. It replaces the data tab's contents **in place**
   (banner, headers, rows; row count may grow or shrink), rewrites the banner
   with the new timestamp, and updates the manifest row's `sql_snapshot`,
   `last_refreshed`, and `row_count`.
Never touch anything outside the data tabs and their manifest rows.

**Refresh clears a managed tab; it never deletes or replaces it.** In Google
Sheets, deleting a tab turns every formula that references it into an error,
permanently — adding a tab of the same name back does not repair them. The same
goes for replacing the whole spreadsheet through a Drive upload: every tab gets
a new identity and every tab the user added is gone. The script keeps each
managed tab's sheet id, so `SUMIFS` on a model tab keeps working across refreshes.
If a user asks you to delete a managed tab, say what will break first.

## Creating a managed tab

1. Develop and verify the SQL interactively (`integrations.query`).
2. Save it: `queries.save` → returns the id. (Explicit column list, never
   `SELECT *` — downstream formulas reference columns by position.)
3. Run the query, write the result to a temporary CSV (header row first, columns
   in query order), write the payload JSON, and run the script's `create`.
   Ask the user what belongs in `refresh_notes` if formatting intent isn't clear.

## The script

ALWAYS use the bundled script for managed tabs — it makes placement and
formatting deterministic, and it is the only supported way to get cell contents
to Google from this plugin. Do not write your own Sheets client for the job.

```sh
# macOS
python3 <plugin>/skills/google-sheets/scripts/dough_sheets.py list    <url-or-id>
python3 <plugin>/skills/google-sheets/scripts/dough_sheets.py create  <url-or-id> --payload payload.json
python3 <plugin>/skills/google-sheets/scripts/dough_sheets.py create  --new "Title" --folder <folderId> --payload payload.json
python3 <plugin>/skills/google-sheets/scripts/dough_sheets.py refresh <url-or-id> --payload payload.json --all
```

```powershell
# Windows — the py launcher, or plain python if it is absent
py -3 <plugin>\skills\google-sheets\scripts\dough_sheets.py list <url-or-id>
```

`--help` documents everything. `create --new` prints the new spreadsheet's URL on
its first line; give it to the user. Relay the per-tab summary lines.

Payload JSON (one entry per tab; identical to the excel skill's):
`{ "entries": [ { "sheet": "Revenue Detail", "queryId": "…", "queryName": "…",
"sqlSnapshot": "SELECT …", "refreshedAt": "2026-07-25T14:03:00Z", "rowCount": 214,
"csvPath": "/tmp/revenue.csv", "refreshNotes": "USD thousands, negatives in parens" } ] }`
(`refreshedAt` is the ISO timestamp of when you ran the query.)

### Why the script stages through Drive

`gws --json` accepts a request body only inline, as one command-line argument.
Windows caps a command line at 32K characters, and on managed Macs an
endpoint-security agent kills any process whose argument passes ~900 — a
symptom you will see as `gws` dying with exit code 137 on a modest body. So the
script never puts cell contents on a command line: it builds a typed workbook,
uploads it once through Drive as a temporary staging spreadsheet, copies each
tab across, pastes values into the managed tab in place, and deletes the
staging file. Types survive (a `0100` cost-centre code stays text; amounts stay
numbers). If you see a `dough-sheets staging …` file linger in Drive after a
failed run, it is safe to delete.

## Where spreadsheets can live

The `gws` grant is `drive.file`: the app sees only files it created or was
handed by URL. A new spreadsheet goes in the `Dough` folder from gws-connect (or
a subfolder of it) — pass its id as `--folder`. A folder the user made by hand in
the Drive UI is invisible to the app, so `--folder` on one fails. If a user asks
you to "find" a sheet, ask for its URL; do not report the grant as broken.

## Formula tabs

Model tabs are ordinary tabs the script never touches. Write them with one
direct call, `USER_ENTERED` so formulas are formulas:

```sh
gws sheets spreadsheets values update \
  --params '{"spreadsheetId":"<id>","range":"Summary!A1:E12","valueInputOption":"USER_ENTERED"}' \
  --json '{"values":[["Mapping","Apr","May"],["Revenue","=SUMIFS('MoM by Account'!E:E,'MoM by Account'!C:C,A2)", …]]}'
```

Keep each call's body small — a few dozen cells per call, well under 900
characters — for the reason above. A summary tab is a handful of such calls.
Add the tab first with a `batchUpdate` `addSheet` request if it does not exist.

## Grain selection & aggregation

- Pull the LOWEST GRAIN the analysis needs into the managed tab and aggregate
  with formulas on model tabs, not in SQL. Refresh then replaces raw rows while
  every formula recomputes, so the analysis persists across refreshes.
- Reference the managed tab's ENTIRE columns (`'Revenue Detail'!D:D`) so any row
  count a refresh produces is captured with zero formula maintenance. Managed
  tabs contain only the banner + query output, so full-column references are
  safe. Blanket counts (`COUNTA`) over a full column include the banner and
  header cells; criteria-based `COUNTIFS`/`SUMIFS` are unaffected.
- Prefer formulas over pivot tables; formulas live on model tabs, never inside
  managed tabs.
- Every page of a query result passes through the conversation. Below the
  20,000-row cap, page and write every row — do not aggregate a result merely
  because it spans pages. Past the cap, or when a result is very wide and deep,
  say so and aggregate in SQL for that tab.

## Guardrails

- The manifest tab name `Dough`, marker cell A2, and column order are a
  contract — never rename or reorder them.
- Reconcile before refreshing: a manifest row naming a missing tab, or a
  banner-carrying tab missing from the manifest, is drift — list mismatches and
  ask the user; never guess. The script exits 3 on drift.
- Refresh REPLACES a managed tab's contents; anything a user typed into one is
  lost by contract. The banner states this — the script always rewrites it.
- Never delete a managed tab, never replace the spreadsheet through a Drive
  upload, never extract credentials from `gws`, never hand-roll a Sheets client.
- Everything Dough writes is merge-free: banners and title bands are a single
  cell overflowing across a filled band.
