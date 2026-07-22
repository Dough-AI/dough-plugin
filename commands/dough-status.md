---
description: Show your Dough connection status, what your org can do, and whether the data lake is ready to query.
---

Report the caller's Dough status in three parts. Be concise.

1. **Connection.** State whether the `dough` MCP is connected. If tool calls
   fail with an auth error, tell them to complete the Dough sign-in (OAuth) or
   re-run it, and stop.

2. **Entitlements.** Call `tools.list` and list the namespaces this org can use
   (e.g. `integrations`, `queries`, `mappings`, `tables`). This reflects the
   org's SKUs.

3. **Data-lake readiness (do not skip).** Having the tools does not mean data is
   reachable. Call `integrations.sources`:
   - Normal result → report the lake as **ready** and summarize connected sources.
   - Error containing *"Data Lake is not set up for this organization"* → report
     **not provisioned**: the org has the SKU but data access needs provisioning
     (operator-side). Do not retry.

End with a one-line verdict: connected + entitled + ready, or exactly which of
those is missing.
