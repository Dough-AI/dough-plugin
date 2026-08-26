---
name: run-agent
description: Open a Dough agent inside this session. Use when asked to run, use, start, or switch to a Dough agent by name — for example "run the campfire agent" or "use rental-agent-demo".
---

# Run a Dough agent in this session

A Dough agent is a directory: its instructions live in its own `CLAUDE.md` and
its skills in `.claude/skills/`. **Being in that directory is what loads both**,
and it is also what lets the agent's scripts find the credentials they need. Adding it to the
session with `--add-dir` registers the skills but does not load `CLAUDE.md`,
which is not the same thing and fails silently.

So this skill does one thing: it fetches the agent and moves the session into it.

## Steps

1. **Fetch the agent.** Run `dough agent sync <name>`. It prints the agent's
   version and its **absolute path**, and it overwrites the directory whether or
   not it already exists — so there is nothing to check first and no "already
   installed" state to reason about.

   If it reports that the agent requires credentials, that is information, not a
   refusal. Keep going: credentials are delivered to this session by the Dough
   hook once you are inside the directory.

2. **Find a directory-change tool.** You need one that changes the *session's*
   working directory. If your harness defers tools until they are searched for,
   **search for it and load it now** — you will not reach for a tool you cannot
   see.

   **If there is no such tool, STOP.** Tell the user to run
   `dough agent run <name>` from a terminal instead. Do not improvise a
   substitute; see step 4.

3. **Change the session directory** to the absolute path from step 1.

4. **Stop there. End your turn.** Tell the user the agent's instructions and
   skills take effect on their next message.

   Two things not to do, both of which look like diligence and are not:

   - **Do not run `pwd` to confirm the move.** The move does not take effect
     until this turn ends. `pwd` still reports the old directory, so checking now
     fails a switch that actually worked — and a check that fires on success is
     worse than no check, because it teaches everyone to ignore it. The same goes
     for reading `./CLAUDE.md` or any other relative path this turn.
   - **Do not fall back to a Bash `cd`.** It does not move the session, and every
     Bash call is a fresh shell, so it does not survive to the next command
     either. When the directory-change tool appears to do nothing, `cd` is
     exactly the wrong conclusion to draw.

5. **On your next turn, confirm the move landed.** Check your working directory
   and that the agent's `CLAUDE.md` is loaded. If either is missing, the move did
   not happen — tell the user to run `dough agent run <name>` from a terminal.

   Do not check for credentials as part of this. They are not in the session by
   design, so their absence tells you nothing about whether the move worked.

6. **From then on, follow the agent's own `CLAUDE.md`.** You are the agent now;
   this skill's job is finished.

## Credentials

**This session holds none of them, and that is not a fault.** An agent's own
scripts fetch their credentials from the Dough vault when they run. Nothing is
placed in the session environment, so `env | grep TOKEN` comes back empty even
when everything is working perfectly. Do not report that as a problem, and do
not try to fix it.

What this means in practice:

- **Run the agent's scripts and let them do it.** A script calls
  `dough_secrets.load()` at startup and gets what it needs.
- **If you write your own script that needs a credential**, call the same
  loader — do not try to read one out of the environment, and never inline a
  value into a command. `load()`'s own docstring carries the few lines that put
  it on your import path.
- **Never print, echo, or copy a credential value**, and never read the file one
  lives in just to display it.

When a credential cannot be resolved, the script fails with a sentence written
for a person: not signed in, a provider an admin has not configured, or the
user's own connection to that provider having expired, with where to reconnect
it. Relay that message and act on it rather than investigating further — it is
the answer, not a symptom.

## Never call `dough agent run` from inside a session

That command launches a *new* Claude Code session, and it refuses to start when
it detects it is already running inside one. It is the terminal entry point, and
it is what you tell the user to run when this skill cannot finish — never
something you run yourself.
