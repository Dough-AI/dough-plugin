"""Drift check: the datalake skill must document the `tables.upload` contract that
the tool actually enforces today.

Nothing here calls Dough. These assertions exist because the failure mode is
silent: the tool changes, the skill keeps describing the old contract, and every
agent that follows it builds a payload the server rejects.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
SKILL = ROOT / "skills" / "datalake" / "SKILL.md"
GUIDE = ROOT / "skills" / "references" / "dough-datalake-guide.md"


def flat(path):
    """Whole file as one line — the docs are hard-wrapped, so a phrase can span a
    newline and any match on raw text would be luck."""
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def upload_docs():
    """Both places that document tables.upload: SKILL.md's '1b' step and the
    reference guide (whose upload rules span several numbered gotchas)."""
    skill = flat(SKILL)
    section = re.search(r"## 1b\..*?(?=## \d)", skill)
    assert section, "SKILL.md no longer has a '1b.' upload section"
    return {"SKILL.md 1b": section.group(0), "guide": flat(GUIDE)}


def near(text, anchor, *needles, window=400):
    """True when every needle appears within `window` chars after `anchor` — i.e.
    in the same explanation, not merely elsewhere in the file."""
    for match in re.finditer(re.escape(anchor), text):
        chunk = text[match.start() : match.start() + window]
        if all(n in chunk for n in needles):
            return True
    return False


def test_new_upload_inputs_are_documented():
    for where, text in upload_docs().items():
        assert "sourceLabel" in text, f"{where} never mentions sourceLabel"
        assert "sourceUrl" in text, f"{where} never mentions sourceUrl"
        assert "keyColumns" in text, f"{where} never mentions keyColumns"


def test_sourceLabel_is_documented_as_required():
    for where, text in upload_docs().items():
        assert near(text, "sourceLabel", "required"), f"{where} does not call sourceLabel required"


def test_keyColumns_is_documented_as_required_on_create():
    for where, text in upload_docs().items():
        assert near(text, "keyColumns", "required", "create"), (
            f"{where} does not tie keyColumns to being required on create"
        )


def test_append_is_not_documented_as_requiring_identical_columns():
    # "exactly the same columns" is the verbatim spine of the retired rule
    # ("An append must have exactly the same columns"). The current contract
    # explicitly allows an append to add OR omit a column, so this phrase can
    # only survive as stale text.
    # "missing or extra column" / "null-filled" come from the same retired
    # sentence ("A missing or extra column is rejected, not null-filled") —
    # omitting a non-key column is now allowed and IS null-filled.
    stale = ["exactly the same columns", "missing or extra column", "null-filled"]
    for path in (SKILL, GUIDE):
        text = flat(path)
        for phrase in stale:
            assert phrase not in text, f"{path.name} still carries the old rule: {phrase!r}"


def test_append_column_rules_are_documented():
    for where, text in upload_docs().items():
        # A non-key column may be omitted — the correction to the retired rule.
        assert re.search(r"non-key column[^.]*(omit|leave out|left out)", text), (
            f"{where} does not say a non-key column may be omitted"
        )
        # Add-and-omit in one upload is the rejected combination, and it is the
        # one an agent will otherwise retry unchanged, so the docs must say the
        # retry is futile as well as that the combination fails.
        assert re.search(r"(reject|refus)", text, re.I), f"{where} states no rejection at all"
        assert re.search(r"identical|same payload", text), (
            f"{where} does not warn that resending the same payload fails again"
        )
        # The remedy: split a genuine rename across two uploads.
        assert "two uploads" in text, f"{where} does not give the two-upload remedy"


def test_multi_file_uploads_are_documented():
    """Both traps a naive file-at-a-time flow walks into.

    They are not stylistic advice — `tests/fixtures/planning-week-2026-w32/`
    contains three real scenario extracts where each one fires: the second file
    duplicates the first on the natural key, and the third both adds a column and
    omits one. A skill that stops explaining either is a skill that lets an agent
    hit them.
    """
    for where, text in upload_docs().items():
        assert "union" in text.lower(), (
            f"{where} never says to union the headers before creating — without it "
            "a later file adds-and-omits and is rejected"
        )
        # The discriminator is the non-obvious half: the value distinguishing the
        # files usually is not IN them, so the text has to say so rather than just
        # saying "add a column".
        assert re.search(r"not in the data|name of a tab|tab name", text), (
            f"{where} never says the discriminator is usually absent from the data "
            "itself, so an agent will look for a column that is not there"
        )
        assert "one file per" in text.lower(), (
            f"{where} never says to upload one file per call — concatenating gives "
            "every row the same provenance"
        )


def test_waiting_between_files_is_documented():
    """The third trap, and the one the docs acquired last.

    An append is refused while the previous upload to that table is still
    loading, because its keys are checked against rows that have not landed yet.
    Uploading a file per call and sending them back-to-back is the obvious
    reading of the section above, and it is refused on file 2 — so the section
    has to say to wait. `tests/test_fake_mcp_rules.py` proves the refusal fires
    for the real fixture's bytes.
    """
    for where, text in upload_docs().items():
        assert "upload_in_flight" in text, (
            f"{where} never names the upload_in_flight refusal, so an agent that "
            "hits it has nothing to match it against"
        )
        assert re.search(r"still loading|finish before|one at a time", text), (
            f"{where} never says to let a load finish before the next upload to "
            "that table"
        )


def test_the_in_flight_refusal_is_documented_as_retryable():
    """It is the ONE upload refusal that resending unchanged fixes, and it sits
    next to the one where resending unchanged is futile. A doc that describes it
    as a failure gets a file abandoned; one that fails to distinguish it from
    add-and-omit gets a correct CSV edited until it is wrong.
    """
    for where, text in upload_docs().items():
        # "identical payload", not just "identical": the add-and-omit rule a few
        # lines away says a retry "fails identically", so the looser needle would
        # match that and pass on a doc that never gave the remedy.
        assert near(text, "upload_in_flight", "identical payload", window=700), (
            f"{where} does not say to resend the identical payload after "
            "upload_in_flight — without it the refusal reads as a dead end"
        )
        assert re.search(r"[Nn]othing (was|is) recorded", text), (
            f"{where} never says nothing was recorded, so an agent cannot tell "
            "whether retrying would double-load the rows"
        )
