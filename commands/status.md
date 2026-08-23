---
description: Show your Dough connection status, what your org can do, whether the data lake is ready to query, and whether the plugin is up to date.
allowed-tools: Bash(dough:*), mcp__dough__tools__list, mcp__dough__integrations__sources
---

Report the caller's Dough status in four parts. Be concise.

1. **Connection.** State whether the `dough` MCP is connected. If tool calls
   fail with an auth error, tell them to complete the Dough sign-in (OAuth) or
   re-run it, and stop.

2. **Entitlements.** Call `tools.list` and list the namespaces this org can use
   (e.g. `integrations`, `queries`, `mappings`, `tables`). This reflects the
   org's SKUs.

3. **Data-lake readiness (do not skip).** Having the tools does not mean data is
   reachable. Call `integrations.sources` and read its **`status`** field (a normal
   payload, not an error):
   - `status: "ready"`, `needsSetup: false`, with `connected` sources → **ready**;
     summarize the connected sources.
   - `status: "ready"`, `needsSetup: true` (or empty `connected`) → **provisioned
     but no sources connected yet** — nothing to query until an integration is
     connected.
   - `status: "not_provisioned"` → **not provisioned**: SKU present, no tenant
     (operator-side). Do not retry.
   - `status: "error"` → report the `errorMessage`.

4. **Plugin freshness.** Run `dough plugin refresh --check`. It reports whether
   a newer plugin has been published and installs nothing — this command is a
   report, and nobody asking for status is asking to have software installed.
   Treat it as best-effort: if `dough` is missing, is too old to know the
   `plugin` command, or cannot reach the network, say so in one line and carry
   on. A stale plugin does not stop anything above from being reported.

   If an update is available, say so and point at **`/dough:refresh`**, which
   installs it.
   Do not tell them to restart — nothing has changed yet.

End with a one-line verdict: connected + entitled + ready, or exactly which of
those is missing. Mention a waiting plugin update there if step 4 found one.
