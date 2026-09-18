"""Tests for the agent gym's comparison.

Every test here is built so it FAILS without the behaviour it names: a test that
passes both before and after a change measures nothing. The controls at the end
are the important ones — a clean bridge proves nothing on its own.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

COMPARE = Path(__file__).parent.parent / "skills" / "agent-gym" / "scripts" / "compare.py"

EVAL_YAML = """
agent: test-agent
period: month
outputs:
  - id: reclass
    kind: workbook
    build: "true"
    candidate:
      file: output/{period}/candidate.xlsx
    reference:
      file: refs/{period}/reference.xlsx
    components:
      - id: lines
        kind: rows
        reference: {sheet: Reclass, header_row: 1, key: [txn, vendor], amount: amount,
                     fields: {target: to_account}}
        candidate: {sheet: Lines, header_row: 1, key: [txn, vendor], amount: amount,
                     fields: {target: proposed_account}, filter: {verdict: RECLASS}}
      - id: total
        kind: figure
        metric: exact
        reference: {sheet: Summary, label: Total, label_col: A, value_col: B}
        candidate: {sheet: Summary, label: Total, label_col: A, value_col: B}
eval_set:
  - {period: 2026-06, role: development}
"""


def write_book(path: Path, sheets: dict) -> None:
    """sheets: {name: [[row], [row]]}, written from A1."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r, row in enumerate(rows, start=1):
            for c, value in enumerate(row, start=1):
                ws.cell(row=r, column=c, value=value)
    wb.save(path)


def build_agent(tmp_path: Path, *, candidate_rows=None, reference_rows=None,
                candidate_total=300.0, reference_total=300.0) -> Path:
    agent = tmp_path / "agent"
    (agent / "eval").mkdir(parents=True)
    (agent / "eval" / "eval.yaml").write_text(EVAL_YAML)

    candidate_rows = candidate_rows if candidate_rows is not None else [
        ["t1", "Acme", 100.0, "Travel", "RECLASS"],
        ["t2", "Globex", 200.0, "Meals", "RECLASS"],
        ["t3", "Initech", 55.0, "", "OK"],          # not in the population under test
    ]
    reference_rows = reference_rows if reference_rows is not None else [
        ["t1", "Acme", 100.0, "Travel"],
        ["t2", "Globex", 200.0, "Meals"],
    ]
    write_book(agent / "output" / "2026-06" / "candidate.xlsx", {
        "Lines": [["txn", "vendor", "amount", "proposed_account", "verdict"], *candidate_rows],
        "Summary": [["Total", candidate_total]],
    })
    write_book(agent / "refs" / "2026-06" / "reference.xlsx", {
        "Reclass": [["txn", "vendor", "amount", "to_account"], *reference_rows],
        "Summary": [["Total", reference_total]],
    })
    return agent


def compare(agent: Path, out: Path, period="2026-06"):
    result = subprocess.run(
        [sys.executable, str(COMPARE), str(agent), period, "--out", str(out), "--no-recalc"],
        capture_output=True, text=True,
    )
    bridge_path = out / f"{period}.bridge.json"
    bridge = json.loads(bridge_path.read_text()) if bridge_path.exists() else None
    return result, bridge


def steps_of(bridge, component):
    return next(b for b in bridge["bridges"] if b["component"] == component)["steps"]


def test_matching_month_passes(tmp_path):
    result, bridge = compare(build_agent(tmp_path), tmp_path / "out")
    assert result.returncode == 0
    assert bridge["verdict"] == "pass"
    assert bridge["unexplained_steps"] == 0


def test_filter_excludes_rows_outside_the_population(tmp_path):
    """Without the filter the OK row reads as only-in-candidate and swamps the bridge."""
    _, bridge = compare(build_agent(tmp_path), tmp_path / "out")
    population = next(b for b in bridge["bridges"] if b["component"] == "lines")["population"]
    assert population == {"matched": 2, "only_reference": 0, "only_candidate": 0, "mismatched": 0}


def test_amount_difference_is_reported_to_the_cent(tmp_path):
    agent = build_agent(tmp_path, candidate_rows=[
        ["t1", "Acme", 112.34, "Travel", "RECLASS"],
        ["t2", "Globex", 200.0, "Meals", "RECLASS"],
    ])
    _, bridge = compare(agent, tmp_path / "out")
    assert bridge["verdict"] == "needs disposition"
    # keyed on txn+vendor, so the row matches and its amount differs: one step,
    # carrying the delta to the cent rather than either gross figure
    steps = steps_of(bridge, "lines")
    assert [(s["amount"], "amount differs" in s["note"]) for s in steps] == [(12.34, True)]


def test_field_difference_is_caught_without_any_dollar_delta(tmp_path):
    """Same row, same amount, different target account: no delta to give it away."""
    agent = build_agent(tmp_path, candidate_rows=[
        ["t1", "Acme", 100.0, "Office expenses", "RECLASS"],
        ["t2", "Globex", 200.0, "Meals", "RECLASS"],
    ])
    _, bridge = compare(agent, tmp_path / "out")
    notes = [s["note"] for s in steps_of(bridge, "lines")]
    assert any("target differs" in n and "Travel" in n and "Office expenses" in n for n in notes)


def test_missing_row_is_reported_with_its_amount(tmp_path):
    agent = build_agent(tmp_path, candidate_rows=[["t1", "Acme", 100.0, "Travel", "RECLASS"]])
    _, bridge = compare(agent, tmp_path / "out")
    steps = steps_of(bridge, "lines")
    assert [(s["amount"], "only in reference" in s["note"]) for s in steps] == [(200.0, True)]


def test_figure_residual(tmp_path):
    agent = build_agent(tmp_path, candidate_total=277.7)
    _, bridge = compare(agent, tmp_path / "out")
    figure = next(b for b in bridge["bridges"] if b["component"] == "total")
    assert figure["residual"] == -22.3


def test_duplicate_sheet_is_refused(tmp_path):
    """A rebuild that appends model sheets leaves stale ones in front; reading by
    name would report the old numbers as the new ones."""
    agent = build_agent(tmp_path)
    path = agent / "output" / "2026-06" / "candidate.xlsx"
    wb = load_workbook(path)
    wb.copy_worksheet(wb["Lines"]).title = "Lines1"
    wb.save(path)
    result, bridge = compare(agent, tmp_path / "out")
    assert result.returncode != 0
    assert "appears 2 times" in (result.stderr + result.stdout)
    assert bridge is None


def test_unknown_output_kind_fails_loudly(tmp_path):
    agent = build_agent(tmp_path)
    spec = (agent / "eval" / "eval.yaml").read_text().replace("kind: workbook", "kind: google_sheet")
    (agent / "eval" / "eval.yaml").write_text(spec)
    result, _ = compare(agent, tmp_path / "out")
    assert result.returncode != 0
    assert "cannot read" in (result.stderr + result.stdout)


def test_missing_column_names_what_it_wanted(tmp_path):
    agent = build_agent(tmp_path)
    path = agent / "refs" / "2026-06" / "reference.xlsx"
    wb = load_workbook(path)
    wb["Reclass"]["D1"] = "account"            # was to_account
    wb.save(path)
    result, _ = compare(agent, tmp_path / "out")
    assert "to_account" in (result.stderr + result.stdout)


def test_undisposed_file_lists_every_open_difference(tmp_path):
    agent = build_agent(tmp_path, candidate_total=277.7)
    out = tmp_path / "out"
    _, bridge = compare(agent, out)
    text = (out / "2026-06.undisposed.yaml").read_text()
    for b in bridge["bridges"]:
        for step in b["steps"]:
            assert step["id"] in text
    assert "accepted_by:" in text


def test_a_disposition_settles_the_difference(tmp_path):
    agent = build_agent(tmp_path, candidate_total=277.7)
    _, bridge = compare(agent, tmp_path / "out1")
    step = steps_of(bridge, "total")[0]
    (agent / "eval" / "dispositions.yaml").write_text(
        "dispositions:\n"
        f"  - id: {step['id']}\n"
        f"    amount: {step['amount']}\n"
        "    reason: judgment\n"
        "    note: below materiality; the accountant booked it anyway\n"
        "    accepted_by: someone@example.com\n"
        "    accepted_at: 2026-09-18\n"
    )
    result, after = compare(agent, tmp_path / "out2")
    assert result.returncode == 0
    assert after["verdict"] == "pass"
    assert after["disposed_steps"] == 1
    settled = steps_of(after, "total")[0]
    assert settled["reason"] == "judgment"
    assert settled["accepted_by"] == "someone@example.com"


def test_a_disposition_stops_applying_when_the_amount_changes(tmp_path):
    """The control on disposition: a ruling covers THAT difference, not that
    line for ever."""
    agent = build_agent(tmp_path, candidate_total=277.7)
    _, bridge = compare(agent, tmp_path / "out1")
    step = steps_of(bridge, "total")[0]
    (agent / "eval" / "dispositions.yaml").write_text(
        "dispositions:\n"
        f"  - id: {step['id']}\n"
        "    reason: judgment\n"
        "    accepted_by: someone@example.com\n"
    )
    # the same figure now differs by a different amount
    path = agent / "output" / "2026-06" / "candidate.xlsx"
    wb = load_workbook(path)
    wb["Summary"]["B1"] = 250.0
    wb.save(path)
    result, after = compare(agent, tmp_path / "out2")
    assert result.returncode != 0
    assert after["verdict"] == "needs disposition"
    assert steps_of(after, "total")[0]["reason"] == "unexplained"


def test_a_bad_reason_is_refused(tmp_path):
    agent = build_agent(tmp_path, candidate_total=277.7)
    (agent / "eval" / "dispositions.yaml").write_text(
        "dispositions:\n  - id: abc123\n    reason: fine-i-guess\n    accepted_by: a@b.com\n"
    )
    result, _ = compare(agent, tmp_path / "out")
    assert "is not a reason" in (result.stderr + result.stdout)


def test_a_disposition_without_a_name_is_refused(tmp_path):
    agent = build_agent(tmp_path)
    (agent / "eval" / "dispositions.yaml").write_text(
        "dispositions:\n  - id: abc123\n    reason: judgment\n"
    )
    result, _ = compare(agent, tmp_path / "out")
    assert "accepted_by" in (result.stderr + result.stdout)
