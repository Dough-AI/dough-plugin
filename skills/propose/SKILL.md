---
name: propose
description: Use when something needs to be WRITTEN to a connected accounting system — booking a journal entry, an accrual, a reclass, a correcting or adjusting entry in QuickBooks — or any time you are about to tell someone to go and make an entry by hand. Raises the change for human approval instead of performing it; nothing reaches the books until a person approves.
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
"you'll need to book this in QuickBooks". Propose it instead. Telling a person to
retype numbers you already have is the failure this replaces — they will retype
them slightly wrong, and nothing records why the entry was made.

## Working rules

### Ask the server what is proposable — never assume
Your first call is `tools.describe("proposals.propose")`. It returns the
`(target, kind)` pairs that can be proposed **right now** and documents each
payload, including rules you would otherwise get wrong (amounts carry at most two
decimal places; the side of a line is `postingType`, never a negative amount).

This plugin is pinned on disk and the catalog moves without it. A shape you
remember from a previous session may be stale; the descriptor never is.

### The rationale is the product
Everything else is mechanical. The rationale is what a human reads to decide, and
a proposal they cannot evaluate is one they have to reject.

Say **why this write**, and **what you based it on** — the source, the period, the
filter. Compare:

> ✗ "September accrual."
> ✓ "September contractor invoices totalling $8,400.50 arrived after close.
>    Accruing so the month reflects the expense. From `bill.vendor_invoices`,
>    invoices dated 2026-09-01..30 with no matching payment."

If you cannot write the second kind, you do not yet understand the change well
enough to propose it. Ask the user rather than guessing.

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
3. **Write the rationale.** Interview the user if you are missing the *why*.
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
