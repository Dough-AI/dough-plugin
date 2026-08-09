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

The last two tests go further and drive the real planning-week fixture through
the flows a wrong run would use, so each trap the e2e expects the model to avoid
is demonstrated to be a real trap for those exact files: sending the next file
immediately, and planning the key from the columns the files actually have.
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


def land(fake, name="t"):
    """Poll until the outstanding load is ready — what an agent following the
    skill does between two uploads to one table.

    Every test that appends has to do this first, which is the point: an append
    sent while its predecessor is still loading is refused before it reaches any
    of the rules below, so without it these tests would all be asserting the
    same single refusal.
    """
    result = None
    for _ in range(fake.POLLS_TO_LAND):
        result = fake.table_status({"name": name, "kind": "uploaded"})
    assert result and result.get("state") == "ready", f"never became ready: {result}"
    return result


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
    land(fake)
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
    land(fake)
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
    land(fake)
    result = append(
        fake, "period,cost_center,scenario,amount\n2026-07-01,Sales,upside,175\n"
    )
    assert result["status"] == "running"


# --- adding and omitting in one upload ---------------------------------------


def test_an_append_that_adds_and_omits_is_rejected_naming_both_sides(fake):
    # No land() on purpose — see
    # test_a_malformed_append_is_told_about_the_malformed_part_first.
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
    land(fake)
    result = append(fake, "period,cost_center\n2026-08-01,Sales\n")
    assert result["columnsOmitted"] == ["amount"]


def test_an_append_that_only_adds_is_accepted(fake):
    """The other half, likewise: existing rows just have no value for it."""
    create(fake, BASE, ["period", "cost_center"])
    land(fake)
    result = append(
        fake, "period,cost_center,amount,headcount\n2026-08-01,Sales,150,12\n"
    )
    assert result["columnsAdded"] == ["headcount"]


# --- an upload still loading -------------------------------------------------


def test_an_append_sent_while_the_previous_load_runs_is_refused(fake):
    """The refusal USE-361 is about. Sending file 2 the instant file 1 returns is
    the obvious flow, and it is the one that fails."""
    create(fake, BASE, ["period", "cost_center"])
    with pytest.raises(fake.Rejected) as excinfo:
        append(fake, "period,cost_center,amount\n2026-08-01,Sales,150\n")
    reason = refusal(excinfo)
    assert "still loading" in reason
    # It has to say the CSV was not the problem, or an agent reads a refusal
    # naming its payload and starts editing the payload.
    assert "nothing was loaded" in reason


def test_nothing_is_recorded_when_an_upload_is_refused_as_still_loading(fake):
    """"Nothing was recorded" is a claim the refusal makes, so it is worth
    checking rather than trusting: a refusal that still moved the table's state
    would leave the retry landing on top of something."""
    create(fake, BASE, ["period", "cost_center"])
    before = fake.uploaded_tables()["t"]
    with pytest.raises(fake.Rejected):
        append(fake, "period,cost_center,amount\n2026-08-01,Sales,150\n")
    assert fake.uploaded_tables()["t"] == before


def test_the_identical_payload_succeeds_once_the_load_has_landed(fake):
    """The whole distinction the skill has to teach. Every other refusal here is
    permanent for the bytes that caused it — this one is not, and an agent that
    treats them the same either drops a file or edits a CSV that was correct."""
    create(fake, BASE, ["period", "cost_center"])
    payload = "period,cost_center,amount\n2026-08-01,Sales,150\n"
    with pytest.raises(fake.Rejected):
        append(fake, payload)
    land(fake)
    assert append(fake, payload)["status"] == "running"


def test_polling_the_default_kind_does_not_land_the_load(fake):
    """`tables.status` defaults to kind:"calculated", which never finds an
    uploaded table. An agent that polls without the kind has learned nothing and
    must still be refused — otherwise the wrong call would look like it worked."""
    create(fake, BASE, ["period", "cost_center"])
    for _ in range(fake.POLLS_TO_LAND * 2):
        assert fake.table_status({"name": "t"})["found"] is False
    with pytest.raises(fake.Rejected) as excinfo:
        append(fake, "period,cost_center,amount\n2026-08-01,Sales,150\n")
    assert "still loading" in refusal(excinfo)


def test_one_poll_is_not_enough(fake):
    """A single ping of tables.status is not "waiting for the load"; the skill
    says poll UNTIL it is ready. If one call sufficed, this test and the one
    above would be indistinguishable and the instruction would be untested."""
    create(fake, BASE, ["period", "cost_center"])
    assert fake.table_status({"name": "t", "kind": "uploaded"})["state"] == "loading"
    with pytest.raises(fake.Rejected) as excinfo:
        append(fake, "period,cost_center,amount\n2026-08-01,Sales,150\n")
    assert "still loading" in refusal(excinfo)


def test_a_create_is_never_refused_for_a_load_in_flight(fake):
    """Only an append compares against rows already in the table, so only an
    append has anything to wait for. A create lands on a different table."""
    create(fake, BASE, ["period", "cost_center"])
    assert create(fake, BASE, ["period", "cost_center"], name="other")["status"] == "running"


def test_a_replace_is_never_refused_for_a_load_in_flight(fake):
    """A replace discards whatever is there, so the rows it would have been
    checked against do not matter."""
    create(fake, BASE, ["period", "cost_center"])
    result = fake.upload(
        {
            "name": "t",
            "csv": BASE,
            "mode": "replace",
            "confirm": True,
            "sourceLabel": "Plan v2, from Finance",
        }
    )
    assert result["status"] == "running"


def test_a_malformed_append_is_told_about_the_malformed_part_first(fake):
    """Ordering, and it matches the real tool's: the column rules are checked
    before the baseline is needed. An append that both adds-and-omits AND
    arrives early must hear about the add-and-omit, because that is the half
    that waiting will not fix — reporting "still loading" would send the agent
    off to retry a payload that can never be accepted.
    """
    create(fake, BASE, ["period", "cost_center"])
    with pytest.raises(fake.Rejected) as excinfo:
        append(fake, "period,cost_center,headcount\n2026-08-01,Sales,12\n")
    reason = refusal(excinfo)
    assert "missing from the upload:" in reason
    assert "still loading" not in reason


# --- the fixture itself ------------------------------------------------------


def fixture_csv(stem):
    return (FIXTURE / f"fy26h2_plan_{stem}.csv").read_text(encoding="utf-8")


def test_sending_the_next_file_straight_away_is_refused(fake):
    """The first trap the naive flow hits, and the one that arrives earliest:
    file 2 sent as soon as file 1 returned, over the real fixture bytes."""
    create(fake, fixture_csv("base"), ["period", "cost_center", "account"], name="naive")
    with pytest.raises(fake.Rejected) as excinfo:
        append(fake, fixture_csv("upside"), name="naive")
    assert "still loading" in refusal(excinfo)


def test_the_naive_flow_over_the_real_fixture_hits_both_rejections(fake):
    """The planning-week e2e asserts the model was never refused. That assertion
    is only meaningful if these three files, uploaded the obvious way, WOULD
    refuse it — so prove it against the fixture bytes rather than a miniature.

    Both traps the fixture's README claims are demonstrated here: the natural key
    collides on the second file, and the third both adds and omits.

    Each upload is allowed to land first, so that what is demonstrated is the
    trap under test and not the timing one above it. That is the honest ordering
    anyway: an agent that waits properly and STILL plans its key from the natural
    columns is the one these two refusals are for, and it is the more likely
    mistake now that the skill tells it to wait.
    """
    natural_key = ["period", "cost_center", "account"]

    create(fake, fixture_csv("base"), natural_key, name="naive")
    land(fake, "naive")
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
