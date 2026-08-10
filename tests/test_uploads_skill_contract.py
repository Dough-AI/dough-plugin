"""Drift check: the uploads skill must agree with the `tables.upload` contract
the tool actually enforces today, and with the datalake skill's multi-file flow.

Nothing here calls Dough. The uploads skill decides a table's SHAPE before the
datalake skill's 1b mechanics load it — so when the contract grows a structural
input (keyColumns, a scenario discriminator), the shape decision is where it has
to be made, and a skill that never mentions it produces tables the tool rejects
one file later.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
SKILL = ROOT / "skills" / "uploads" / "SKILL.md"


def flat(path):
    """Whole file as one line — the docs are hard-wrapped, so a phrase can span a
    newline and any match on raw text would be luck."""
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def near(text, anchor, *needles, window=400):
    """True when every needle appears within `window` chars after `anchor` — i.e.
    in the same explanation, not merely elsewhere in the file."""
    for match in re.finditer(re.escape(anchor), text):
        chunk = text[match.start() : match.start() + window]
        if all(n in chunk for n in needles):
            return True
    return False


def test_grain_is_tied_to_keyColumns():
    # The skill's grain sentence ("one row per vendor per month") IS what
    # tables.upload now demands as keyColumns on create. A skill that makes the
    # agent state the grain but never declare it as the key leaves the one
    # structural decision the tool enforces to improvisation.
    text = flat(SKILL)
    assert "keyColumns" in text, "uploads skill never mentions keyColumns"
    assert near(text, "grain", "keyColumns", window=600) or near(
        text, "keyColumns", "grain", window=600
    ), "uploads skill does not connect the grain statement to keyColumns"


def test_provenance_inputs_are_documented():
    text = flat(SKILL)
    assert "sourceLabel" in text, "uploads skill never mentions sourceLabel"
    assert "sourceUrl" in text, "uploads skill never mentions sourceUrl"
    assert near(text, "sourceLabel", "required"), (
        "uploads skill does not call sourceLabel required"
    )


def test_placeholder_values_route_to_nullTokens():
    # A parsed file spelling missing data as "N/A" or "-" should surface as
    # nullTokens on the upload, not as a column typed STRING to force it through.
    text = flat(SKILL)
    assert "nullTokens" in text, "uploads skill never mentions nullTokens"


def test_scenario_discriminator_is_not_forbidden():
    # The shape rules must not read as a blanket ban on columns absent from the
    # data: a multi-file scenario upload REQUIRES a discriminator synthesised
    # from each file's identity (tab name, file name) and added to the key.
    # "scenario" listed as an example of a forbidden invented column is the
    # verbatim spine of the old rule.
    text = flat(SKILL)
    assert not near(text, "invented constant columns", "scenario", window=120), (
        "uploads skill still lists `scenario` as an example of a forbidden column"
    )
    assert near(text, "discriminator", "keyColumns", window=500) or near(
        text, "identity", "keyColumns", window=500
    ), (
        "uploads skill never explains that a discriminator synthesised from "
        "source identity joins keyColumns"
    )


def test_multi_file_flow_lives_here():
    """The several-files workflow is this skill's, not a handoff.

    It used to sit in datalake 1b and be reached through a pointer. It moved
    because deciding what tells the files apart IS the shape decision this skill
    exists to make — the discriminator question and the versions question above
    it are one question. These assertions are the ones that used to guard
    `test_skill_contract.py::test_multi_file_uploads_are_documented`; the traps
    they describe are real, and `tests/fixtures/planning-week-2026-w32/` fires
    every one of them.
    """
    text = flat(SKILL)
    assert re.search(r"[Ss]everal files into one table", text), (
        "uploads skill has no multi-file section"
    )
    assert "union" in text.lower(), (
        "uploads skill never says to union the headers before creating — without "
        "it a later file adds-and-omits and is rejected"
    )
    # The discriminator is the non-obvious half: the value distinguishing the
    # files usually is not IN them, so the text has to say so rather than just
    # saying "add a column".
    assert re.search(r"not in the data|name of a tab|tab name", text), (
        "uploads skill never says the discriminator is usually absent from the "
        "data itself, so an agent will look for a column that is not there"
    )
    assert "one file per" in text.lower(), (
        "uploads skill never says to upload one file per call — concatenating "
        "gives every row the same provenance"
    )


def test_waiting_between_files_is_documented():
    """One table loads one upload at a time. Uploading a file per call and
    sending them back-to-back is the obvious reading of the section above, and
    it is refused on file 2, so the section has to say to wait.
    `tests/test_fake_mcp_rules.py` proves the refusal fires for the real
    fixture's bytes."""
    text = flat(SKILL)
    assert "upload_in_flight" in text, (
        "uploads skill never names the upload_in_flight refusal, so an agent "
        "that hits it has nothing to match it against"
    )
    assert re.search(r"still loading|finish before|one at a time", text), (
        "uploads skill never says to let a load finish before the next upload "
        "to that table"
    )


def test_the_in_flight_refusal_is_documented_as_retryable():
    """It is the ONE upload refusal that resending unchanged fixes, and the
    add-and-omit rejection it must be told apart from now lives in a different
    skill — so this text has to carry the contrast itself rather than relying on
    the reader having just read the other rule."""
    text = flat(SKILL)
    assert near(text, "upload_in_flight", "identical payload", window=700), (
        "uploads skill does not say to resend the identical payload after "
        "upload_in_flight — without it the refusal reads as a dead end"
    )
    assert re.search(r"[Nn]othing (was|is) recorded", text), (
        "uploads skill never says nothing was recorded, so an agent cannot tell "
        "whether retrying would double-load the rows"
    )
