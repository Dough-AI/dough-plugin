---
description: Update the Dough plugin to the latest published version, and report what changed.
allowed-tools: Bash(dough:*)
---

Bring this machine's Dough plugin up to date, then report.

Run `dough plugin refresh`.

That command downloads the plugin straight from the public repo over HTTPS and
writes it into Claude Code's shared plugin store. It deliberately does not go
through the marketplace clone, because the clone is what gets stuck: the desktop
app has no action that advances it, so uninstalling and reinstalling the plugin
re-installs the same stale copy it already had. Going around it is the whole
point of this command.

Report exactly what the command printed, then:

- **If it refreshed** (`X -> Y`): tell them to **restart Claude Code**, and be
  plain that the change is not live until they do. A refresh never applies to
  the session that ran it — this session is still running the old copy, whatever
  the version number now says on disk. Do not describe the new behaviour as
  though it were already in effect.
- **If it was already current**: say so in one line. Nothing to restart.
- **If it printed a `Warning:` about versions disagreeing**: relay it verbatim.
  It means a release bumped some version fields and not the one the desktop app
  gates on, so that release is one nobody can install. That is a publishing bug
  for whoever maintains the plugin, not something the user can fix.

Two failures both mean "re-run the installer", and the second is the one most
people will hit first:

- **`dough` is not found** — the CLI is not installed.
- **`dough: unknown command 'plugin'`** — the CLI is installed but predates this
  command. Say that plainly; the message itself mentions neither the CLI nor an
  update, so a user has no way to read it as "my dough is too old". Nothing about
  the plugin needs reinstalling.

Either way, point them at the installer rather than working around it:

- macOS: `curl -fsSL https://raw.githubusercontent.com/Dough-AI/dough-installer/main/install.sh | sh`
- Windows: `irm https://raw.githubusercontent.com/Dough-AI/dough-installer/main/install.ps1 | iex`

Then have them run `/dough` again.

If the command fails for any other reason — no network, a proxy, a permissions
error on the plugin store — show what it said and stop. Do not try to patch the
plugin store by hand, and do not fall back to `claude plugin` commands: those go
through the same clone this command exists to bypass.
