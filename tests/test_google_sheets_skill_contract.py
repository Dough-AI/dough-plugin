"""Drift check: the google-sheets skill must keep the claims that were measured.

Nothing here calls Google. `test_dough_sheets_live.py` proves the script keeps
the contract on a real spreadsheet; `test_google_sheets_e2e.py` proves a model
reading the skill produces one. This file's subject is the skill's own text and
the constants both scripts share, where every failure is silent: a rule that
loses its reason gets "simplified" away by the next reader, and a constant that
drifts between the Excel and Sheets scripts splits one manifest contract in two.

Each assertion pins a claim TOGETHER WITH ITS REASON, following
test_gws_connect_skill_contract.py. The baseline run — the real model asked for
saved queries in a Google Sheet before this skill existed — did three things the
skill has to rule out, and each has an assertion here: it pulled credentials out
of `gws auth export` to call the REST API itself, it replaced the spreadsheet
through a Drive upload on refresh (every tab got a new identity), and it wrote
its own 13KB Sheets client.
"""

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SKILL = ROOT / "skills" / "google-sheets" / "SKILL.md"
SCRIPT = ROOT / "skills" / "google-sheets" / "scripts" / "dough_sheets.py"
EXCEL_SCRIPT = ROOT / "skills" / "excel" / "scripts" / "dough_excel.py"
EXCEL_SKILL = ROOT / "skills" / "excel" / "SKILL.md"


def flat(path):
    """Whole file as one line — the docs are hard-wrapped, so a phrase can span a
    newline and any match on raw text would be luck."""
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def near(text, anchor, *needles, window=500):
    """True when every needle appears within `window` chars after `anchor` — i.e.
    in the same explanation, not merely elsewhere in the file."""
    for match in re.finditer(re.escape(anchor), text):
        chunk = text[match.start() : match.start() + window]
        if all(n in chunk for n in needles):
            return True
    return False


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_skill_exists_with_frontmatter():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "name: google-sheets" in text
    assert "description:" in text
    description = re.search(r"description: (.+)", text).group(1)
    assert "Dough" in description and "Google Sheet" in description


def test_contract_constants_match_excel():
    """One manifest contract, two hosts. The Sheets script cannot import the
    Excel one (a skill synced on its own has no sibling), so the constants are
    duplicated — and this is what stops them drifting apart."""
    sheets = load(SCRIPT)
    try:
        excel = load(EXCEL_SCRIPT)
    except ImportError:
        import pytest

        pytest.skip("openpyxl not installed; the Excel script cannot be imported")
    assert sheets.MANIFEST_SHEET == excel.MANIFEST_SHEET
    assert sheets.MANIFEST_VERSION == excel.MANIFEST_VERSION
    assert sheets.VERSION_MARKER == excel.VERSION_MARKER
    assert sheets.MANIFEST_HEADERS == excel.MANIFEST_HEADERS
    assert sheets.TITLE_TEXT == excel.TITLE_TEXT
    assert sheets.BANNER_TEMPLATE == excel.BANNER_TEMPLATE
    assert sheets.ROW_CAP == excel.ROW_CAP
    assert sheets.NUMERIC_PATTERN.pattern == excel.NUMERIC_PATTERN.pattern
    assert sheets.COL_WIDTHS == excel.COL_WIDTHS
    assert excel.DARK_FILL.fgColor.rgb.endswith(sheets.DARK_FILL)
    assert excel.HEADER_FILL.fgColor.rgb.endswith(sheets.HEADER_FILL)
    assert excel.BAND_FILL.fgColor.rgb.endswith(sheets.BAND_FILL)
    assert excel.TAB_COLOR == sheets.TAB_COLOR


def test_script_is_standard_library_only():
    """It runs with whatever python3 the machine has — no uv, no openpyxl.
    The excel script needs openpyxl and says so in its usage line; this one
    must not grow a dependency by accident."""
    text = SCRIPT.read_text(encoding="utf-8")
    imports = re.findall(r"^(?:from|import) (\w+)", text, re.MULTILINE)
    allowed = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else None
    third_party = {"openpyxl", "requests", "google", "googleapiclient", "gspread", "pandas"}
    assert not third_party & set(imports), sorted(third_party & set(imports))
    if allowed is not None:
        assert set(imports) <= allowed | {"__future__"}, sorted(set(imports) - allowed)
    assert "uv run" not in text


def test_script_shows_both_invocations():
    """`python3` is the macOS name; Windows has the py launcher. gws-connect
    already learnt this the hard way."""
    for text in (SCRIPT.read_text(encoding="utf-8"), flat(SKILL)):
        assert "python3" in text
        assert "py -3" in text


def test_skill_forbids_extracting_credentials():
    """Baseline: the model ran `gws auth export`, parsed the refresh token, and
    minted access tokens itself. A session holding a token is what the
    gws-connect skill's whole design avoids."""
    text = flat(SKILL)
    assert "gws auth export" in text
    assert near(text, "gws auth export", "never", "token") or near(text, "never pull credentials", "gws auth export")


def test_skill_says_refresh_clears_in_place_and_why():
    """Baseline: refresh re-uploaded the workbook through Drive; every tab got a
    new sheetId and any tab a person had added would have been gone. The rule
    must travel with its reason (deleting breaks formulas, permanently) or it
    reads as a style preference."""
    text = flat(SKILL)
    assert near(text, "deleting a tab", "formula", "permanently", window=300)
    assert near(text, "never deletes or replaces", "sheet id") or near(text, "keeps each managed tab's sheet id", "SUMIFS")
    assert near(text, "Drive upload", "every tab", window=300)
    script = SCRIPT.read_text(encoding="utf-8")
    assert "IN PLACE" in script
    assert '"deleteSheet": {"sheetId": target}' not in script, "the script must never delete the managed tab itself"


def test_skill_explains_why_content_is_staged():
    """The argv limit is invisible until it kills a run. Without the number and
    the symptom, the next reader 'simplifies' the script back to inline bodies
    and it works on their laptop and dies on a managed one."""
    text = flat(SKILL)
    assert near(text, "Why the script stages", "900", "137", "Windows", window=900)
    script = SCRIPT.read_text(encoding="utf-8")
    assert "ARG_BUDGET" in script
    budget = int(re.search(r"^ARG_BUDGET = (\d+)", script, re.MULTILINE).group(1))
    assert budget < 900, f"ARG_BUDGET={budget} is at or over the measured kill threshold"


def test_skill_routes_to_gws_connect_and_never_installs():
    text = flat(SKILL)
    assert "dough:gws-connect" in text
    assert near(text, "dough:gws-connect", "Do not install")


def test_skill_names_the_drive_file_scope_limits():
    """A folder made by hand in the Drive UI is invisible to a drive.file grant.
    Without this the model reports the grant as broken, or worse, asks for a
    wider scope."""
    text = flat(SKILL)
    assert "drive.file" in text
    assert near(text, "drive.file", "Dough", "invisible", window=500)
    assert near(text, "ask for its URL", "do not report the grant as broken", window=200)


def test_skill_teaches_formula_tabs_with_user_entered_and_small_bodies():
    text = flat(SKILL)
    assert "USER_ENTERED" in text
    assert near(text, "USER_ENTERED", "SUMIFS", window=600)
    assert near(text, "Keep each call's body small", "900", window=200)


def test_skill_records_parameters_in_refresh_notes():
    """Manifest v1 has no parameters column; the e2e asserts the notes carry
    them, so the skill has to say so."""
    text = flat(SKILL)
    assert near(text, "refresh_notes", "parameterised", "period_start", window=500)


def test_paging_guidance_matches_excel():
    """Both skills read the same tool. If one says maxRows and the other does
    not, a model loading both learns two contracts."""
    for path in (SKILL, EXCEL_SKILL):
        text = flat(path)
        assert "integrations.query.next" in text
        assert near(text, "PAGE size", "nextCursor", window=400)
    assert "maxRows" in flat(SKILL)


def test_skill_stays_readable():
    """Loaded on every spreadsheet task; the excel skill is ~1200 words."""
    words = len(SKILL.read_text(encoding="utf-8").split())
    assert words < 1600, words
