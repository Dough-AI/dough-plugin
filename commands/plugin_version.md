---
description: Report which Dough plugin version is loaded, which is installed on disk, and whether a newer one has been published.
allowed-tools: Bash(dough:*)
---

Report which Dough plugin version is actually in play. There are three numbers
and they do not have to agree — that disagreement is the whole reason this
command exists.

1. **Loaded in this session.** The plugin root for this session is:

   `${CLAUDE_PLUGIN_ROOT}`

   The last path segment is the version. This is what the session is *executing*,
   whatever the store says, because Claude Code pins the install path and reads
   command and skill bodies at session start.

2. **Installed on disk, and latest published.** Run `dough plugin refresh --check`.
   It prints either `already current (X)` or `update available: X -> Y`, and it
   installs nothing.

Then say which of these three states they are in, and only the one that applies:

- **Loaded = installed = latest.** Current. Nothing to do.
- **Installed is newer than loaded.** A refresh has already run in this session
  or another one, and this session is still on the old copy. In the **terminal
  (Claude Code CLI)**, `/reload-plugins` loads it on the spot. In the **desktop
  app** that command does not exist, so they must **fully quit and reopen** —
  not just close the window, which leaves the app running with the old plugin
  loaded and looks exactly like a refresh that did not work. Give the one that
  matches where they are; if you cannot tell, give the quit-and-reopen form,
  which works in both.
- **A newer version is published.** Point them at **`/dough:refresh`**, then a
  restart.

If `dough` is not installed, or is too old to know the `plugin` command
(`unknown command 'plugin'`, needs v0.1.39+), you can still report the loaded
version from the path above — say that the other two are unknown and why, rather
than reporting nothing.

Keep it to a few lines. Give the three numbers, then the one state that applies.
Do not recommend a restart when nothing has changed.
