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

If `dough` is not found, the CLI is not installed. Point them at the installer
rather than trying to work around it:

- macOS: `curl -fsSL https://raw.githubusercontent.com/Dough-AI/dough-installer/main/install.sh | sh`
- Windows: `irm https://raw.githubusercontent.com/Dough-AI/dough-installer/main/install.ps1 | iex`

If the command fails for any other reason — no network, a proxy, a permissions
error on the plugin store — show what it said and stop. Do not try to patch the
plugin store by hand, and do not fall back to `claude plugin` commands: those go
through the same clone this command exists to bypass.
