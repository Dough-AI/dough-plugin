# Design: `google-sheets` skill — Dough-managed Google Sheets via the `gws` CLI

**Date:** 2026-09-10
**Status:** Approved design; updated during implementation with measured facts
**Source material:** the `excel` skill and its `dough_excel.py` script (manifest
v1), the `gws-connect` skill and `triage.py`, and the `gws` 0.22.5 command
surface as measured on this machine.

## Problem

The `excel` skill lets Dough manage data sheets inside a workbook on disk: a
visible `Dough` manifest maps each data sheet to a saved query, and a script
creates and refreshes those sheets deterministically. Nothing equivalent exists
for Google Sheets, even though `gws-connect` already gets the `gws` CLI
installed and authorised for exactly that. A user who wants a saved query in a
Google Sheet today gets hand-assembled API calls, no manifest, and no refresh.

## Goal

A sibling skill, `google-sheets`, that applies the same manifest contract to a
Google Sheet and ships a script that talks to `gws` directly, so `list`,
`create`, and `refresh` are deterministic on a spreadsheet the way they are on
an `.xlsx`. Verified end to end against real Google Sheets and real saved
queries, including formulas on other tabs surviving a refresh.

## Decisions made during brainstorming

- **Same manifest contract as Excel** (`dough-manifest v1`), as a visible
  `Dough` tab. A spreadsheet stays self-describing to a human and to a Claude
  without the skill loaded. Developer metadata was rejected for being invisible.
- **The script calls `gws` itself** rather than emitting request bodies for
  Claude to send. Deterministic placement and formatting; short skill text.
- **New sibling skill, own script, stdlib only.** Extending `excel` was rejected
  (muddles the trigger, drags openpyxl into the Sheets path). A shared contract
  module under `skills/references` was rejected (a cross-skill import breaks
  when a skill is synced alone via `dough skill sync`). The contract constants
  are duplicated and a test pins them equal.
- **Name:** `google-sheets`, loaded as `dough:google-sheets`.
- **No subcommand for formula tabs.** Formula tabs are small; Claude writes
  them with one direct `gws` call using `USER_ENTERED`. The skill teaches that.
- **Testing is live.** A script-level suite against a real spreadsheet, and an
  opt-in model-driven e2e against the live Dough MCP and real `gws`, exercising
  three real saved queries and SUMIFS over a managed tab. Offline fakes were
  rejected as the primary test: the user wants the real seam exercised.

## Three facts that shape the script

1. **`gws --json` takes the body inline only.** Neither `--json @file` nor
   stdin works (verified with `--dry-run`). The whole body is one argv string.
   Windows caps a command line at 32,767 characters.
2. **On this machine an endpoint-security agent (SentinelOne) kills `gws`
   once a single argument passes ~910 characters** — SIGKILL, exit 137, no
   crash report, regardless of what the command does, inside or outside the
   Claude Code sandbox. Measured by bisection; `gws --help <1.4K junk>` dies
   the same way. A saved query's SQL alone is longer than that, so no amount
   of row-chunking makes inline bodies viable on managed Macs. Reading
   credentials out of `gws auth export` to call the REST API directly was
   rejected: the session would hold a token, which is what gws-connect's
   design exists to avoid (and the baseline model did exactly this).
3. **Deleting a tab in Google Sheets turns every formula referencing it into
   an error, permanently** — verified: adding a tab of the same name back does
   not repair it. The Excel script deletes and recreates the data sheet on
   refresh and gets away with it because Excel re-resolves sheet names on
   open. The Sheets script must clear the tab in place and keep its sheet ID.

So cell contents never travel on a command line. The script builds a typed
`.xlsx` with the standard library, uploads it once through `gws drive files
create --upload` (Drive converts it to a Google Sheet; verified that text stays
text, `0100` stays `0100`, numbers stay numbers, a 1,200-character cell arrives
whole), copies each tab into the target with `sheets.copyTo`, pastes values
into the managed tab in place with `copyPaste`, and deletes the staging file.
Formatting and structure go through `batchUpdate` bodies split to stay under
`ARG_BUDGET` (800). This is fewer calls than chunked `values.update` on any
machine and removes the Windows limit as a concern.

## Contract (unchanged from Excel, restated for Sheets)

Tab `Dough`:
- Row 1: title text in A1 only; dark fill (#111827) and white bold text applied
  cell by cell across A1:G1. Never merged.
- A2: `dough-manifest v1`, small gray.
- Row 3: headers `sheet | query_id | query_name | sql_snapshot | last_refreshed
  | row_count | refresh_notes`, bold, fill #E5E7EB. Frozen rows = 3. Basic
  filter on row 3.
- Rows 4+: one row per managed tab. Wrapped, top-aligned; `sql_snapshot` in a
  monospace font; alternating banding on even rows. Column widths as Excel
  (A 24, B 38, C 28, D 30, E 22, F 10, G 48, in character units scaled to
  pixels).

Each managed data tab:
- Row 1: banner text in A1 only, dark band across the data width.
  `⚡ Managed by Dough · refreshed <date> · this sheet is replaced wholesale on
  refresh — build formulas on other sheets; details on the 'Dough' sheet.`
  The wording is kept identical to Excel so the banner constant is shared.
- Tab color #2563EB. Row 2: headers, bold. Rows 3+: data. Nothing else.
- Grid sized to exactly the data (rows + 2, data width). Full-column
  references from other tabs (`'MoM by Account'!E:E`) work regardless of size.

Parameterised saved queries: v1 has no params column (Excel has the same gap).
The parameter values used are recorded in `refresh_notes`, e.g.
`params: period_start=2025-08-01, period_end=2026-07-01`, and a refresh reuses
them.

Version handling is identical to Excel: a newer marker stops with "update the
Dough plugin"; an older one stops with "migrate".

## Script: `skills/google-sheets/scripts/dough_sheets.py`

Standard library only. Runs with `python3` on macOS and `py -3` (or `python`)
on Windows.

Subcommands, same payload JSON as the Excel script:

```
dough_sheets.py list    <sheet>
dough_sheets.py create  <sheet> --payload payload.json
dough_sheets.py create  --new "Title" [--folder <driveFolderId>] --payload payload.json
dough_sheets.py refresh <sheet> --payload payload.json [--sheets A,B | --all]
```

`<sheet>` is a full Google Sheets URL or a bare spreadsheet ID. `create --new`
makes the spreadsheet through `gws drive files create` (so it can be placed in
a folder the app can see) and prints its URL on the first line of output.

Payload: `{"entries": [{"sheet", "queryId", "queryName", "sqlSnapshot",
"refreshedAt", "rowCount", "csvPath", "refreshNotes"}]}` — identical to Excel.
`csvPath` is a UTF-8 CSV with a header row; a BOM is tolerated.

### gws mechanics

- **Locating `gws`:** `GWS_BIN` env override, then PATH via `shutil.which`,
  then `/usr/local/bin/gws`, `~/.local/bin/gws` and
  `%LOCALAPPDATA%\dough\bin\gws.exe` — where the installer actually puts it. Absent:
  exit 4 with "run the gws-connect skill".
- **Invocation:** always an argument list, never a shell string. Judge by exit
  code. Parse stdout from the first `{`, because every call prints
  `Using keyring backend: …` on stderr and may print other noise.
- **Reads:** `spreadsheets get` with `fields=properties.title,spreadsheetUrl,
  sheets.properties,sheets.merges` for the tab list, sheet IDs, and merge
  check; `values get` on `Dough!A1:G` for the manifest (trailing empty cells
  are padded back to seven columns).
- **Writes:** never inline. `Staging` builds one `.xlsx` holding every data
  tab (banner, headers, rows) plus a `manifest` tab with one row per entry,
  uploads it from a temp directory (gws refuses `--upload` paths outside its
  working directory), reads back the staged sheet IDs, and `copyTo`s each
  into the target. A new managed tab is the copy itself, renamed and resized;
  an existing one is cleared (`updateCells` with `fields="*"`), resized, and
  filled with `copyPaste` (`PASTE_VALUES`, destination bounded exactly — an
  open-ended destination repeats the source to fill the grid), then the copy
  is deleted. Manifest rows are `copyPaste`d one at a time from the staged
  `manifest` tab into their upserted row. Numbers are coerced with the Excel
  script's strict decimal rule before staging.
- **Formatting:** `batchUpdate` requests grouped into calls whose body stays
  under `ARG_BUDGET`, in order. Colours are sent at full float precision;
  Google floors channels to 8-bit, so a rounded 0.3882 for 0x63 comes back as
  0x62.
- **Refresh of an existing tab:** clear in place as above. The sheet ID never
  changes. A tab named in the manifest but missing from the spreadsheet is
  recreated with a note on stderr, as Excel does.
- **Rate limits:** the Sheets API allows 60 write requests per minute per
  user. On HTTP 429 the script sleeps with exponential backoff (1, 2, 4, 8,
  16 s) and retries the same chunk; after five failures it exits 4.
- **Partial failure:** the staging file is deleted in a `finally`; if that
  fails its ID is printed. A run that dies between tabs leaves the manifest
  consistent with the tabs already landed, and the next run repairs the rest.

### Windows

- Both invocations shown in `--help` and in the skill.
- stdout and stderr reconfigured to UTF-8 (the banner has a non-ASCII
  character; a cp1252 console would crash on printing it).
- `pathlib` throughout; `csvPath` used as given.
- No cell content on the command line anywhere, so the Windows limit and the
  endpoint-security kill are both moot; `ARG_BUDGET` bounds the rest.

### Exit codes

`0` success · `2` usage or validation · `3` manifest drift or version mismatch
· `4` gws failure (binary missing, auth, HTTP error after retries), with the
last lines of gws's stderr.

## Skill: `skills/google-sheets/SKILL.md`

Frontmatter description: recognise, create, and refresh Google Sheets whose
data tabs are managed by Dough; trigger on a Google Sheets URL or ID, "put
this in a Google Sheet", or a spreadsheet carrying a `Dough` tab. Body mirrors
the Excel skill section for section — recognising, contract, refresh contract,
creating, grain selection, formatting, guardrails — with these Sheets-specific
additions:

- **Connection first.** If `gws` is missing or returns 401/403, load
  `dough:gws-connect`. Do not install or authorise from this skill.
- **Where files can live.** The grant is `drive.file`: the app sees only files
  it created or was handed by URL. Put new spreadsheets in the `Dough` folder
  from gws-connect's Stage 4 (or a subfolder), never a folder the user made in
  the Drive UI. A user who asks Claude to "find" a sheet is asked for its URL.
- **Formula tabs.** Write them with one `gws sheets spreadsheets values update`
  call, `valueInputOption=USER_ENTERED`, body inline. Keep the body under
  ~24,000 characters; a summary tab is a few dozen cells. Full-column
  `SUMIFS` over the managed tab is the default pattern. `COUNTA` over a full
  column includes the banner and header, as in Excel.
- **Never delete a managed tab.** Deleting breaks every formula pointing at
  it; the script clears in place. If a user asks to remove a managed tab,
  say what will break first.
- **Reading the manifest:** use `list`; do not read cells by hand.
- **Paging:** unchanged from Excel — `maxRows` for the whole result, page
  with `integrations.query.next` until no cursor. Add: every page passes
  through the conversation, so a wide, deep result is worth aggregating in
  SQL for context reasons as well as the 20,000-row cap.

## Tests

### `tests/test_dough_sheets_live.py` — script against a real spreadsheet

Skips unless `triage.py` reports `CONNECTED`. Module fixture: find or create
`Dough` / `Dough sheets tests` through `gws drive files`; create one
spreadsheet per run named `dough-sheets-test <UTC timestamp>`; delete it at
teardown unless `DOUGH_KEEP_SHEETS=1`. Cases:

- create into a new spreadsheet: `Dough` tab first, marker, manifest row,
  banner, headers, numbers coerced, no merges.
- create into a spreadsheet that already has a user tab: the tab survives.
- refresh with fewer rows and a mangled banner: banner self-healed with the
  new date, old rows gone, manifest timestamp and row_count updated in place.
- a formula tab with `SUMIFS` over the managed tab computes before refresh
  and still computes after (read back with `UNFORMATTED_VALUE`; no `#REF!`).
- sheet ID unchanged across refresh.
- refresh naming an unknown tab exits 3 with "reconcile".
- newer manifest marker exits 3 with "update the Dough plugin"; older exits
  3 with "predates".
- a big tab (1,500 rows): every row lands, typed.
- `gws` missing exits 4 and names the gws-connect skill.

### `tests/test_google_sheets_e2e.py` — model-driven, opt-in

Skips unless `DOUGH_E2E=1`. Runs `claude -p` with `--plugin-dir` set to this
repo, the plugin's own `.mcp.json` (live Dough), and real `gws`; allowed tools
Bash, Read, Write, Edit, Skill, and the `mcp__dough__queries__*` and
`mcp__dough__integrations__query*` tools. Two separate sessions:

1. "Put these three saved queries into a new Google Sheet in the Dough
   sheets tests folder, and add a summary tab that totals the MoM by Account
   tab by audit mapping for each month with SUMIFS": Audit P&L — MoM by
   Account (Apr–Jul 2026); Revenue and margin by month (parameterised,
   defaults); Operating expenses by department, top 7 plus tail (defaults).
2. "Refresh every managed tab in <url>."

Assertions, as invariants rather than transcripts:
- the manifest has three rows whose `query_id`s are the real saved-query IDs;
- each managed tab has the banner, bold headers, and a row count equal to the
  manifest's `row_count`;
- no merged ranges anywhere;
- the summary tab's SUMIFS values equal sums computed by the test from the
  managed tab's cells, for at least one mapping and month;
- a tab the test adds between the turns, with a formula over a managed tab,
  survives the refresh and still computes (rules out refresh-by-re-upload);
- after turn 2, the same cells still hold numbers, not `#REF!`, and every
  manifest `last_refreshed` moved forward;
- `refresh_notes` on the parameterised query names its parameter values.

The spreadsheet is deleted at teardown unless `DOUGH_KEEP_SHEETS=1`; the run
directory keeps both agents' stdout.

### `tests/test_google_sheets_skill_contract.py` — drift check

Pins the skill's claims with their reasons, in the style of
`test_gws_connect_skill_contract.py`: never delete a managed tab; `USER_ENTERED`
for formula tabs; body-size note; `drive.file` placement; routes to
`gws-connect`; both invocations shown. Also asserts that the contract
constants in `dough_sheets.py` (`MANIFEST_HEADERS`, `VERSION_MARKER`,
`TITLE_TEXT`, `BANNER_TEMPLATE`, fills, tab color, `ROW_CAP`, `NUMERIC_PATTERN`) equal those in
`dough_excel.py`, by importing both modules.

## Housekeeping

- Bump `plugin.json` and `marketplace.json` to 0.29.0 (CI checks agreement).
- README: list the skill next to `excel`.
- `getting-started`: route Google Sheets work to `google-sheets`.
- CI: nothing to add; the live suites self-skip there.

## Out of scope

- Migrating an Excel workbook to a Google Sheet or vice versa.
- A manifest v2 with a parameters column.
- Number formats parsed from `refresh_notes` (Excel does not do this either).
- Windows execution of the live suites from this machine.
