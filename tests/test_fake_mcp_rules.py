"""The fake Dough server's rejections, proved to fire. No model involved.

`tests/test_upload_e2e.py` asserts that a rejection HAPPENED; the planning-week
e2e asserts that one did NOT. That second shape is only worth anything if the
fake is capable of the rejection in the first place — an absence assertion
passes trivially against a server that can never refuse, which is a green test
that proves nothing.

So this file is the other half of `tests/test_planning_week_e2e.py`: it feeds
`tables__upload` the payloads a wrong run would send and asserts each one is
refused, and feeds it the near-misses that must still be accepted so the rules
are not merely "reject everything". It needs no auth and no tokens, so it runs
in the ordinary suite without DOUGH_E2E.

The last test goes further and drives the real planning-week fixture through the
naive one-file-at-a-time flow, so the trap the e2e expects the model to avoid is
demonstrated to be a real trap for those exact files.
"""

import importlib.util
import itertools
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
FAKE = TESTS / "fake_dough_mcp.py"
FIXTURE = TESTS / "fixtures" / "planning-week-2026-w32"

_counter = itertools.count()


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A freshly imported server with an empty log.

    The module reads $DOUGH_FAKE_LOG at import and holds it in a constant, so
    isolation means a new module object per test, not just a new file.
    """
    monkeypatch.setenv("DOUGH_FAKE_LOG", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("DOUGH_FAKE_SINK", "http://127.0.0.1:1")
    spec = importlib.util.spec_from_file_location(
        f"fake_dough_mcp_{next(_counter)}", FAKE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create(fake, csv, keys, name="t"):
    return fake.upload(
        {
            "name": name,
            "csv": csv,
            "mode": "create",
            "keyColumns": keys,
            "sourceLabel": "Plan v1, from Finance",
        }
    )


def append(fake, csv, name="t", **extra):
    return fake.upload(
        {
            "name": name,
            "csv": csv,
            "mode": "append",
            "sourceLabel": "Plan v1, from Finance",
            **extra,
        }
    )


def refusal(excinfo):
    return str(excinfo.value)


BASE = """period,cost_center,amount
2026-07-01,Sales,100
2026-07-01,Eng,200
"""


# --- duplicate keys ----------------------------------------------------------


def test_an_append_repeating_a_key_already_in_the_table_is_rejected(fake):
    """The planning-week collision in miniature: same key, different number,
    which is exactly what a second scenario of one plan looks like."""
    create(fake, BASE, ["period", "cost_center"])
    with pytest.raises(fake.Rejected) as excinfo:
        append(fake, "period,cost_center,amount\n2026-07-01,Sales,175\n")
    reason = refusal(excinfo)
    assert "duplicate key" in reason
    # Naming the offending keys is the point: a refusal that says only "duplicate
    # keys" leaves the reader to find them.
    assert 'period="2026-07-01"' in reason
    assert 'cost_center="Sales"' in reason


def test_only_the_colliding_rows_are_named(fake):
    """A rejection that named every row of the upload would be no more use than
    one that named none."""
    create(fake, BASE, ["period", "cost_center"])
    with pytest.raises(fake.Rejected) as excinfo:
        append(
            fake,
            "period,cost_center,amount\n2026-07-01,Eng,250\n2026-08-01,Eng,260\n",
        )
    reason = refusal(excinfo)
    assert 'period="2026-07-01"' in reason
    assert "2026-08-01" not in reason, f"named a row that does not collide: {reason}"


def test_a_csv_that_repeats_a_key_internally_is_rejected(fake):
    """The same failure inside one file, where there is no table to collide with
    — a concatenation of two scenarios sent as a single upload lands here."""
    doubled = BASE + "2026-07-01,Sales,175\n"
    with pytest.raises(fake.Rejected) as excinfo:
        create(fake, doubled, ["period", "cost_center"])
    reason = refusal(excinfo)
    assert "duplicate key" in reason and "within this upload" in reason
    assert 'cost_center="Sales"' in reason


def test_the_same_rows_under_a_wider_key_are_accepted(fake):
    """The positive control for the duplicate-key rule, and the fix the fixture
    is built around: the discriminator makes the colliding rows distinct."""
    create(
        fake,
        "period,cost_center,scenario,amount\n2026-07-01,Sales,base,100\n",
        ["period", "cost_center", "scenario"],
    )
    result = append(
        fake, "period,cost_center,scenario,amount\n2026-07-01,Sales,upside,175\n"
    )
    assert result["status"] == "running"


# --- adding and omitting in one upload ---------------------------------------


def test_an_append_that_adds_and_omits_is_rejected_naming_both_sides(fake):
    create(fake, BASE, ["period", "cost_center"])
    with pytest.raises(fake.Rejected) as excinfo:
        append(fake, "period,cost_center,headcount\n2026-08-01,Sales,12\n")
    reason = refusal(excinfo)
    assert "missing from the upload:" in reason and "would be added" in reason
    assert '"amount"' in reason, f"did not name the omitted column: {reason}"
    assert '"headcount"' in reason, f"did not name the added column: {reason}"


def test_an_append_that_only_omits_is_accepted(fake):
    """Half of the pair, on its own, is ordinary: the column is simply empty for
    the appended rows."""
    create(fake, BASE, ["period", "cost_center"])
    result = append(fake, "period,cost_center\n2026-08-01,Sales\n")
    assert result["columnsOmitted"] == ["amount"]


def test_an_append_that_only_adds_is_accepted(fake):
    """The other half, likewise: existing rows just have no value for it."""
    create(fake, BASE, ["period", "cost_center"])
    result = append(
        fake, "period,cost_center,amount,headcount\n2026-08-01,Sales,150,12\n"
    )
    assert result["columnsAdded"] == ["headcount"]


# --- the fixture itself ------------------------------------------------------


def fixture_csv(stem):
    return (FIXTURE / f"fy26h2_plan_{stem}.csv").read_text(encoding="utf-8")


def test_the_naive_flow_over_the_real_fixture_hits_both_rejections(fake):
    """The planning-week e2e asserts the model was never refused. That assertion
    is only meaningful if these three files, uploaded the obvious way, WOULD
    refuse it — so prove it against the fixture bytes rather than a miniature.

    Both traps the fixture's README claims are demonstrated here: the natural key
    collides on the second file, and the third both adds and omits.
    """
    natural_key = ["period", "cost_center", "account"]

    create(fake, fixture_csv("base"), natural_key, name="naive")
    with pytest.raises(fake.Rejected) as collision:
        append(fake, fixture_csv("upside"), name="naive")
    assert "duplicate key" in refusal(collision)

    # And the third file's shape is refused on its own terms, against a table
    # created from a header that did not anticipate it.
    with pytest.raises(fake.Rejected) as shape:
        append(fake, fixture_csv("downside"), name="naive")
    reason = refusal(shape)
    assert "missing from the upload:" in reason and "would be added" in reason
    assert '"headcount"' in reason and '"risk_note"' in reason
