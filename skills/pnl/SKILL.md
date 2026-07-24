---
name: pnl
description: Use when presenting a P&L / income statement / profit-and-loss / earnings / "the financials" — anything with revenue down to net income, gross/operating/net margin, or an actual-vs-budget comparison. Enforces line-item order, sign conventions, subtotals, margins, and comparative columns for a consistent statement.
---

# Presenting a P&L

Consistent income-statement presentation. A P&L is datalake work. **Invoke the `datalake` skill first** and follow its
Working rules (reuse-first, ask-when-ambiguous, tables-first, verify-the-data) to
source the numbers — don't re-derive them here. Reuse-first matters most for a
P&L: an income statement is exactly the kind of thing an org has already built as
a calculated table or a saved query, so look there before writing SQL against raw
tables. This skill adds the P&L-specific presentation conventions on top.

## Compute it right before you present it
A P&L is only as good as its inputs — get these right before formatting anything.

### Sign convention (the #1 cause of wrong P&Ls)
Establish the amount's sign convention before you sum. Amounts are often stored as
**unsigned magnitudes** — the positive value of whichever of debit/credit applies —
with direction implied by a debit/credit split or the account's normal balance. A
plain `SUM` then adds inflows and outflows as the **same sign** and is meaningless
(neither a total nor net income). Do one:
- **Use a pre-signed amount column** if one exists — but verify it is actually
  signed (check for negative values) and that its magnitude matches the unsigned
  column.
- **Otherwise sign by normal balance** per account type: credit-normal (Revenue,
  Other income) = inflow **(+)**; debit-normal (COGS, Operating expenses, Other
  expense) = outflow **(−)**. Then Net income = Σ(signed) = revenues − expenses.

Confirm the convention holds on the data (every row should match exactly one
normal-balance formula), and state which column/convention you used.

### Scope: income-statement accounts only
A P&L covers income-statement accounts. **Exclude balance-sheet account types
(Asset, Liability, Equity)**; keep Revenue, Other income, COGS, Operating expenses,
Other expense. Filter to P&L account types before totaling.

### Hygiene
- Exclude deleted / voided rows (defensively, even if there are none today).
- Keep reversing entries but **flag** them (e.g. `is_reversal`) rather than dropping —
  they are legitimate, but worth tracing.
- Surface review flags (e.g. `needs_review`) so unreviewed rows can be filtered
  downstream.
- Know how intercompany is handled — a dedicated **Eliminations** entity nets it
  structurally, versus a flag you apply — so you don't double-count.

## Structure & order
Lay the statement out top-to-bottom in this multi-step order — the operating vs.
non-operating split is the point of the format:

| Line | Notes |
|---|---|
| **Revenue** | by line item |
| Cost of revenue / COGS | |
| **Gross profit** | subtotal (bold) — show gross margin % |
| Operating expenses | grouped: Sales & Marketing, R&D, G&A (excl. D&A) |
| **EBITDA** | subtotal — show EBITDA margin % *(include only if D&A is broken out)* |
| Depreciation & amortization | |
| **Operating income (EBIT)** | subtotal (bold) — show operating margin % |
| Other income / (expense) | non-operating: interest, FX, gains/losses |
| **Pre-tax income** | subtotal |
| Income tax | |
| **Net income** | bottom line (bold) — show net margin % |

## Ordering within a section
- **Revenue:** largest → smallest by amount, unless the org has a canonical order
  (e.g. product lines, then services). Keep the order **stable across periods** —
  never reorder period to period.
- **Expenses:** COGS before OpEx; within OpEx, group by function, largest →
  smallest inside each group.

## Numbers & signs
- Costs, expenses, losses, and any negative subtotal → **parentheses** `(1,234)`.
  Never a leading minus sign; never mix the two in one statement.
- State the unit once (e.g. "$ in thousands"); use thousands separators and
  consistent decimals (0 for a whole-dollar management P&L).
- Zero → `—`. Margins and percentages → 1 decimal place + `%`.
- Right-align numbers, left-align labels, indent sub-items under their subtotal.

## Comparative columns
When comparing periods or plan, use: **Actual │ Budget-or-Prior │ Variance ($) │
Variance (%)**.
- Compute variance on the **signed** values (expenses are negative), as
  `Actual − comparison`. Because expenses are already negative, the variance sign
  then encodes favorability on **every** line: **positive = favorable, negative
  (in parentheses) = unfavorable** — no per-line favorability logic needed.
- Variance % = variance ÷ |comparison|, shown in parentheses when unfavorable.
- Add a **% of revenue** (common-size) column when it aids comparison.

## Example (conventions in one view)
```
$ in thousands              Q3 Actual   Q3 Budget   Var ($)   Var (%)
Revenue                        12,400      12,000        400     3.3%
Cost of revenue               (4,100)     (3,900)       (200)   (5.1%)
Gross profit                    8,300       8,100        200     2.5%
  Gross margin %                66.9%       67.5%
Sales & marketing             (3,200)     (3,000)       (200)   (6.7%)
R&D                           (1,800)     (1,750)        (50)   (2.9%)
G&A                           (1,100)     (1,050)        (50)   (4.8%)
Operating income (EBIT)         2,200       2,300       (100)   (4.3%)
  Operating margin %            17.7%       19.2%
```
