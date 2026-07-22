---
name: getting-started
description: Use when starting with Dough or asking what data and tools you have — confirms the Dough connection, reports what the org can do, and checks whether the data lake is actually ready to query.
---

# Getting started with Dough

Use this to orient a partner the first time, or whenever they ask "am I
connected / what can I do here?"

## Steps

1. **Confirm the connection and capabilities.** Call `tools.list`. It is
   org-scoped, so it reflects exactly which namespaces this org can use
   (expect `integrations`, `queries`, `mappings`, `tables` for a datalake org).
   Report them plainly.

2. **Probe whether the data lake is actually usable — do NOT stop at step 1.**
   Holding the datalake SKU makes the tools *appear*, but real data access also
   requires a provisioned tenant with connected sources. Call `integrations.sources`
   (a lightweight read) and read its **`status`** field — it reports readiness in a
   normal payload, it does not throw:
   - `status: "not_provisioned"` → the org has the SKU but no provisioned tenant.
     Say so directly: tools are granted but data access needs provisioning
     (operator-side). Stop — do not retry.
   - `status: "ready"` with `needsSetup: true` (or an empty `connected` list) →
     provisioned, but no integrations are connected yet. The lake exists but has
     nothing to query until a source is connected. Say that.
   - `status: "ready"` with `needsSetup: false` and one or more `connected`
     sources → fully ready. Summarize what's connected.
   - `status: "error"` → report the `errorMessage`.

3. **Orient to the data.** If ready, call `integrations.tables` to show what
   datasets/tables exist, and mention that saved queries (`queries.list`) show
   how the org already computes its metrics.

4. **Point the way forward.** Tell the partner they can now explore and write
   their own read-only SQL — see the `datalake` skill for the full workflow, and
   `references/dough-datalake-guide.md` for good practice and gotchas.

For exact tool inputs, call `tools.describe`. For behaviors and gotchas, read
`../references/dough-datalake-guide.md`.
