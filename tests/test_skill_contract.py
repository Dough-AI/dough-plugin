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
