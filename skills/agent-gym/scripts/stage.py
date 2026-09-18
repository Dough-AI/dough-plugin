#!/usr/bin/env python3
"""Stage one period's build, and audit it afterwards for what it read.

  uv run --with pyyaml stage.py <agent-dir> <period> [--root DIR]
  uv run --with pyyaml stage.py <agent-dir> <period> --audit <transcript.jsonl>...

Staging copies the agent into a directory holding only what that period may see:
its own files, and `inputs/` up to and including that period. `eval/` does not
come along, so neither a build script nor an agent working in there can read the
reference it is about to be compared against, or a later period's data.

That is hygiene, not a wall — every tool takes absolute paths, and an agent asked
to reproduce someone's workbook has a genuine reason to go looking for one. So
the second half of this script is the audit: given the transcripts of whatever
ran, it reports every path outside the staging directory that was touched, and
fails the period if a reference was among them. Blindness then stops being a
claim and becomes a line in the report.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:                                   # pragma: no cover
    raise SystemExit(
        "PyYAML is missing, and staging reads the agent's eval.yaml with it.\n"
        "Run this through uv, which brings its own:\n"
        "  uv run --with pyyaml " + __file__
    )

# Never copied into a staging directory, whatever the agent holds.
NEVER_STAGE = {"eval", ".git", "__pycache__", ".venv", "node_modules"}

# Directories whose children are periods, and where each one stops.
#
#   inputs/  through the period itself — it is what the build reads. Later
#            periods are dropped: next month's data is not available to a run of
#            this month.
#   output/  strictly before the period. It holds candidates already built, so
#            this period's own would be the answer sitting in the working
#            directory, and a holdout's would be a later period's answer.
#
# Earlier periods stay in both: an agent may legitimately look at the month
# before, and its rules are usually mined from exactly that.
PERIOD_DIRS = {"inputs": "after", "output": "from"}


def load_spec(agent: Path) -> dict:
    return yaml.safe_load((agent / "eval" / "eval.yaml").read_text())


def reference_paths(agent: Path, spec: dict) -> list[Path]:
    """Every reference the eval set names, resolved — the paths the audit looks
    for. A period's own reference is not special: reading ANY of them during a
    build is contamination."""
    out = []
    for output in spec["outputs"]:
        template = output["reference"]["file"]
        for case in spec["eval_set"]:
            path = Path(template.format(period=case["period"])).expanduser()
            out.append(path if path.is_absolute() else agent / path)
    return out


def stage(agent: Path, period: str, root: Path | None) -> Path:
    spec = load_spec(agent)
    periods = sorted(c["period"] for c in spec["eval_set"])
    if period not in periods:
        sys.exit(f"{period} is not in this agent's eval set")

    root = root or Path("/tmp") / "agent-gym-staging"
    staging = root / agent.name / period
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    for item in sorted(agent.iterdir()):
        if item.name in NEVER_STAGE:
            continue
        if item.name in PERIOD_DIRS and item.is_dir():
            cutoff = PERIOD_DIRS[item.name]
            (staging / item.name).mkdir()
            for sub in sorted(item.iterdir()):
                if sub.is_dir() and (sub.name > period if cutoff == "after" else sub.name >= period):
                    continue
                (shutil.copytree if sub.is_dir() else shutil.copy2)(sub, staging / item.name / sub.name)
            continue
        (shutil.copytree if item.is_dir() else shutil.copy2)(item, staging / item.name)

    # The period's own output directory is dropped above (it may hold the
    # candidate from a previous run), so re-create it empty: a build writing
    # there should not have to mkdir its own destination, and a shell redirect
    # cannot.
    for output in spec["outputs"]:
        (staging / output["candidate"]["file"].format(period=period)).parent.mkdir(
            parents=True, exist_ok=True)

    # What the agent may see: enough to know it is staged, and nothing more. The
    # reference paths used to be listed here, which handed a map to the answers
    # to anything working in the directory — and made an agent that merely read
    # this file look contaminated.
    (staging / ".gym-staging.json").write_text(json.dumps({
        "agent": agent.name, "period": period,
        "staged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "periods_carried": {name: (f"<= {period}" if cutoff == "after" else f"< {period}")
                            for name, cutoff in PERIOD_DIRS.items()},
    }, indent=2) + "\n")

    # What the auditor needs, kept beside the staging directory rather than in it.
    (staging.parent / f"{period}.excluded.json").write_text(json.dumps({
        "agent_dir": str(agent), "period": period,
        "references_excluded": [str(r) for r in reference_paths(agent, spec)],
    }, indent=2) + "\n")
    return staging


# ── the audit ────────────────────────────────────────────────────────────────
# A Bash call hides its file access inside a shell string, so the audit reads the
# whole text rather than a `file_path` field. Four forms have to be caught: an
# absolute POSIX path, a ~-relative one, a Windows path behind a drive letter,
# and — for a read made after a `cd`, where no directory appears at all — the
# reference's own filename.
PATH_RE = re.compile(r"([A-Za-z]:[\\/][^\s\"',;:()\[\]]*"
                     r"|(?:/|~[\\/])[^\s\"',;:()\[\]]+)")


def resolved(path) -> str:
    """One spelling per file.

    Symlinks are followed and case is folded where the platform folds it, so the
    two ways to name the same file compare equal: on macOS `/tmp` is a symlink to
    `/private/tmp`, and on Windows `C:\\Users` and `c:\\users` are one directory.
    Comparing raw strings silently missed both.
    """
    return os.path.normcase(os.path.realpath(path))


def paths_in(text: str) -> set[str]:
    found = set()
    for raw in PATH_RE.findall(text):
        # A transcript is JSON, so a Windows separator arrives doubled.
        cleaned = raw.replace("\\\\", "\\").replace("\\ ", " ").rstrip("\\")
        found.add(cleaned)
        if cleaned.startswith("~"):
            found.add(str(Path.home()) + cleaned[1:])
    return found


def actions_text(transcript: Path) -> str:
    """Paths the run ACTED on, not every path that crossed its screen.

    A transcript records both: the tool calls it made, and the text that came
    back. A path quoted in a tool RESULT — a directory listing, a file it read
    that happens to mention another — is not a read. Counting those made an
    agent look contaminated for reading its own staging manifest.

    JSONL records are scanned for tool_use blocks only. Anything that is not
    JSON (a hook log, a shell trace) is scanned whole, since every line in one
    of those is an action by construction.
    """
    parts: list[str] = []
    for line in transcript.read_text(errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            parts.append(line)
            continue
        content = (record.get("message") or {}).get("content")
        blocks = content if isinstance(content, list) else []
        acted = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
        if acted:
            parts.append(json.dumps([b.get("input", {}) for b in acted]))
        elif not blocks:
            parts.append(line)               # a flat log record, not a message
    return "\n".join(parts)


def audit(agent: Path, period: str, transcripts: list[Path], staging: Path | None) -> dict:
    """Report every reference path that appears in what ran.

    A transcript is any file the run produced that records its tool calls — a
    Claude Code session JSONL, a hook log, a command log. Whatever names a path
    it opened. Reading one of these is cheap; not reading them means blindness
    stays an assumption.
    """
    spec = load_spec(agent)
    refs = {resolved(p) for p in reference_paths(agent, spec) if p.exists()}
    ref_dirs = {resolved(p.parent) for p in reference_paths(agent, spec)}

    seen: set[str] = set()
    acted: list[str] = []
    missing: list[str] = []
    for path in transcripts:
        if not path.exists():
            missing.append(str(path))
            continue
        text = actions_text(path)
        acted.append(text)
        for found in paths_in(text):
            # Both sides go through `resolved`, or the same file under two
            # spellings compares unequal and the check silently misses it.
            seen.add(os.path.normcase(found))
            seen.add(resolved(found))

    touched_refs = {p for p in seen if p in refs
                    or any(p.startswith(d + os.sep) for d in ref_dirs)}

    # A reference opened by bare filename, after a cd into its directory, names
    # no directory at all. The filenames are known, so look for them directly —
    # they are distinctive enough ("Stripe Reconciliation 2026-07.xlsx") that a
    # mention is worth reporting even when it is only a mention.
    # The backstop reads the same "what the run did" text, so a reference opened
    # by bare filename after a cd is caught, while one merely quoted back in a
    # tool result is not.
    # `refs` are normcased, so the comparison text has to be too, or on Windows
    # a lowercased name would never be found in the original-case transcript.
    names = {Path(r).name for r in refs}
    acted_text = os.path.normcase("\n".join(acted))
    for name in names:
        if name in acted_text and not any(name in p for p in touched_refs):
            touched_refs.add(f"(by name) {name}")
    touched_refs = sorted(touched_refs)
    return {
        "period": period,
        "transcripts": [str(p) for p in transcripts],
        "transcripts_missing": missing,
        "staging": str(staging) if staging else None,
        "references_checked": len(refs),
        "references_touched": touched_refs,
        # An audit with nothing to read proves nothing, and must not read as a pass.
        "verdict": ("not audited" if (missing or not transcripts)
                    else "contaminated" if touched_refs else "blind"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("agent_dir")
    ap.add_argument("period")
    ap.add_argument("--root", help="where staging directories are created")
    ap.add_argument("--audit", nargs="*", help="transcripts/logs of what ran, to check for reads")
    ap.add_argument("--out", help="write the audit result here as JSON")
    args = ap.parse_args()

    agent = Path(args.agent_dir).expanduser().resolve()

    if args.audit is not None:
        result = audit(agent, args.period, [Path(p).expanduser() for p in args.audit], None)
        if args.out:
            Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        if result["verdict"] == "contaminated":
            print(f"\n{args.period}: a reference was read while the candidate was being built. "
                  "The candidate is contaminated; if this period was a holdout, its blind "
                  "result is gone.", file=sys.stderr)
            return 1
        if result["verdict"] == "not audited":
            print(f"\n{args.period}: nothing to audit — blindness is unverified, not proven.",
                  file=sys.stderr)
            return 2
        return 0

    staging = stage(agent, args.period, Path(args.root).expanduser() if args.root else None)
    print(staging)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
