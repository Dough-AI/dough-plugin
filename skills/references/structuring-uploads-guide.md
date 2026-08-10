# Reading structure out of human-formatted sources

Pathology catalog for the uploads skill, step 1. Each entry: the pattern, and
what to do about it. Scan for the ones your source exhibits.

## Formatting is structure
In a finance spreadsheet, formatting carries meaning that cell values don't:

- **Bold row preceded by a blank row** = a subtotal for the block above it.
- **Bold row directly after a detail row** = a line item that happens to be
  bold (emphasis, a late addition) — it is data, not a subtotal. Getting these
  two backwards either double-counts or drops rows.
- **Section labels in a side column** with no numbers of their own announce a
  category; the rows beneath belong to it until the next label.
- **Indentation** (leading spaces in labels) usually encodes hierarchy depth.
- Subcategory names often exist *only* in subtotal labels ("Search Subtotal")
  — detail rows must inherit their subcategory from the subtotal that covers
  them, not from their own text.

These cues survive in xlsx and die in CSV export — when both exist, parse the
xlsx. Load with `openpyxl` twice: `data_only=True` for values, default for
formulas (see next entry).

And **keep the xlsx you parsed**: upload it with the table as `sourceWorkbook`
(uploads step 5). Everything in this file is an argument that the workbook holds
structure the CSV cannot — which makes shipping only the CSV a decision to
destroy the evidence for every judgement call you are about to make. Whoever
re-checks a number later needs the same bold rows and formula ranges you had.

## Formula ranges encode group membership
A subtotal cell like `=SUM(C6:C12)` is the sheet author telling you exactly
which rows belong to that group. After assigning detail rows to categories/
subcategories from formatting cues, verify every assignment against the
subtotal formulas' ranges. This catches misread section boundaries immediately
and is cheaper than debugging a tie-out failure later.

## Summary cells that lie
Cells in a summary block may **link to another tab** (often actuals) instead
of summing their own detail — e.g. a closed month's column repointed at the
actuals workbook, sometimes flagged only by a footnote. Symptoms: one column's
summary disagrees with detail while all others tie; deltas against that
column read 0.00. The true figure must be inferred from the detail/subtotal
rows, and the discrepancy reported to the user — their sheet's totals are a
hybrid, and your parsed number will "contradict" it.

## Pivot tabs export without values
A pivot table's cells often come through empty or stale in exports. Recover
the definition — row fields, column fields, aggregation, source range — from
the pivot spec, not the rendered cells.

## Pasted dumps go stale
A tab pasted incrementally from another system (each close, someone pastes the
new month) misses entries created or reclassified after the paste. When a
connected system of record covers the same data, reconcile against it and
report gaps as findings — the lake, not the sheet, is authoritative.

## Precision
Plan amounts are often annual ÷ 12 — repeating decimals. Rounding to 2dp
before aggregating drifts totals (a real case: $0.10 off a $1.97M grand
total, enough to fail a to-the-penny tie-out). Keep full stored precision
through parse, comparison, and upload; rounding is for display only.

## Assorted traps
- **Zero vs. blank:** blank month cells on seasonal items are absences, not
  zeros — they become no row at all (see the shape rules in SKILL.md).
- **Merged or two-row headers:** unmerge mentally before mapping columns;
  the first data row is often further down than it looks.
- **Notes squatting in data columns:** a trailing text column (or stray cell
  in a numeric column) holding commentary — route it to the commentary
  column or table notes, don't let it break type validation.
- **Derived columns** (an FY total column summing the months) are
  verification targets like derived rows — never loaded.
