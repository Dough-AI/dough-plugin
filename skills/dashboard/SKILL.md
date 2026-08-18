---
name: dashboard
description: Use when building, changing, or refreshing a Dough dashboard — a page of widgets over the org's saved queries that anyone in the org can open at a URL. Also when someone asks to put numbers on a dashboard, chart a result, or share a set of figures with their team. Covers picking a form that fits the data's cardinality and the widget's width, declaring query parameters with defaults, totals that are recomputed rather than summed, and the save → refresh → read-the-result loop.
---

# Building a Dough dashboard

A dashboard is a **stored spec** — JSON naming saved queries, visualisation
types, columns and sizes — that renders at `/dashboards/<id>` for everyone in the
org. It holds no SQL of its own: every widget names a `savedQueryId`, so the
queries come first and the dashboard is built on top of them. A refresh runs those
queries and stores a **snapshot**; opening the page reads the snapshot, not the
lake. For exact tool inputs call `tools.describe`.

## Working rules
Apply these before and during the steps below.

### The datalake skill's discipline comes first
A dashboard is datalake work with an audience. **Invoke the `datalake` skill** and
follow its Working rules — reuse the org's calculated tables and saved queries
before writing new SQL, read a table's aliases and notes, ask when the source is
ambiguous, verify what a column means. Reuse matters more here than anywhere else:
a dashboard is a URL colleagues paste around, so a number that is subtly wrong is
now subtly wrong in public, on a page nobody re-derives.

### Check a dimension's cardinality before choosing a form
`SELECT COUNT(DISTINCT entity) …` costs nothing next to a chart nobody can read.
Run it, then pick:

| Distinct values | Form |
|---|---|
| 1 | Not a breakdown at all — a stat tile (`number` / `currency` / `percent`). A one-category chart is a single bar holding the whole total, which says nothing the tile wouldn't. |
| 2–8, short labels | `bar` |
| Many, or long labels | Roll up to a category, or use a `table`. |

Eight full account names do not fit across a bar chart's labels, and one entity
is not a distribution. There is **no pie type** in the vocabulary, deliberately —
don't plan for one.

**Prefer a category rollup to a raw account list.** It reads better, and it earns
something a long list does not: a rollup can be checked against a control total.
Sum the categories, compare to the unrolled total, and say so.

### Match series density to the width
1,000 rows per widget is the system cap, **not a readable chart**. A line in a
760px-wide widget has sub-pixel spacing well before that: a year of daily data is
365 points and reads as a smear. Ask whether weekly — 52 points — answers the same
question. This is authoring judgement, not something the tools will refuse; the
schema will happily store a spec whose chart is illegible.

`size.cols` (a span out of 12, 2–12) reaches the renderer and sets the chart's
geometry. A narrow widget draws a narrow chart, so give a dense series room.

### Never put a rate and a currency on the same axis
A `line` widget has one `valueFormat` for all of its series, so revenue in USD and
gross margin in percent cannot share one. Even where you could force it, don't:
one series flattens to a line on the floor. Put the rate in its own `percent` stat
tile or its own chart beside the currency one.

### A ratio total is recomputed from its parts, never summed
On twelve real months of one org's P&L, gross profit ÷ revenue over the whole
period is **77.9%**. Averaging the twelve monthly margins gives **77.3%**. Both
look plausible on a page; only one is the gross margin. So in a `table` widget:

```jsonc
"totals": {
  "sum": ["revenue", "gross_profit"],
  "ratio": { "gross_margin": ["gross_profit", "revenue"] }   // numerator, denominator
}
```

`sum` is for currencies and counts. **A rate — margin, growth, share, rate per
unit — always goes in `ratio`**, naming the two columns it is computed from. A
column in both is rejected.

## 1. Get the queries right first
The widget is only as good as the result behind it.

- Reuse before writing: `queries.list` / `queries.get` and the `dough_calculated`
  tables. A dashboard built on the org's own definitions agrees with the rest of
  the org by construction.
- Iterate with `integrations.query` (it takes `params` for `@name` placeholders,
  so a parameterised query can be run exactly as the dashboard will run it) until
  the result is right. **A widget names columns from the result**, so the SELECT's
  column names are the contract — name them from a result you have actually run,
  not one you expect.
- **Order a time series oldest-first.** That is the order a line chart reads left
  to right, and a stat tile's `rowSelector` defaults to `"last"` for exactly that
  reason: the newest period is the last row, and a `delta` compares it against its
  older neighbour. A newest-first result needs `rowSelector: "first"`.
- One widget, one query. Aggregate in SQL — nothing on the page re-aggregates.
- `queries.save` each one with a clear name; it validates read-only and does not
  execute.

## 2. Declare parameters, with defaults
Put the knobs on the **saved query**, in `parameters[]`:

```jsonc
"parameters": [{ "name": "period_start", "type": "DATE", "default": "2025-08-01" }]
```

- Types are `DATE`, `STRING`, `INT64`, `FLOAT64`, `BOOL`. The SQL references them
  as `@period_start` — BigQuery named parameters, never string interpolation.
- **A default is required by the schema, and that is the point:** the query stays
  runnable on its own from `/datalake/queries` and from `integrations.query`, and
  every dashboard that adopts it inherits the knob.
- **Controls bind to parameters by NAME.** A dashboard control named
  `period_start` drives every widget whose query declares `period_start` — no
  wiring, no list to keep in sync. The flip side: a query that spells it
  `start_period` is silently left on its own default. Spell them identically.
- A control's `type` must match the parameter's type; `dashboards.save` rejects
  the mismatch and names both sides.
- Limits: 8 controls, 24 widgets, 4 series per line, 20 columns per table.

## 3. Write the spec
`dashboards.save` — omit `id` to create, pass `id` to update. It is a **partial
update**: send only the fields you are changing, so a rename does not need the
spec resent. The vocabulary:

- **Stat tiles** — `number`, `currency`, `percent`: a `valueColumn`, optional
  `decimals`, `caption`, `rowSelector`, and a `delta` against the previous row.
- **`line`** — `xColumn`, 1–4 `series` (each a `column`, `label` and `tone`), a
  `valueFormat`, `yBaseline`.
- **`bar`** — `labelColumn`, `valueColumn`, `valueFormat`, and an optional `tail`
  naming the row that is a rolled-up remainder (`{ "label": "All other" }`), which
  the renderer sets apart from the real categories.
- **`table`** — `columns` (each with an optional `format`) and `totals`.

Rules the schema enforces, worth knowing before it rejects you:

- Anything `currency` needs a 3-letter uppercase code (`"USD"`).
- `tone` is an **enum** — `accent`, `accent-mid`, `accent-pale`, `muted` — never a
  colour string. The renderer resolves it.
- Titles and labels may not contain `< > { } \`. They are model-authored text that
  reaches a shared page.
- Widget ids are lowercase (`gross_margin`), unique within the dashboard.

## 4. Refresh, then read what came back
The loop, and there is **no dry run**:

> `queries.save` each query → `dashboards.save` the spec → `dashboards.refresh` →
> read the returned `widgets` → fix → refresh again.

Columns are checked against the **first refresh's actual result**. What `refresh`
returns per widget is `{ id, rows, error? }`:

- **`error`** — the query failed, its saved query no longer exists, or the table is
  one this identity may not read. Fix and refresh again.
- **`rows: 0`, no error** — the query ran and returned nothing for these
  parameters. The tile will say "No rows for this period." That is a data or
  parameter problem, not a spec one.
- **What `widgets` will NOT tell you:** a column named in the spec but absent from
  the result is not reported here — the query succeeded and rows came back. It
  errors on the **page** ("The result has no column named gross_margin"), never as
  a `$0`. This is why step 1 says to name columns from a result you ran. After the
  first refresh, open `/dashboards/<id>` and look at it — 4px axis labels and a
  mis-named column are both things only a person looking at the page catches.
- **`written: false`** means your refresh ran but a newer request's snapshot is now
  the current one. Nothing is wrong; you lost a race you didn't need to win.

Pass `params` to refresh with values other than the controls' defaults. A name
that is neither a control nor a query parameter fails the whole refresh and says
so. One snapshot is shared by the whole org, so refreshing with different
parameters changes the figures **for everyone** — check with the user first.

## 5. `alreadyRunning` is not a failure
A refresh runs as a background job, serialised per dashboard. `dashboards.refresh`
still returns the finished result, so the loop above is unchanged — but if a
refresh of that dashboard is already running you get:

```jsonc
{ "refreshed": false, "alreadyRunning": true, "jobId": "…" }
```

**Do not retry, and do not report it as an error.** The running refresh writes to
the same dashboard; a second one would only race it. Call `dashboards.get` and
read `refreshStatus` (`jobId`, `status`, `queuedAt`, `startedAt`, `finishedAt`,
`error`) until it is `succeeded` or `failed`; `latestSnapshot` then reflects the
outcome. A failed refresh never blanks the dashboard — the previous snapshot
stands and the page carries the error.

## 6. Hand over the URL — and what you cannot do
A new dashboard is a **draft**: it lives at `/dashboards/<id>`, anyone in the org
with the link can open it, it is marked Draft, and it is absent from the
`/dashboards` index. Give the user the URL as soon as it exists, so they can look
at the real page while you are still shaping it.

- **There is no publish verb over MCP.** Publishing is a person's decision, made
  from the dashboard's own page header in the app. Say where the button is; do not
  go looking for a tool that publishes.
- **There is no delete verb over MCP** — not for a dashboard, not for a snapshot.
  So don't offer to clean up an old dashboard. A superseded one is **updated by
  id** (`dashboards.save` is a partial update, so replacing the spec or dropping a
  widget is one call), or the person deletes it in the app.
- **Rename is available**: `dashboards.save` with `id` and `name`.
- **Editing a saved query is the likelier break.** `queries.save` over MCP does
  **not** warn you that a dashboard reads that query — that check exists on the web
  page, not on the tool. Rename or drop a column and every widget naming it turns
  into an errored tile among tiles that are still correct — and, per step 4, the
  refresh itself reports nothing. Before editing a shared
  query, check `dashboards.list` / `dashboards.get` for widgets pointing at that
  `savedQueryId`; afterwards, refresh them and look at the page.
- On a workspace whose lake uses delegated (OAuth) credentials, dashboards are
  refused — "Dashboards are not available for this workspace." That is deliberate,
  not a fault to work around.
