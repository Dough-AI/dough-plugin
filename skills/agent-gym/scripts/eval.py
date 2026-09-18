#!/usr/bin/env python3
"""Run the agent over its eval set and write one report.

  uv run --with pyyaml --with openpyxl eval.py <agent-dir> [--reveal-holdout] [--rebuild]

Order (the walk-forward rule):
  1. Development periods, oldest first: build a candidate, bridge it, and STOP
     at the first one that needs disposition. Train period by period — settle
     week 1 before week 2 is opened. `--all` runs them without stopping.
  2. Every holdout already revealed, as a REGRESSION — it proves nothing broke.
  3. At most ONE unseen holdout, revealed last, and only with --reveal-holdout.

A failing regression stops the run before any reveal: do not spend a holdout
proving a fix that has not been made. A holdout is single-use, so the report
carries a blind ledger of how many remain.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

GYM = Path(__file__).resolve().parent
REVEALED = "revealed.json"          # which holdouts have been spent, and when


def load_spec(agent: Path) -> dict:
    return yaml.safe_load((agent / "eval" / "eval.yaml").read_text())


def revealed_state(agent: Path) -> dict:
    path = agent / "eval" / REVEALED
    return json.loads(path.read_text()) if path.exists() else {}


def record_reveal(agent: Path, period: str, run: str) -> None:
    state = revealed_state(agent)
    state[period] = {"first_revealed_run": run,
                     "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (agent / "eval" / REVEALED).write_text(json.dumps(state, indent=2) + "\n")


def next_run_dir(agent: Path) -> Path:
    reports = agent / "eval" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    used = [int(p.name) for p in reports.iterdir() if p.is_dir() and p.name.isdigit()]
    path = reports / f"{max(used, default=0) + 1:04d}"
    path.mkdir()
    return path


def build(agent: Path, spec: dict, period: str, rebuild: bool) -> tuple[bool, str]:
    output = spec["outputs"][0]
    candidate = agent / output["candidate"]["file"].format(period=period)
    if candidate.exists() and not rebuild:
        return True, "candidate already built"
    command = output["build"].format(period=period)
    result = subprocess.run(command, shell=True, cwd=agent, capture_output=True, text=True)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip().splitlines()[-1][:300]
    return True, "built"


def bridge(agent: Path, period: str, out_dir: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(GYM / "compare.py"), str(agent), period, "--out", str(out_dir)],
        capture_output=True, text=True,
    )
    path = out_dir / f"{period}.bridge.json"
    if not path.exists():
        return {"period": period, "verdict": "error",
                "error": (result.stderr or result.stdout).strip().splitlines()[-1][:300] if (result.stderr or result.stdout) else "compare produced no bridge"}
    return json.loads(path.read_text())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("agent_dir")
    ap.add_argument("--reveal-holdout", action="store_true",
                    help="spend one unseen holdout at the end of this run")
    ap.add_argument("--rebuild", action="store_true", help="rebuild candidates that already exist")
    ap.add_argument("--all", action="store_true",
                    help="run every development period instead of stopping at the first "
                         "that needs disposition")
    args = ap.parse_args()

    agent = Path(args.agent_dir).expanduser().resolve()
    spec = load_spec(agent)
    state = revealed_state(agent)

    cases = sorted(spec["eval_set"], key=lambda c: c["period"])
    development = [c for c in cases if c.get("role") == "development"]
    holdouts = [c for c in cases if c.get("role") == "holdout"]
    spent = [c for c in holdouts if c["period"] in state]
    unseen = [c for c in holdouts if c["period"] not in state]

    run_dir = next_run_dir(agent)
    run = run_dir.name
    print(f"run {run} — {len(development)} development, {len(spent)} holdout regression(s), "
          f"{len(unseen)} unseen holdout(s)\n")

    results, stopped = [], None
    for case in development + spent:
        period = case["period"]
        role = "development" if case in development else "regression"
        ok, note = build(agent, spec, period, args.rebuild)
        if not ok:
            results.append({"period": period, "role": role, "verdict": "error", "error": note})
            print(f"  {period}  {role:<12} BUILD FAILED — {note}")
            stopped = f"build failed for {period}"
            break
        report = bridge(agent, period, run_dir)
        report["role"] = role
        results.append(report)
        line = f"  {period}  {role:<12} {report['verdict']}"
        if report.get("unexplained_steps"):
            line += f" ({report['unexplained_steps']} unexplained)"
        print(line + (f" — {report['error']}" if report.get("error") else ""))
        if role == "regression" and report["verdict"] != "pass":
            stopped = f"regression failed on {period}"
            break
        # Train one period at a time. Settling this period's differences before
        # the next is opened is what makes the agent generalise: a rule written
        # against six months at once is fitted to all six, and none of them was
        # ever a test. --all overrides, for a regression sweep of settled months.
        if role == "development" and report["verdict"] != "pass" and not args.all:
            stopped = (f"{period} needs disposition — settle it, then run again "
                       "to move on to the next period")
            break

    # The reveal comes last, and only if nothing above failed.
    if unseen and args.reveal_holdout and not stopped:
        case = unseen[0]
        period = case["period"]
        ok, note = build(agent, spec, period, args.rebuild)
        if ok:
            report = bridge(agent, period, run_dir)
            report["role"] = "holdout (blind)"
            results.append(report)
            record_reveal(agent, period, run)
            print(f"  {period}  {'holdout':<12} {report['verdict']} — revealed for the first time")
            unseen = unseen[1:]
        else:
            print(f"  {period}  holdout      BUILD FAILED — {note}")
    elif unseen and not args.reveal_holdout:
        print(f"\n  {len(unseen)} holdout(s) left unspent: {', '.join(c['period'] for c in unseen)}"
              "\n  (pass --reveal-holdout to spend one; a holdout is blind only once)")

    failed = [r for r in results if r.get("verdict") not in ("pass", None)]
    report = {
        "agent": spec.get("agent", agent.name),
        "run": run,
        "sequential": not args.all,
        "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "script",                     # the agent's build command, not an agent session
        "verdict": "pass" if not failed and not stopped else "needs disposition",
        "stopped": stopped,
        "blind_ledger": {
            "holdouts": [c["period"] for c in holdouts],
            "revealed": sorted(revealed_state(agent)),
            "unspent": [c["period"] for c in unseen],
        },
        "periods": [
            {k: v for k, v in r.items() if k != "bridges"} | {
                "components": [
                    {"component": b["component"], "kind": b["kind"],
                     **({"residual": b.get("residual")} if b["kind"] == "figure" else
                        {"population": b.get("population")}),
                     "passed": b.get("passed")}
                    for b in r.get("bridges", [])
                ]
            }
            for r in results
        ],
    }
    (run_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")

    print(f"\n{report['verdict']}"
          + (f" — stopped: {stopped}" if stopped else "")
          + f"\nreport: {run_dir / 'report.json'}")
    if report["blind_ledger"]["unspent"] == []:
        print("no unused holdouts remain: later runs are regression evidence only")
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
