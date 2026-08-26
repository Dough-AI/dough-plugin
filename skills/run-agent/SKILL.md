---
name: run-agent
description: Open a Dough agent inside this session. Use when asked to run, use, start, or switch to a Dough agent by name — for example "run the campfire agent" or "use rental-agent-demo".
---

# Run a Dough agent in this session

A Dough agent is a directory: its instructions live in its own `CLAUDE.md`, its
skills in `.claude/skills/`, and its credentials are delivered to commands run
inside it. **Being in that directory is what loads all three.** Adding it to the
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
   credentials arrive on their next message.

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

5. **On your next turn, you will normally be told where you are.** The Dough hook
   announces the agent and confirms its credentials are live, as a fact rather
   than something you have to remember.

   If that announcement does not arrive, verify: confirm your working directory
   and that the agent's `CLAUDE.md` is loaded. If either is missing, the move did
   not happen — tell the user to run `dough agent run <name>` from a terminal.

6. **From then on, follow the agent's own `CLAUDE.md`.** You are the agent now;
   this skill's job is finished.

## Credentials

Credentials reach commands **you** run. They are attached to each Bash call
inside the agent directory, and the value never appears in the command itself —
only a path to the file holding it. Never print, echo, or copy a credential
value, and never read the file they live in just to display it.

**A command the user types by hand with `!` will not see them.** That is expected
and is not a fault: `!` bypasses the hook that supplies them entirely. If a user
checks that way and reports the credentials are missing, say so and offer to run
the same command yourself.

Two more limits worth stating rather than letting someone discover:

- Only Bash receives them. That is enough, because credentials are consumed by
  scripts.
- They are scoped to this session and removed when it ends.

## Never call `dough agent run` from inside a session

That command launches a *new* Claude Code session, and it refuses to start when
it detects it is already running inside one. It is the terminal entry point, and
it is what you tell the user to run when this skill cannot finish — never
something you run yourself.
