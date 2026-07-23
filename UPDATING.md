# Updating the Dough plugin

How to publish an update, and how clients refresh to get it. Read this before
shipping a skill change — the desktop app is version-gated and has no refresh
button, so the steps below are not optional.

---

## A. Publish an update (maintainer)

Every change to a skill, reference, or command is a release.

1. Edit the files under `skills/`, `skills/references/`, or `commands/`.
2. **Bump the version — to the SAME value in all three places:**
   - `.claude-plugin/plugin.json` → `version`
   - `.claude-plugin/marketplace.json` → `metadata.version`
   - `.claude-plugin/marketplace.json` → `plugins[0].version`  ← **the desktop app reads this one**
3. Validate: `claude plugin validate .` (expect `✔ Validation passed`).
4. `git commit` and `git push` to `main`.

> **Why bump all three.** The Claude Code CLI can fall back to the git commit SHA,
> but the **Claude Desktop app is version-gated and reads the `marketplace.json`
> plugin-entry version**. If you don't bump it there, the desktop Update button
> stays grayed out and clients never receive the change — even though the commit
> is on `main`. Do not omit the version; do not rely on SHA-based auto-update for
> the desktop app.

---

## B. Refresh a client — CLI first, then the desktop app

The Claude Code CLI and the desktop app **share one on-disk store**
(`~/.claude/plugins/`). The desktop app cannot reliably refresh it on its own
(remove/re-add does not re-clone; there is no plugin "refresh" action). So use the
CLI — which has refresh commands — to update the shared store, then let the desktop
app read it.

### Step 1 — in Claude Code (the CLI / terminal)

```
/plugin marketplace update dough-plugins     # git-pulls the shared marketplace clone to latest
/plugin update dough@dough-plugins           # re-installs the plugin into the shared cache
/reload-plugins                              # loads it in the current CLI session
```

Verify: `/plugin` (or `claude mcp list` for the connector) shows Dough at the new
version.

### Step 2 — in the Claude Desktop app

```
Fully quit and reopen the app.
```

On launch it reads the now-fresh shared store, so the skill is current. Verify in
**Settings → Plugins → Dough**: the version and the skill content should match the
release.

### If the desktop still shows the old version/skill

The desktop cache went stale. Now that Step 1 refreshed the shared clone:

- **Uninstall + reinstall** the plugin in the app (Settings → Plugins → Dough → ⋮ →
  Uninstall, then reinstall from the marketplace). Reinstall reads the fresh clone.

---

## C. Desktop-only users (no CLI installed)

There is currently **no reliable self-service refresh** for the desktop app alone —
this is an open Anthropic bug (desktop marketplace remove/re-add does not re-clone,
and there is no plugin refresh action). Best effort: uninstall + reinstall the
plugin, or wait for the app's background update check.

Until Anthropic ships a desktop marketplace refresh, **installing the Claude Code
CLI and running Step B is the dependable path.**

---

## Emergency manual override (maintainer, last resort)

If a client is stuck and cannot use the CLI, the shared store can be corrected by
hand (this is what the CLI does under the covers):

```sh
MKT=~/.claude/plugins/marketplaces/dough-plugins
CACHE=~/.claude/plugins/cache/dough-plugins/dough/<installed-version>
git -C "$MKT" fetch origin && git -C "$MKT" reset --hard origin/main
rsync -a --delete "$MKT/skills/" "$CACHE/skills/"
rsync -a --delete "$MKT/commands/" "$CACHE/commands/"
cp "$MKT/README.md" "$CACHE/README.md"
```

Then fully restart the app. This is a workaround for the desktop bug, not a normal
user flow.
