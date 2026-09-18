---
name: agent-gym
description: Score an agent against the months a person already closed by hand, and iterate until every difference is explained. Use when setting up or running an eval for a Dough agent, when someone asks whether an agent's output is right, when comparing an agent's workbook or sheet against an accountant's own, or when they mention a bridge, a holdout, a reference month, or an eval report.
---

# Agent gym

An agent that produces a month-end deliverable is only trustworthy if it
reproduces months a person already closed. The gym scores it against those
months and keeps the evidence.

Four words carry the whole thing:

| Term | Meaning |
|---|---|
| **reference** | The human's output for one period — their workbook, sheet or posted entry |
| **candidate** | What the agent produced for that same period |
| **bridge** | The walk from reference to candidate: every difference, with an amount and a reason |
| **disposition** | A person's ruling on one difference, which carries across runs |

A reference is **not** ground truth. Human workbooks in these sessions have
carried a hard-coded zero, a stale hand-keyed balance, and an accrual that was
$52.6K light. When the agent and the reference disagree, either may be wrong.

## Which phase you are in

- **No `eval/eval.yaml` in the agent folder** → Setting up, below.
- **It exists** → Running, below.
- **A run came back with unexplained differences** → Disposing, below.

---

## Setting up

The goal is a short `eval/eval.yaml` that says where the same fact lives on each
side. Do not write it from imagination — read both artefacts first.

**1. Find the references and freeze them.** Ask where the human's past months
live. Copy them somewhere **outside the agent folder**, one folder per period,
with a `references.yaml` recording each file's sha256, its origin path and when
it was copied. Two reasons: a run can then never read one, and client files
never end up inside something that might be published.

**2. Check how many periods you have.** The minimum held back is
`min(ceil(N/2), 2)` — two holdouts once there are four or more references, one
when there are two or three. Say what that buys: with one reference there is no
holdout at all, and the report should state that no generalisation is claimed.

**3. Read one reference — a development period, never a holdout.** Open it and
list what it actually asserts: the figures a reviewer would check, and the
populations behind them. Then read the candidate the agent produces. The two
layouts will differ; that is expected, and it is why the mapping is declared.

**4. Propose the components, and ask before writing them.** For each: is this
the figure that matters, and is the reference authoritative for it? A component
is either a `figure` (one number, by cell or by label lookup) or `rows` (a
population, keyed). Ask specifically about:
- **the key** — what makes a row unique; duplicates are ordinary, so the gym
  adds an occurrence index automatically;
- **a filter** — the candidate usually carries more rows than the population
  under test (every line it looked at, not only the ones it acted on);
- **fields** — a row can be in both populations, at the same amount, and still
  disagree (the same charge reclassified to a different account). Declare those
  columns or that difference is invisible;
- **tolerance** — `exact`, or `within` an amount.

**5. Saved queries.** If the agent pulls from the lake, its queries belong in
Dough as saved queries with declared parameters, referenced by id — never SQL
copied into the agent. If they do not exist yet, write the SQL with
`integrations.query`, verify it for one period, then `queries.save` it and put
the id in the agent's config. Explain that a saved query is the org's shared
asset: editing it changes everyone's numbers, and there is a budget — see
guardrail 2.

**6. Write `eval/eval.yaml`** and run the EARLIEST development period, not all
of them. Expect to fix the
mapping once or twice — a blank figure usually means the address is wrong, not
that the number is missing.

## Three standing guardrails

**1. Aggregation belongs in the sheet, not in SQL — and never remove one that is
already there.** A reviewer's first instinct is to change a row and watch the
totals move. That only works if the totals are live: SUMIFS, COUNTIFS or a pivot
over the data in the same file. So:

- Pull the lowest grain the deliverable needs into the data sheets, and compute
  every subtotal with formulas over whole columns, so a refresh of any row count
  recomputes without touching a formula.
- **Keep the aggregates the accountant already built.** If their workbook has a
  pivot or a block of COUNTIFS, reproduce that shape rather than replacing it
  with a value the agent computed. Losing it is a real regression to them even
  when every figure agrees.
- Treat a candidate that hard-codes a figure the reference computed as a
  finding, not a convenience. The bridge will show the numbers matching; what it
  cannot see is that one side stopped recalculating.
- A pivot cache does not recompute on file write, so if a pivot has to stay,
  say plainly that it needs opening in Excel to refresh — do not quote a figure
  read from a stale cache.

**2. Saved queries: enough, and no more.** Push the SQL into Dough as saved
queries with declared parameters — that is what makes it re-runnable by anyone
and stops SQL being copied into the agent. But a query per figure is its own
mess: every one is a shared org asset someone else can edit.

- Aim for **one saved query per managed data sheet**, at the lowest grain that
  sheet needs, and derive every figure from it with formulas.
- A close of this size lands at roughly four to six. If a workbook is heading
  past that, the extra queries are usually aggregates that belong in formulas,
  or near-duplicates that differ by a filter a parameter could carry.
- Before adding one, check `queries.list` for an existing query that already
  covers it. Reuse keeps the org's numbers consistent; a second query with a
  slightly different filter is how two teams end up with two revenue figures.

**3. Train one period at a time, in order.** Run the earliest period, settle
what it shows, then open the next — week 1 and its result, then week 2, and on
until the holdouts. `eval.py` stops at the first development period that needs
disposition for exactly this reason; `--all` overrides it for a regression sweep
of months already settled.

Running six months at once and fixing everything together produces rules fitted
to all six, and none of them was ever a test. Taking them in order means each
period is a small, honest check on what the last one taught you — and it is what
has generalised best in practice.

## Running

```bash
uv run --with pyyaml --with openpyxl scripts/eval.py <agent-dir> [--reveal-holdout] [--rebuild] [--all]
uv run --with pyyaml --with openpyxl scripts/compare.py <agent-dir> <period>   # one period
```

The loop: development periods oldest first, **stopping at the first one that
needs disposition** (guardrail 3), then every holdout already revealed
as a regression, then **at most one unseen holdout, last, and only with
`--reveal-holdout`**. A failing regression stops the run before any reveal —
never spend a holdout proving a fix that has not been made.

**A holdout is blind once.** After its bridge has been read it is a regression
test: it shows nothing broke, not that the agent generalises. The report carries
a ledger of how many remain, and says so when none do.

Reports land in `<agent>/eval/reports/<run>/`, numbered and never overwritten:
one `<period>.bridge.json` per period plus a `report.json` for the run.

## Reading a bridge

Report the differences, not the verdict. For each one say what it is worth, on
which line, and which side looks wrong. Then propose a reason:

| Reason | Means |
|---|---|
| `judgment` | The person decided differently, and that is allowed |
| `reference_error` | The human's workbook is wrong |
| `stale_data` | The sources moved after the reference was built |
| `bug` | The agent is wrong — fix it rather than accept it |

**A clean run proves nothing on its own.** Before reporting a pass, say what
would have failed. If nothing would, the comparison is not testing anything:
perturb one input and confirm the bridge reports that exact amount.

Watch for differences that are one fact counted twice — a row-level step and the
total that contains it. Say so rather than presenting two findings.

## Disposing

An unexplained difference fails its period. Each run writes
`<period>.undisposed.yaml` listing every one, pre-filled with its id.

Work through them **with the person**, one at a time, proposing a reason and the
evidence for it. Never fill in `accepted_by` yourself: the name is the point.
Move the completed entries into `<agent>/eval/dispositions.yaml` and re-run — the
ruled differences stay visible in the bridge but no longer fail it.

A ruling covers **that** difference, not that line for ever. The id includes the
amount, so if the same line later differs by a different amount it resurfaces.
That is deliberate; do not edit ids to make it go away.

A `bug` is not a disposition. Fix the agent, record what changed and why in
`<agent>/eval/changes.yaml`, and re-run: the next run is the evidence.

## What this does not do yet

Say so plainly rather than implying coverage:

- **Workbooks only.** `kind: workbook` is the only reader; a Google Sheet, a
  JSON summary or a lake table fails loudly with the kinds it knows.
- **Script mode only.** It runs the agent's build command. It does not exercise
  the agent's own judgment, and every report says `mode: script`.
- **Blindness is a convention here, not a wall.** Keep references outside the
  workspace; nothing yet stops a build from reading one.
- **No input parity.** Whether the lake could replace a hand-fetched input is
  not scored.
