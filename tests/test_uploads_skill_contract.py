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


def test_multi_file_flow_defers_to_datalake_section():
    # Mechanics (union headers, one upload per file) live in the datalake skill's
    # "Several files into one table" — the uploads skill should hand off, not
    # duplicate or contradict.
    text = flat(SKILL)
    assert re.search(r"[Ss]everal files into one table", text), (
        "uploads skill never points at the datalake skill's multi-file section"
    )
