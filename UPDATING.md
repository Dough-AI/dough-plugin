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

## B. Refresh a client — `/dough:refresh`, or `dough plugin refresh`

**The one-liner: run `/dough:refresh` in Claude Code, then restart it.** That is the whole
procedure for a user on any platform, and it is the answer to give in support.

`/dough:refresh` runs `dough plugin refresh`, which downloads this
repo over plain HTTPS and writes it into Claude Code's shared plugin store
(`~/.claude/plugins/`). It needs no `git`, no credentials and no `claude` CLI —
the repo is public — which is why it works on a Windows machine where the app's
own update path does not.

It needs **Dough CLI v0.1.39 or later** (`dough --version`). On an older binary
`/dough:refresh` reports `dough: unknown command 'plugin'` — which names neither the CLI nor an
update, so it reads as a broken plugin when it is an old CLI. Re-running the
installer (section C) fixes it; nothing about the plugin needs reinstalling.

From a terminal, without Claude Code, the same thing:

```
dough plugin refresh           # install the latest published version
dough plugin refresh --check   # report whether an update exists, change nothing
```

`/dough:plugin_version` reports all three numbers — loaded in this session,
installed on disk, latest published — which is the fastest way to tell "the
refresh did not work" apart from "the refresh worked and they have not restarted".

### A refresh NEVER applies to the running session

Claude Code pins the plugin's install path **and** reads its command and skill
bodies at session start. Editing the store underneath a live session changes
nothing in it — verified by sentinel: a command file altered mid-session still
served the old text, even after `installed_plugins.json` had moved on.

So the refresh always ends with **restart Claude Code** (fully quit and reopen the
desktop app). Never tell a user the new behaviour is live before they do.

### Why not `/plugin update`

```
/plugin marketplace update dough-plugins     # git-pulls the shared marketplace clone
/plugin update dough@dough-plugins           # re-installs FROM THAT CLONE
```

This still works and remains fine for maintainers. It is not what to tell a stuck
user, because the thing that gets stuck is the clone itself:

- The installed copy lives in a **version-named** cache dir and
  `installed_plugins.json` pins the plugin to that exact path. What hands out new
  versions is the git clone under `marketplaces/`.
- The desktop app has **no action that advances that clone**. So uninstall +
  reinstall in the app re-installs *from the stale clone* and faithfully
  reproduces the version the user already had — which is why "I reinstalled it
  and it is still wrong" is the usual report, and why it is not a cache problem
  they can clear.
- The clone can also wedge in ways nothing surfaces. On one developer machine it
  sat checked out on a **feature branch** with its `main` ref a month behind,
  while `/plugin marketplace update` reported success.

`dough plugin refresh` does not read, trust, or require the clone. It repairs it
afterwards on a best-effort basis (force-resetting it to `origin/main` when `git`
is available) so a later uninstall/reinstall from the app does not regress the
user — but the refresh is correct whether or not that repair succeeds.

### Verify

`/plugin` in the CLI, or **Settings → Plugins → Dough** in the app, should show
the new version, and the skill content should match this release.

---

## C. Desktop-only users (no Dough CLI installed)

Install the Dough CLI once; then `/dough:refresh` works from inside the app like anywhere
else.

- macOS: `curl -fsSL https://raw.githubusercontent.com/Dough-AI/dough-installer/main/install.sh | sh`
- Windows: `irm https://raw.githubusercontent.com/Dough-AI/dough-installer/main/install.ps1 | iex`

Without the CLI there is still **no reliable self-service refresh** — the desktop
app's remove/re-add does not re-clone, and there is no plugin refresh action. That
is an open Anthropic bug; installing the CLI is the way around it.

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
