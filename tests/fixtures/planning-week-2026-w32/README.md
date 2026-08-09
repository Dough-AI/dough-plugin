# Planning week 2026-W32 — a multi-file upload fixture

Three scenario extracts from one FP&A planning week, of the shape a finance team
actually produces: one gsheet tab per scenario, exported separately, landing as
separate files that all belong in **one** lake table.

They are not arbitrary. Each difference between them exists to make a specific
failure reachable, so a skill that handles multi-file uploads can be tested
rather than asserted.

| File | Columns | Rows |
|---|---|---|
| `fy26h2_plan_base.csv` | period, cost_center, account, amount, **headcount** | 15 |
| `fy26h2_plan_upside.csv` | period, cost_center, account, amount, **headcount** | 15 |
| `fy26h2_plan_downside.csv` | period, cost_center, account, amount, **risk_note** | 15 |

All three cover the same three periods (Jul–Sep 2026) and the same five
cost-centre/account pairs, because that is what makes them comparable — and what
makes them collide.

## The two failures a naive one-file-at-a-time flow walks into

**1. Duplicate keys, on the second file.** The obvious key for planning data is
`(period, cost_center, account)`. Under that key, every row of `upside` collides
with a row of `base` — same period, same cost centre, same account, different
number. That is the whole point of a scenario. The second upload is rejected.

The fix is a discriminator column, and note where it lives: **the scenario is
not in the data.** It is in the file name, or the gsheet tab name. Nothing in
`fy26h2_plan_upside.csv` says "upside". So the column has to be *synthesised*
from the file's identity before upload, and the key has to include it.

`_dough_source` does not solve this. It records origin per row, but it is free
text stamped by Dough, not a column you can declare as part of a key.

**2. Add-and-omit, on the third file.** `downside` omits `headcount` (nobody
modelled hiring for it) and adds `risk_note` (someone added commentary later).
Both are ordinary things to happen between one export and the next.

Upload the files in order, creating the table from `base`, and the third upload
brings a new column *and* drops a known one — the shape Dough refuses because it
cannot tell a rename from a deliberate drop-plus-add. Rejected, and re-sending
it fails identically.

The fix is to compute the **union of columns across all files first** — period,
cost_center, account, amount, headcount, risk_note, plus the synthesised
scenario — and create the table with all of them. Every subsequent file then
only *omits* columns, which is allowed, and never adds one.

That is why "list all the columns across the files" belongs in the flow before
the first upload rather than after the first failure.

## What a correct run looks like

1. Convert each source to CSV.
2. Read all three headers; the union is
   `period, cost_center, account, amount, headcount, risk_note`.
3. Notice the three files share a natural key and would collide; add `scenario`,
   taken from each file's identity.
4. Confirm with the person that these three really are the same table — that is
   a judgement about meaning, not something a header comparison can settle.
5. Create from the first file with `keyColumns: [period, cost_center, account,
   scenario]` and every union column present.
6. Append the rest, **one file per upload**, each with its own `sourceLabel` and
   `sourceUrl` pointing at the gsheet it came from — so a row's origin stays
   recoverable per scenario rather than collapsing to "the planning week".

Step 6 is the reason to upload one file at a time rather than concatenating them
locally: concatenation would give every row the same provenance, and the per-file
pointer is the thing that makes "where did this number come from" answerable.

## Deliberately not included

A file with a genuinely mistyped header. That case is already covered by
`tests/test_upload_e2e.py`, and mixing it in here would confound "these files
legitimately differ" with "this file is wrong" — which is precisely the
distinction the union step exists to make.
