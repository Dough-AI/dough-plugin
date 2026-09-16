---
name: propose
description: Use when something needs to be WRITTEN to a system Dough is connected to — creating or editing an account, a customer, a vendor or an item; raising an invoice, a transfer, a purchase order or a journal entry; an accrual, a reclass, a correcting or adjusting entry — or any time you are about to tell someone to go and make that change by hand in QuickBooks, NetSuite or whatever system they use. Raises the change for human approval instead of performing it; nothing is written until a person approves.
---

# Proposing a write

Dough writes to a customer's books only after a human approves. `proposals.propose`
is how a change gets raised; it does **not** perform the write. For exact inputs
call `tools.describe("proposals.propose")` — the descriptor carries the currently
proposable actions and the payload shape for each.

**With evidence attached:** `/dough:propose` runs this same flow and additionally
uploads the session transcript and the files behind the entry, so an approver can
see what the numbers came from. Prefer it when the reasoning matters. The bytes
travel out of band — never paste file contents into the tool call.

**The moment this applies:** you have worked out a change and are about to say
"you'll need to make this one by hand" — in QuickBooks, in NetSuite, wherever the
record lives. Propose it instead. Telling a person to retype numbers you already
have is the failure this replaces — they will retype them slightly wrong, and
nothing records why the entry was made.

## Working rules

### Ask the server what is proposable — never assume
Your first call is `tools.describe("proposals.propose")`. It returns the
`(target, kind)` pairs that can be proposed **right now** and documents each
payload. The shapes do not agree across targets — the same journal entry line
expresses its side, and even its amount's *type*, differently in each — and a
wrong shape is refused rather than coerced. Read the doc for the pair you are
proposing; do not carry one over from another target or another session.

This plugin is pinned on disk and the catalog moves without it. A shape you
remember from a previous session may be stale; the descriptor never is.

### The `rationale` is what they decide from
Say **why this write**, and **what you based it on** — the source, the period, the
filter. It is the only prose an approver gets, and it is never posted. Compare:

> ✗ "September accrual."
> ✓ "September contractor invoices totalling $8,400.50 arrived after close.
>    Accruing so the month reflects the expense. From `bill.vendor_invoices`,
>    invoices dated 2026-09-01..30 with no matching payment."

If you cannot write the second kind, you do not yet understand the change well
enough to propose it. Ask the user rather than guessing.

Attaching evidence does not replace it. Evidence shows *what the numbers came
from*; the rationale says *why this write* — the queue shows both, and an
approver reading a payload with neither has only the amounts to go on.

**The memo is not the place for any of it.** `privateNote`, and a line's `memo`,
are written into the customer's books permanently. Keep them to a short summary
of what the entry is; the descriptor for the action says exactly how short.

### NetSuite resolves nothing
QuickBooks takes an account by name and resolves it **when you propose**, refusing
a wrong one on the spot with the near matches; `proposals.accounts` will list the
real chart of accounts for you first. NetSuite has neither. It takes numeric
internalIds and checks none of them until it posts — *after* a human has approved.

A wrong `accountId` at least fails loudly then. A wrong-but-valid `departmentId`,
`classId`, `locationId` or `entityId` posts successfully into the wrong report,
and nothing tells anyone. So never guess a NetSuite id: ask the person you are
working with, and if they cannot supply it, propose without the segment rather
than inventing one.

### Be honest about `proposedVia`
It defaults to `agent`, which is the claim that assumes less. Set `human` **only**
when a person has actually walked through the payload with you — the amounts, the
accounts, the date. It is an audit field: an approver who sees `human` believes a
second person already looked. Claiming it for a payload you assembled alone is the
one thing this field exists to prevent.

### Do not re-implement the tool's checks
The tool already enforces balanced debits and credits, at least two lines, cent
precision, date format, payload shape, whether the org has configured a gateway
for the action, whether an assignee is eligible, and separation of duties.

Do not pre-check any of it. A guard you write here is a guard that disagrees with
the real one — and when it does, you either block a legal write or wave through
something the server will reject anyway. Send the proposal and read the answer.

### Never say it has been posted
A proposal that comes back `approved` has **not** been written to the books.
Approval and posting are separate: posting runs in the background afterwards.
Relay the `status` and `waitingOn` the response gives you, and say plainly that
nothing has been written yet.

## Raising one

1. **Describe.** `tools.describe("proposals.propose")` for the proposable actions
   and the payload doc for the one you need.
2. **Build the payload** to that doc.
3. **Write the `rationale`.** Interview the user if you are missing the *why*.
   Attaching evidence at step 5 does not excuse it — see above.
4. **Offer an assignee** (optional). Naming an approver or reviewer is **binding**:
   only that person can act, and only they see it in their queue. Omitting it
   leaves the proposal open to anyone holding the capacity, which is usually what
   you want — name someone only when the user asks for a specific person.

   The fields take a **Dough user id**, and nothing Dough exposes will give you
   one. If someone says "send it to Priya" and you do not have her id, **say so
   and ask** — do not guess an id, and do not guess an email either. Guessing
   wrong is not harmless: an id that belongs to a real person binds the proposal
   to them, and only they will see it. Leaving the assignment off is always safe.
5. **Attach the transcript** when the reasoning matters. It becomes the evidence
   behind the summary, for an approver who wants more than one line.
6. **Call it, then relay** the reference (`PROP-…`), what it is waiting on, and
   where to act on it — all of which come back in the response.

## Evidence-backed proposals

Commands that require evidence, including `/dough:propose` and
`/dough:proposal_fix`, must follow this entire section. Do not shorten it or
quietly propose without evidence.

1. **Check the CLI first.** Run `dough evidence --help`. If `dough` is missing or
   does not know `evidence`, stop before scanning or proposing. Evidence requires
   dough CLI v0.1.46 or later.

   **Find out whether anyone is there before you decide what to do about it.**
   Run the `scan` from step 3 — it is a plugin script, not the `dough` binary,
   so it works whether or not the CLI exists, and its `unattended` field is the
   only way you can learn this. Nothing else in the environment is visible to
   you.

   - **`unattended` is `true`:** stop and report that the CLI is missing. There
     is nobody to open a terminal. Do **not** propose without evidence — that
     trades a missing signature for a missing audit trail, which is the worse of
     the two.
   - **Otherwise:** give the appropriate installer command, ask the user to open
     a new terminal, and have them rerun their original command:

   - macOS/Linux: `curl -fsSL https://raw.githubusercontent.com/Dough-AI/dough-installer/main/install.sh | sh`
   - Windows: `irm https://raw.githubusercontent.com/Dough-AI/dough-installer/main/install.ps1 | iex`

2. **Refresh best-effort.** Run `dough plugin refresh`. A failure or unsupported
   command must not block the proposal. If it updates the plugin, report that the
   new copy applies only after `/reload-plugins` or an app restart; continue the
   current flow using the live `tools.describe` schema.

3. **Scan.** Run
   `${CLAUDE_PLUGIN_ROOT}/skills/propose/scripts/collect_evidence.py scan` with
   `python3` on macOS/Linux or `python` on Windows. Run it as one bare command—no
   `cd`, pipe, or compound shell command—and read its complete output.

   Its `unattended` field tells you **whether anybody is there**: `true` means
   nobody is, and `false` means **assume someone is**. It is deliberately
   false-unless-certain, so a box that predates this flag asks rather than
   proceeds — the safe direction.

   Every step that would ask a question depends on it, **including step 1**,
   which is why that step runs this same scan early: it is a plugin script
   rather than the `dough` binary, so it works with or without the CLI. You
   cannot determine this any other way — the environment variable behind it is
   invisible to you, which is exactly why the scan reports it.

4. **Curate and disclose.** Keep only files that substantiate the replacement.
   Give each a one-line note. Show every retained file, its size and note, plus
   the session transcript, which is always uploaded. State the limits: 25 MB per
   object, 100 MB per set, and 64 objects.

   Then, **consent — which depends on whether anyone is there.**

   - **`unattended` was `true` in the scan output** (a hosted agent on its own
     machine): say all of the above as a message, then **continue without
     asking**. Nobody is reading, and a question here strands the run holding a
     rented machine. The disclosure is not skipped — it is the record, and it
     lands in the run log where the agent's owner reads it afterwards.
   - **Otherwise:** obtain clear user consent before any upload, as always.

   Proceeding unattended is not a lowered bar. **The proposal is still approved
   by a person** before anything is booked, so consent for the upload was given
   in advance by whoever configured the agent to run this way, and consent for
   the booking is still given afterwards, per entry. What you must never do is
   propose *without* evidence because nobody was available to approve attaching
   it — that would trade a missing signature for a missing audit trail.

5. **Upload once.** Run one bare command:

   `dough evidence upload --session <sessionId> --file <kept path> --file <kept path>`

   The command freezes and uploads the transcript too. It returns `evidenceId`,
   `uploaded`, `failed`, and `rejected`, and declares every object before bytes
   move so missing objects remain visible to the approver.

6. **Handle incomplete uploads explicitly.** Show every `failed` and `rejected`
   object and relay rejection messages without interpreting their codes.

   **Unattended: proceed with the object declared missing.** It was declared
   before bytes moved, so the gap stays visible to the approver, which is the
   property that matters. Say exactly what is missing and why.

   Do **not** re-run the upload. It mints a NEW evidence set with a new
   transcript snapshot and abandons the first — which is the very "upload a new
   set that omits it" the interactive branch forbids below. The transport
   already retries each object with backoff before reporting it failed, so a
   failure here has been retried. And do not cancel: a run that reached this
   point has done the work, and discarding it leaves nobody anything to
   approve.

   Otherwise offer retry, proceed with the declared missing object, or cancel.
   If the user proceeds, do not upload a new set that omits it; doing so would
   hide the gap.

7. **Attach the reference.** Put
   `transcript: { evidenceId, sessionId, manifest }` on the proposal call the
   invoking workflow requires: `proposals.propose` for `/dough:propose` and a
   standalone `/dough:proposal_fix`, or `proposals.propose_batch` when fixing a
   batch. The manifest has one entry per retained file with `key`, `filename`,
   `sha256`, `bytes`, `mime`, `role`, and its note. Send rationale separately on
   each proposal or batch item. Never inline transcript or file contents into MCP.

8. **Recover safely.** For `invalid_evidence`, restart from disclosure and
   consent with a new set — unattended, that disclosure goes to the run log and
   you continue, exactly as in step 4. For `evidence_integrity`, redeclare and re-upload; do
   not retry the consumed or mismatched set. If the proposal tool the invoking
   workflow requires is absent, stop and report a stale plugin/server or MCP
   connection rather than proposing by another route.

## When it comes back refused

Nothing is queued and there is nothing to withdraw — fix and call again. The
`code` tells you whether that is even possible:

| `code` | What it means | What to do |
| - | - | - |
| `invalid_payload` | The entry is not legal. `findings` name the offending line and what is wrong (`findingCount` is the true total when the list is capped). | Fix every finding and retry. |
| `invalid_assignee` | The person you named cannot act in that capacity. The response lists who **is** eligible, by name. | Re-ask the user, offering those names. Do not retry the same id. |
| `separation_of_duties` | The assignment breaks a rule — a proposer cannot review their own write, and a reviewer cannot also approve. | Name someone else, or omit the assignment. |
| `stage_not_configured` | You assigned a reviewer to a gateway that has no review stage (or an approver where none is required). | Drop that assignment. A different person will not help. |
| `no_eligible_actor` | The gateway requires a stage nobody currently holds. | An admin must add someone under Action Gateway. Stop. |
| `no_gateway` | Nobody has configured who approves this action for this org. | An admin must set it up. Nothing you can do here will work. Stop and say so. |
| `unknown_action` | Not in the catalog. The response lists what is proposable. | Offer a proposable action, or tell the user this is not supported yet. |

Report a refusal as what it is. `no_gateway` and `no_eligible_actor` need an
administrator and no amount of retrying helps; the rest are yours to fix. Do not
apologise for a proposal being "stuck" — a refused proposal was never created.
