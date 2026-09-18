---
name: agent-gym
description: Score an agent against periods a person already worked by hand, and iterate until every difference is explained. Use when setting up or running an eval for a Dough agent, when someone asks whether an agent's output is right, when replacing a spreadsheet process someone runs every week, month or quarter, when comparing an agent's workbook or sheet against the one a person built, or when they mention a bridge, a holdout, a reference period, or an eval report.
---

# Agent gym

Someone has a process they run every period out of a spreadsheet: a workbook or
a Google Sheet, worked the same way each time. An agent that takes it over is
only trustworthy if it reproduces the periods that person already produced. The
gym scores it against those periods and keeps the evidence.

**Period means whatever the process runs on** — a week, a month, a quarter. The
examples here are monthly because the first agents were, but nothing in the loop
assumes it: a period is just the label in `eval_set`, passed to the build command
and to every path as `{period}`. Use whatever the process uses (`2026-w32`,
`2026-08`, `2026-Q3`), and keep it sortable, because the order of the eval set is
the order of training.

Four words carry the whole thing:

| Term | Meaning |
|---|---|
| **reference** | The human's output for one period — the workbook or sheet they produced |
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

**1. Find the references and freeze them.** Ask where the person's past runs
live — the last several weeks, months or quarters of whatever they produce, and
how far back they go. Copy them somewhere **outside the agent folder**, one
folder per period, with a `references.yaml` recording each file's sha256, its
origin path and when
it was copied. Two reasons: a run can then never read one, and client files
never end up inside something that might be published.

**2. Count the periods you have.** The minimum held back is
`min(ceil(N/2), 2)` — two holdouts once there are three or more references, one
when there are two. Say what that buys: with one reference there is no
holdout at all, and the report should state that no generalisation is claimed.

**3. Read one reference — a development period, never a holdout.** Open it and
list what it actually asserts: the figures a reviewer would check, the
populations behind them, and — just as important — **where each input came
from**. Tabs pasted from an export, a CSV someone drops in, a figure typed from
another system: write them down, they are step 4. Then read the candidate the
agent produces. The two layouts will differ; that is expected, and it is why the
mapping is declared.

**4. Sort out the inputs, period by period.** An eval reruns a past period, so
every input has to be available *as it was then*. Take the list from step 3 and
put each one in a bucket:

- **In the data lake.** The best case: the agent queries it per period. See
  step 5 — do not assume the answer, go and look.
- **A file the person supplies each period** (a CSV export, a JSON drop, a
  workbook from another team). Freeze one copy per period inside the agent at
  `inputs/<period>/<name>`, exactly as it arrived, and have the build read that
  path. Record where each file came from and when it was taken, next to them.
  Anything downloaded fresh today is *not* what that period ran on.
- **Typed by hand** (a rate, a roster, a threshold). That is configuration, not
  input: put it in the agent's rules file where a reviewer can see it, and say
  so when it changes.

Two traps worth naming: a file re-exported today can contain corrections made
after the period closed, which makes the agent look wrong when the reference was
right; and an input with no period in its path quietly gets reused for every
period, so every candidate is built from the same data.

**5. Ask the lake before accepting a manual input.** For each input, check
whether Dough already has it, and say what you found:

- `queries.list` first — an existing saved query may already return it, and
  reusing it keeps the numbers consistent with the rest of the org.
- Then `integrations.tables` and `integrations.describe` for the tables behind
  it, and `integrations.query` to test a period against the file the person
  uses. If the totals tie, propose replacing the manual input with a saved
  query, and show the comparison that justifies it.
- If they do not tie, say by how much and keep the file. A partial match is a
  finding about the lake, not a reason to switch.

Where you do create one: write the SQL with `integrations.query`, parameterise
the period, verify it against a period whose answer you already know, then
`queries.save` it and reference it by **id** from the agent — never SQL copied
into the agent folder. A saved query is the org's shared asset: editing it
changes everyone's numbers, and there is a budget — see guardrail 2.

**6. Propose the components, and ask before writing them.** For each: is this
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

**7. Expect a reference's layout to move, and say so per period.** A human
workbook grows a row, drops a tab, gets re-cut. When one period's figure sits
somewhere else, do not weaken the mapping for every period — add an
`overrides:` block to that period's entry in the eval set, naming only what
moved and why:

```yaml
  - period: 2026-01
    role: development
    overrides:
      # the Summary block gained a row in February; January sits one higher
      holding_balance: {reference: {cell: I26}}
```

An override is a record of drift, and it belongs next to the period it explains.

**8. Write `eval/eval.yaml`** and run the EARLIEST development period, not all
of them. Expect to fix the mapping once or twice — a blank figure usually means
the address is wrong, not that the number is missing.

## Three standing guardrails

**1. Aggregation belongs in the sheet, not in SQL — and never remove one that is
already there.** A reviewer's first instinct is to change a row and watch the
totals move. That only works if the totals are live: SUMIFS, COUNTIFS or a pivot
over the data in the same file. So:

- Pull **only the grain the pivots and formulas actually need** — the columns
  and the level of detail the aggregates group by, and no more. Not the lowest
  grain available: a sheet carrying detail nothing reads is slower to refresh,
  heavier to open, and gives a reviewer more to scroll past.
- Compute every subtotal with formulas over whole columns, so any row count a
  refresh produces recomputes without touching a formula.
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

**2. Push the data into saved queries — then stop.** Every input that can come
from the lake should, as a saved query with declared parameters, referenced by
id. That is what makes it re-runnable by anyone, keeps one definition of a
number across the org, and removes a file someone has to remember to export.
Look for the chance actively (setup step 5), rather than accepting the
spreadsheet's inputs as given.

The limit is the other half of the rule: a query per figure is its own mess, and
every one is a shared asset someone else can edit.

- Aim for **one or two saved queries per managed data sheet**, returning what
  that sheet's aggregates need, and derive every figure from them with formulas.
- If a sheet is heading past two, look at what the extras are. Usually they are
  aggregates that belong in formulas, or near-duplicates differing by a filter
  that a parameter could carry.
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
# the loop, over the eval set
uv run --with pyyaml --with openpyxl scripts/eval.py <agent-dir> [--reveal-holdout] [--rebuild] [--all]

# one period on its own
uv run --with pyyaml --with openpyxl scripts/compare.py <agent-dir> <period>
```

The loop: development periods oldest first, **stopping at the first one that
needs disposition** (guardrail 3), then every holdout already revealed
as a regression, then **at most one unseen holdout, last, and only with
`--reveal-holdout`**. A failing regression stops the run before any reveal —
never spend a holdout proving a fix that has not been made.

**A holdout is blind once.** After its bridge has been read it is a regression
test: it shows nothing broke, not that the agent generalises. The report carries
a ledger of how many remain, and says so when none do.

Every build runs in a **staging directory** — a copy of the agent holding only
what that period may see: its own files, `inputs/` up to and including the
period, `output/` strictly before it, and no `eval/` at all. The candidate is
copied back afterwards. So a build cannot read the reference it is about to be
scored against, or a later period's data, by construction rather than by
convention.

## Agent mode: testing the agent, not the script

`build:` runs a script, which tests the script. To test the **agent** — its
CLAUDE.md, its rules, its judgement — hand the staged directory to a subagent
and collect what it produces:

```bash
# 1. stage the period and stop
uv run --with pyyaml --with openpyxl scripts/eval.py <agent-dir> --prepare <period>

# 2. dispatch a subagent whose working directory is the path that printed, with
#    no other context. It reads the agent's CLAUDE.md and builds the output.

# 3. collect it, audit what it read, and bridge it
uv run --with pyyaml --with openpyxl scripts/eval.py <agent-dir> --collect <period> \
  --transcript ~/.claude/projects/<slug>/<session>/subagents/agent-<id>.jsonl
```

The gym cannot dispatch a subagent — only you can — so it stages the work, hands
the directory over, and takes the result back, keeping the books either way.

**The audit is what makes blindness evidence rather than a claim.** Staging is
hygiene, not a wall: every tool takes absolute paths, and an agent asked to
reproduce someone's workbook has a genuine reason to go looking for one. So the
audit reads the run's transcript and reports every reference path the run
*acted* on. Three verdicts, all recorded in the bridge under `blindness`:

- `blind` — nothing outside staging was opened. The result counts.
- `contaminated` — a reference was read while the candidate was being built. The
  candidate is worthless, and if the period was a holdout its blind result is
  gone for good.
- `not audited` — there was no transcript to read. Blindness is **unverified,
  not proven**, and this never exits 0.

Reports land in `<agent>/eval/reports/<run>/`, numbered and never overwritten:
one `<period>.bridge.json` per period plus a `report.json` for the run.

A bridge lists at most 20 differences of each kind, to stay readable when
something has gone badly wrong. The population counts are always complete, and
`truncated_steps` says how many were left out — a bridge with any cannot pass,
however many of the listed ones have been ruled on. When you see one, fix the
cause rather than working through the list: 200 unmatched rows is one problem,
not 200.

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

If a gym upgrade changes how a step id is built, every existing ruling stops
matching and its difference resurfaces. That is the safe direction — nothing is
settled by accident — but do not re-rule from scratch: find the new id for the
same difference and re-point the entry, keeping the reason, note, name and date.
Say in the file that you did.

A `bug` is not a disposition, and ruling one does not settle the period: the run
still fails, reporting it under `open_bugs`. That is deliberate — the fix settles
a bug, not the label. Fix the agent, record what changed and why in
`<agent>/eval/changes.yaml`, and re-run: the next run is the evidence.

## What this does not do yet

Say so plainly rather than implying coverage:

- **Workbooks only.** `kind: workbook` is the only reader; a Google Sheet, a
  JSON summary or a lake table fails loudly with the kinds it knows.
- **The default loop tests the build command, not the agent.** Whatever `build:`
  names is what runs, so the agent's own reading of its instructions is never
  exercised. `report.json` says `mode: script` whatever the command actually
  was, so do not read it as evidence of what ran; a single-period bridge carries
  no `mode` at all. Agent mode (above) is the path that tests the agent, and it
  is driven one period at a time by hand.
- **The audit sees what a transcript records.** It reads tool calls, so work
  done outside one — a person opening the reference and typing a figure in — is
  invisible to it. `not audited` is the honest verdict when there is nothing to
  read, and it is not a failure of the agent.
- **No input parity.** Whether the lake could replace a hand-fetched input is
  not scored.
