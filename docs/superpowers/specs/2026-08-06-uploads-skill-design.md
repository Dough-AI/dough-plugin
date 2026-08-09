# Design: `uploads` skill — structuring data before it becomes an uploaded table

**Date:** 2026-08-06
**Status:** Approved design, pre-implementation
**Source material:** Product OPEX session transcript 2026-08-04 (Little Spoon), where
two plan tables were parsed out of a human-formatted Google Sheet, tied out to the
penny, and loaded via `tables.upload` — all without skill support.

## Problem

The plugin's `datalake` skill (section 1b) covers the *mechanics* of
`tables.upload`: inline CSV, `mode`, `columnTypes`, polling `tables.status` with
`kind:"uploaded"`. Nothing covers the work that actually dominated the transcript:
reading the implicit structure of a human-formatted source (sections in a side
column, bold subtotal rows, summary cells linked to actuals), deciding the target
table shape, converting wide layouts to long rows at full precision, and
reconciling the parsed result against the source's own totals before loading.
Users loading financial projections or sales-related data get no guidance on how
the table should be shaped, so structurally incompatible or silently wrong tables
are one unlucky session away.

## Goal

A skill that guides the full arc — parse → shape → verify — for user-provided
data headed into `dough_uploaded`, primarily financial projections (budgets,
operating plans, latest estimates) and sales-related data (projections, pipeline
exports).

## Decisions made during brainstorming

- **Full arc**, not just schema conventions or a question protocol.
- **New standalone skill** named `uploads`, sibling of `datalake`/`excel`/`pnl`/
  `propose`; `datalake` 1b stays as the tool mechanics and gains a pointer.
- **Sources:** xlsx workbooks, CSV/pasted data, and data parsed from documents
  (invoices, PDFs). No dependency on a Drive connector.
- **Canonical schema + principles** — a default shape Claude adapts, with the
  reasoning exposed so unlike data is shaped by the same logic.
- **Verification is a gate but proportional** — full tie-out for large/
  interpretive/multi-table parses; a grand-total check for small simple ones;
  nothing for the clean-CSV fast path.
- **No formal pre-upload checkpoint** — questions are raised conversationally as
  they arise; upload proceeds once the applicable verification passes.
- **Plan versions (OP vs. latest estimates): ask per case** — present
  separate-table-per-snapshot vs. one-table-with-scenario-column with trade-offs
  each time; no default.
- **Pure instructions** — no shipped parser or checker scripts; parsing code is
  bespoke per session.
- **Organization:** lean `SKILL.md` + on-demand heuristics reference file
  (matches the `datalake` + `references/` pattern).

## Files

```
skills/uploads/SKILL.md                            (new)
skills/references/structuring-uploads-guide.md     (new)
skills/datalake/SKILL.md                           (one pointer line in section 1b)
.claude-plugin/plugin.json                         (version bump)
.claude-plugin/marketplace.json                    (version bump, per UPDATING.md)
```

### Trigger description (frontmatter of `skills/uploads/SKILL.md`)

> Use when loading user-provided data into Dough as an uploaded table — a budget,
> forecast, latest estimate, operating plan, sales projection, pipeline export, or
> anything parsed from a spreadsheet, CSV, or document — and the table's structure
> isn't already decided. Covers reading the implicit structure of human-formatted
> sources, converging on a canonical long-format schema, reconciling parsed rows
> against the source's own totals before loading, and recording names, aliases,
> and caveats.

## SKILL.md body: the five-stage arc

Written in the plugin's house style: numbered arc, working rules stated where they
bind, tool mechanics deferred to `datalake` 1b rather than duplicated.

### Stage 0 — Fast path

If the data is already tidy — clean CSV, obvious grain, no summary rows mixed into
detail — confirm column types and meaning with the user and upload directly.
There is no tie-out on the fast path: nothing was parsed or reshaped, so there is
nothing to reconcile against. The rest of the skill is for human-formatted
sources.

### Stage 1 — Understand the source

Inventory what was provided: tabs, regions within tabs, or data extracted from a
document. Classify each region as detail rows, subtotal/summary rows, pivot
output, or commentary. Identify the *authoritative detail* region; derived
regions (subtotals, summary blocks) become verification targets for stage 4,
never data. Load `references/structuring-uploads-guide.md` here when the source
is a formatted spreadsheet or an interpreted document. When more than one tab or
region could plausibly be the source of truth, ask instead of guessing (the
transcript's user twice corrected course: which tab, and June-onward vs.
July-onward).

### Stage 2 — Decide the target shape

Apply the canonical schema (below) and state the grain in one sentence ("one row
per vendor per month"). Raise structural questions conversationally as they
arise — no formal gate:

- Period scope (which months/years).
- Exclusion rules (the transcript's null-safe Wurawe exclusion is the exemplar).
- What the table will join against — align dimension spellings with actuals now,
  or record the mismatch deliberately.
- Plan versions: separate table per snapshot vs. one table with a scenario
  column — present both with trade-offs each time; no default.

### Stage 3 — Parse and reshape

Bespoke code per session; no shipped parser. Standing rules:

- Full precision end to end; never round before aggregating.
- Wide month columns melt into long rows.
- Subtotal and derived rows are excluded from data but kept aside for stage 4.
- The source's own names are preserved verbatim at every dimension level.
- Commentary is attached deliberately (ask how the user wants it) rather than
  dropped silently.

### Stage 4 — Verify (proportional tie-out)

Effort scales with the blast radius of what was parsed:

- **Small, simple parses** — one tab, one region, tens of rows, no interpretive
  calls: check the grand total against the source (plus a per-period total if the
  source shows one), then proceed. Don't build a reconciliation harness for a
  20-row budget.
- **Full tie-out** — required when any of: multi-tab or multi-table upload
  session; hundreds of rows; interpretive parsing (subtotal-vs-detail
  heuristics, section boundaries, inferred values); or data others will build
  workbooks/BvA analyses on. Every derived figure the source offers becomes a
  verification target — grand total, each subtotal row, each summary-block
  figure, per-period totals — and each must equal the sum of parsed detail
  exactly, with row counts checked where knowable.

At either tier: comparisons run on full-precision values, and the gate holds — a
checked total that doesn't tie is investigated and either resolved or explicitly
waived by the user before `tables.upload` is called. Order of suspicion on
failure: first the parse (misclassified rows, dropped rows, misread section
boundary), then the source (stale pasted dumps, cells linked to actuals).
Source-side failures are findings to report to the user — like the transcript's
missing $23,458.65 — not errors to silently absorb. The skill states its tier
decision plainly ("small parse: verifying grand total only") so the user can ask
for the full treatment.

### Stage 5 — Upload and record

Defer to `datalake` 1b for `tables.upload` mechanics. This skill adds the
conventions:

- BigQuery-safe table name; human display name recorded in table notes.
- Caveats recorded via `tables.annotate` notes (e.g. "June column is estimates,
  not actuals"), including any tie-out waivers and acknowledged source
  discrepancies from stage 4.
- An alias for the name the team actually uses.
- `columnTypes` declared for every column.

## Canonical schema and principles

Default target for planning/projection data, adapted per case:

| Column | Type | Notes |
|---|---|---|
| dimension hierarchy | STRING × N | One column per level, broadest → narrowest (e.g. `category`, `subcategory`, `vendor`). The narrowest level is the table's grain; every parent level appears as a sibling column on every row. |
| `period` | DATE | End-of-month for monthly data |
| `amount` | FLOAT64/NUMERIC | Full precision, never pre-rounded |
| commentary column | STRING | Only if the source carries commentary worth keeping |

There is no privileged `line_item` concept — in the transcript that was simply
the lowest dimension level (per-vendor grain). The rule: **capture the source's
lowest-level dimension plus all of its parent dimensions as sibling columns.**

Principles (stated in the skill so unlike data is shaped by the same logic):

- **Lowest grain, full parent chain.** Never load only subtotal-level rows when
  detail exists; never orphan a detail row from its parents.
- **Output measures only, not calculation inputs.** If the source derives
  revenue as price × quantity, load revenue at the lowest grain — not price and
  quantity columns. The table records outcomes at a grain, not the model that
  produced them.
- **Long over wide.** Wide is a presentation format; long is a query format.
- **The grain must be statable in one sentence.**
- **Periods are real DATEs**, not strings, so they join and sort against actuals.
- **Derived rows are not data** — they are verification targets.
- **Names align with what they'll join against**, or the mismatch is recorded
  deliberately.
- **Caveats live in table notes, not in the data.**

## Heuristics reference: `skills/references/structuring-uploads-guide.md`

Loaded only at stage 1 for formatted/interpreted sources. A catalog of source
pathologies — each entry a recognizable pattern plus how to handle it, kept terse
enough to scan mid-session. Seed content (from the transcript, written to grow):

- **Formatting is structure.** Bold row preceded by a blank row = subcategory
  subtotal; bold row directly after a detail row = a bold line item. Sections
  announced in a side column. Indentation as hierarchy. These cues survive in
  xlsx and die in CSV export — prefer the xlsx when both exist.
- **Summary cells that lie.** Cells that link to other tabs (June estimate cells
  pointing at actuals) rather than holding their own values; the true figure
  must be inferred from subtotal/detail rows.
- **Pivot tabs export without values.** Recover rows/columns/aggregation from
  the pivot definition, not the cells.
- **Pasted dumps go stale.** Incrementally pasted tabs miss later entries and
  reclasses; reconcile against the system of record when one is connected and
  report gaps as findings.
- **Assorted:** zero-month cells, repeating decimals (annual ÷ 12), merged
  headers, two-row headers, notes squatting in data columns.
- **The precision cautionary tale:** 2dp rounding drifted a grand total by
  $0.10; rule — keep full precision, round only at presentation.

## Changes outside the new skill

- `skills/datalake/SKILL.md` section 1b: add one pointer line — "if the source
  is a spreadsheet or document whose structure isn't settled, use the `uploads`
  skill first." Datalake's frontmatter description is untouched.
- `.claude-plugin/plugin.json`: version bump; marketplace entry per
  `UPDATING.md`.

## Out of scope

- No shipped scripts (parsing and tie-out code is bespoke per session).
- No Drive/Sheets connector dependency.
- No changes to `excel`, `pnl`, or `propose` skills.
- Post-upload analysis and workbook building (covered by `datalake` / `excel`).

## Validation

No scripts ship, so no unit tests. Validation is a walkthrough:

1. Replay the transcript's "2026 Operating Plan Detail" parse against the skill
   and confirm it produces the same decisions (schema, subtotal handling,
   full-precision tie-outs, notes/alias conventions).
2. One clean-CSV case confirming the fast path uploads directly without
   over-verifying.

Authoring follows `superpowers:writing-skills` conventions at implementation
time.
