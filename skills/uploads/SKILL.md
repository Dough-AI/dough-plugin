---
name: uploads
description: Use when loading user-provided data into Dough as an uploaded table — a budget, forecast, latest estimate, operating plan, sales projection, pipeline export, or anything parsed from a spreadsheet, CSV, or document — and the table's structure isn't already decided. Covers reading the implicit structure of human-formatted sources, the canonical shape for uploaded tables, reconciling parsed rows against the source's own totals before loading, and recording names, aliases, and caveats.
---

# Structuring data for upload

How to get from a human-formatted source to a well-shaped `dough_uploaded`
table. Tool mechanics (`tables.upload`, `mode`, `columnTypes`, polling) live in
the datalake skill, section 1b — this skill decides **what shape the table
should be** and **how to prove the parse is right** before that call.

## 0. Fast path
Already-tidy data — clean CSV, obvious grain, no summary rows mixed into
detail — needs none of what follows: confirm column types and meaning with the
user and upload directly. No tie-out; nothing was parsed, so there is nothing
to reconcile. The rest of this skill is for human-formatted sources.

One thing the fast path does **not** skip: if the tidy data came out of a
workbook, that workbook still goes up with it (`sourceWorkbook`, step 5). A
clean single-sheet `.xlsx` needs no parsing decisions and is still the file the
numbers actually live in.

## 1. Understand the source
Inventory what you were given — tabs, regions within tabs, or data extracted
from a document — and classify each region: **detail rows** (the finest grain
present), **derived rows** (subtotals, totals, summary blocks, pivot output),
or commentary. Detail is the data; derived figures are the verification
targets for step 4 — collect them now, never load them.

For a formatted spreadsheet or interpreted document, read
`../references/structuring-uploads-guide.md` first — formatting cues (bold,
blank rows, side-column section labels) carry the structure, and several
common source pathologies will silently corrupt a naive parse.

When more than one tab, region, or date range could plausibly be the source of
truth, ask — name the candidates and let the user pick. Users routinely
correct which tab and which period window; a wrong guess here poisons
everything downstream.

## 2. Decide the shape
The canonical uploaded table, one row per lowest-grain dimension per period:

| Column | Type | Rule |
|---|---|---|
| dimension hierarchy | STRING × N | One column per level, broadest → narrowest (`category`, `subcategory`, `vendor`, …). The narrowest level is the grain; every parent appears as a sibling column on every row. |
| `period` | DATE | **End-of-month** for monthly data (`2027-01-31`, not `2027-01-01`). |
| `amount` | FLOAT64 | Full precision, never pre-rounded. |
| commentary | STRING | Only if the source carries commentary worth keeping. |

State the grain in one sentence — "one row per vendor per month" — before
writing any parse code, and declare it: the columns that identify a row are
the upload's `keyColumns` (required on create). If parsed rows collide under
the key your grain sentence implies, the grain statement is wrong — fix it
before uploading, not after the rejection. Then the rules that make tables
consistent org-wide:

- **Capture the source's lowest dimension level plus its full parent chain.**
  There is no privileged "line item" — it's just the narrowest level.
- **Columns come from the source — its data or its identity.** A scenario or
  version discriminator that lives only in a tab name or file name is still
  source data: synthesize it deliberately, name its per-file values for the
  user, and add it to `keyColumns`. What never gets a column is an
  assumption — currency, units, draft-vs-final; those are table notes.
- **Output measures only.** If the source derives revenue = price × quantity,
  load revenue at the lowest grain, not price and quantity. The table records
  outcomes at a grain, not the model that produced them.
- **Empty and zero cells are not rows.** Load the cells that hold a value.
- **Names verbatim** at every dimension level, aligned with what the table
  will join against — if actuals spell it `Professional Fees:Product
  Consulting`, match it, or record the mismatch deliberately.
- **Commentary attaches once per line item** — to its latest populated
  period — unless the user wants it elsewhere. Never duplicate it onto every
  row and never drop it silently.

Raise structural questions as they arise, conversationally: period scope,
exclusion rules ("leave out vendor X" — make it null-safe), and, when the data
is one snapshot of a plan that will have successors (operating plan → latest
estimates), the versions question: **separate table per snapshot** (immutable,
simple, compare by joining across tables) vs. **one table with a scenario
column** (append each snapshot, filter to compare, a missed filter silently
mixes scenarios). Present both with trade-offs; there is no default.

### Several files into one table
Any group of files that belongs in one table: monthly or quarterly extracts, one
file per region or entity, tabs of a workbook, successive snapshots of the same
plan (the one-table answer to the versions question above, reached from the other
direction). The rules below do not depend on which of those it is. Whatever the
files came from — CSVs, workbook tabs, exported sheets — they must be CSV before
they are uploaded, and each one's location travels with it as that upload's
`sourceUrl`. Converting to CSV is a step on the way, not a decision to discard
what you converted: when the files came out of a workbook, that workbook goes up
too (step 5). Then, **before the first upload:**
- **Take the union of every file's header, and create with all of it.** Files cut
  from the same template still disagree: one period added a column, one region
  tracks something the others don't. A later file that omits columns the table
  has is fine. A later file that both adds a column and omits one is rejected —
  and creating from the union is what stops that from ever arising.
- **Ask whether two files can hold the same row.** Work out what makes a row
  unique, then check whether the files partition on it or overlap. Files that
  each cover a different slice usually partition — monthly extracts differ by a
  `period` that is already in the data, regional files by a `region` column. Files
  that are alternative *views of the same rows* always overlap: scenarios of one
  plan, successive versions of one forecast, an actuals file re-exported after a
  correction. Overlapping means every row of the second file collides with the
  first under the key, and the upload is rejected.
- **When they do overlap, what separates them is usually not in the data.** It
  lives in the files' identity rather than their contents — a tab name, a filename,
  a date stamped on the export, the workbook it came out of. So it has to be
  synthesized: this is the from-identity column of the shape rules above. Propose
  it and its per-file values, add it to `keyColumns`, and confirm before uploading.
  Files that partition cleanly need none of this — don't add a discriminator that
  earns nothing.
- **Confirm the files really are one table.** Matching headers are not the same as
  matching meaning; that judgement is the person's, not yours.
- **One file per `tables.upload` call**, each with its own `sourceLabel`,
  `sourceUrl` and `sourceWorkbook`. Do not concatenate them locally — that
  collapses every row's origin into a single pointer, which is the thing per-row
  provenance exists to prevent.

Then, sending them:
- **Let each file's load finish before sending the next one to that table.** Poll
  `tables.status` with `kind:"uploaded"` until it reports the table ready — a load
  takes a few seconds — and only then upload the next file. An append sent while
  the previous one is still loading is refused (`upload_in_flight`): its keys are
  checked against the rows already in the table, and those rows are not there yet.
- **That refusal is a wait, not a problem to report.** Nothing was recorded and
  nothing was loaded; the CSV is fine. Poll until the table is ready and **send the
  identical payload again** — it will be accepted, and its keys will be checked
  this time. This is the exact opposite of the add-and-omit rejection in datalake
  1b, where resending the same payload fails identically: confusing the two either
  abandons a file that was correct or keeps re-sending one that never will be.

## 3. Parse
Bespoke code per session — read the actual file, don't force it through a
generic parser. Standing rules: keep full precision end to end and never round
before aggregating; melt wide period columns into long rows; keep derived rows
aside as verification targets; drop nothing silently. Placeholders that mean
"no value" ("N/A", "-", "TBD") stay in the CSV and are declared as
`nullTokens` on the upload — don't type a numeric column as STRING to force
them through, and don't silently rewrite cells.

## 4. Verify — proportional to blast radius
- **Small, simple parse** (one region, tens of rows, no interpretive calls):
  check the grand total against the source, plus a per-period total if the
  source shows one. Done. Don't build a reconciliation harness for a 20-row
  budget.
- **Full tie-out** — required for a multi-tab or multi-table session, hundreds
  of rows, interpretive parsing (subtotal heuristics, section boundaries,
  inferred values), or data others will build on: every derived figure
  collected in step 1 must equal the sum of parsed detail beneath it
  **exactly**, with row counts checked where knowable.

Say which tier you're on ("small parse: verifying grand total only") so the
user can ask for the full treatment. On failure, suspect in order: your parse
(misclassified subtotal, dropped rows, misread section boundary), then the
source (stale pastes, cells linked elsewhere — see the reference guide).
Source-side failures are findings to report, not errors to absorb. A checked
figure that neither ties nor carries the user's explicit waiver blocks the
upload — no "close enough", no reconciling later.

## 5. Upload and record
Mechanics per datalake 1b. Conventions this skill adds:
- Table name: BigQuery-safe (letters, numbers, underscores — no spaces,
  hyphens, or leading digits). Put the human display name in the notes.
- `columnTypes` declared for **every** column; `keyColumns` = the grain you
  stated in step 2.
- `sourceLabel` (required) says where the data actually came from, in words a
  person would recognize — the sheet, tab, or file and who keeps it ("2026
  Operating Plan Detail tab, finance's budget sheet"), never what you did with
  it. `sourceUrl` when the source has a location.
- **Keep the file you parsed** — `sourceWorkbook`, when the file you actually
  read in step 3 is not the CSV you are uploading. Mechanics (`tables.source.prepare`
  → PUT → pass the `objectPath` back) are datalake guide item 5. What has to be
  decided here is whether this source wants one at all, and that turns on two
  independent questions — does anyone else have a **location** they can open, and
  do you hold **original bytes** that aren't already the CSV:

  | What you were given | `sourceUrl` | `sourceWorkbook` | Why |
  |---|---|---|---|
  | Google Sheet, exported to xlsx because you needed the formatting | the sheet's URL | the export you parsed | The live sheet keeps changing. The export is the only evidence of what it said when you read it. |
  | Google Sheet read straight to CSV | the sheet's URL | none | The CSV *is* what you parsed, and Dough already keeps every CSV. |
  | `s3://` or a shared drive holding a workbook | the `s3://` path | the downloaded workbook | The object can be replaced in place, and the path is often unreadable to whoever asks later. |
  | `s3://` holding a CSV | the `s3://` path | none | Same as the second row — don't upload a copy of the CSV. |
  | A local `.xlsx` someone sent you | none — a laptop path is not a location | the workbook | Nobody else can open `/Users/me/Downloads/plan.xlsx`. The bytes are all there is. |

  Note the two middle rows: **the source's kind never decides this.** The same
  gsheet, the same bucket, goes either way depending on what you actually parsed.
  A source with no URL and no workbook is fine — that is a hand-built CSV, and
  `sourceLabel` carries it alone.
- **With a workbook attached, `sourceLabel` must name the tab and the range.** A
  workbook cannot say which of its sheets became these rows, and a header that
  started at row 10 under a metadata block is invisible once the CSV is cut. This
  is the half of provenance the bytes cannot carry, so the sheet stops being one
  of several things the label might mention and becomes the thing it must.
- When several files feed one table, each upload carries its own label and URL.
  **Tabs of one book all attach that same book**: prepare it once and pass the
  same `objectPath` on each upload — every version then points at the bytes it
  actually came from. A path prepared for a *different* table is refused, so a
  book feeding two tables is uploaded once per table.
- The workbook is **deleted with the table** — don't tell a user it survives
  independently.
- `tables.annotate` after load: **notes** carrying the grain, what was
  excluded and why, source caveats found in step 4, tie-out waivers, and
  unconfirmed assumptions (currency, draft-vs-final). Caveats live in notes,
  never as extra data columns.
- An **alias** only when the team has a name for this data ("the marketing
  plan" → `marketing_plan`) — table-name-shaped (letters, numbers,
  underscores) and different from the table's real name, or annotate rejects
  it. If nobody has named it yet, record no alias rather than inventing one.
