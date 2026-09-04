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


def skill_1b():
    section = re.search(r"## 1b\..*?(?=## \d)", flat(SKILL))
    assert section, "SKILL.md no longer has a '1b.' upload section"
    return section.group(0)


def upload_docs():
    """Both places that document tables.upload: SKILL.md's '1b' step and the
    reference guide (whose upload rules span several numbered gotchas)."""
    return {"SKILL.md 1b": skill_1b(), "guide": flat(GUIDE)}


def guide_docs():
    """The reference guide alone — for rules whose skill-level home is now the
    uploads skill, but whose deep tool-behaviour statement stays here."""
    return {"guide": flat(GUIDE)}


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

    THE GUIDE ONLY. The workflow itself moved to the uploads skill, where the
    "what tells these files apart" decision sits next to the rest of the shape
    decisions; `tests/test_uploads_skill_contract.py` guards it there. What stays
    here is the guide's item 7, which is the deep tool-behaviour reference both
    skills point at and which is interleaved with items 6 and 8 that did not
    move. `test_datalake_routes_several_files_to_the_uploads_skill` below guards
    the seam the move created.
    """
    for where, text in guide_docs().items():
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
    for where, text in guide_docs().items():
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
    for where, text in guide_docs().items():
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


def test_datalake_routes_several_files_to_the_uploads_skill():
    """The seam the move created, and the one thing that can silently rot.

    Section 1b used to carry the several-files workflow itself. Now it carries a
    pointer, and a datalake-only session handed three monthly extracts reaches
    the rules ONLY by following it — so the pointer has to fire on the right
    trigger. "Structure isn't settled" is not that trigger: three tidy CSVs that
    collide on their natural key have perfectly settled structure and still walk
    into both rejections. The route has to be keyed on there being several files
    landing in one table.

    `tests/test_planning_week_e2e.py::*[datalake]` is the behavioural half of
    this: it drives that exact session and fails if the routing does not work in
    practice. This test is the cheap half — it fails in 4 seconds when someone
    edits the pointer out, instead of in 5 billed minutes.
    """
    section = skill_1b()
    assert re.search(r"\*\*uploads\*\* skill", section), (
        "datalake 1b no longer names the uploads skill at all, so nothing routes "
        "a several-files session to the rules that moved there"
    )
    assert near(section, "uploads", "Several files into one table", window=400) or near(
        section, "Several files into one table", "uploads", window=400
    ), (
        "datalake 1b does not name the uploads skill's multi-file section as "
        "where to go — a bare 'see the uploads skill' makes the agent hunt"
    )
    # Keyed on the situation (several files, one table), not on the source being
    # messy — the planning-week fixture is three tidy CSVs.
    assert re.search(
        r"([Mm]ore than one file|[Ss]everal files|[Mm]ultiple files)[^.]{0,120}"
        r"(one table|same table|single table)",
        section,
    ), (
        "datalake 1b's route to the uploads skill is not keyed on several files "
        "landing in one table, so a session with three tidy extracts will not "
        "recognise itself in it"
    )


ONBOARDING = ROOT / "skills" / "getting-started" / "SKILL.md"


def test_onboarding_points_at_the_uploads_skill():
    """The entry skill has to name both directions out of it.

    It routed only to `datalake`, so an org onboarding through it met the
    mechanics and never learned that a skill exists for the hard case. That gap
    is silent in exactly the way this file's header describes: the agent reaches
    a subtotal-laden workbook, follows 1b, and loads a plausible wrong table.
    """
    text = flat(ONBOARDING)
    assert "`uploads`" in text, (
        "getting-started never mentions the uploads skill, so onboarding routes "
        "every load — tidy CSV and human-formatted workbook alike — to datalake 1b"
    )
    assert near(text, "`uploads`", "shape", window=500), (
        "getting-started names the uploads skill without saying what it decides, "
        "which gives an agent no basis for choosing it over 1b"
    )


def test_onboarding_still_routes_the_tidy_case_to_1b():
    """The pointer must not overcorrect. A clean CSV needs no shape decisions, and
    an onboarding that sends every upload through the full uploads flow would put
    a tie-out harness in front of a 20-row budget."""
    text = flat(ONBOARDING)
    assert near(text, "tidy CSV", "1b", window=200), (
        "getting-started does not say the tidy-CSV case is datalake 1b alone"
    )


PROPOSE = ROOT / "skills" / "propose" / "SKILL.md"
PROPOSE_COMMAND = ROOT / "commands" / "propose.md"
SCRIPT = ROOT / "skills" / "propose" / "scripts" / "collect_evidence.py"


def propose_description():
    """The `description:` line of the propose skill's frontmatter.

    Its own line in the file, and the highest-leverage string in the repo: it
    decides whether the skill LOADS. Everything below it is unreachable to an
    agent whose situation never matched this.
    """
    text = PROPOSE.read_text(encoding="utf-8")
    match = re.search(r"^description:(.*?)^---$", text, re.MULTILINE | re.DOTALL)
    assert match, "propose/SKILL.md frontmatter has no description:"
    return re.sub(r"\s+", " ", match.group(1)).strip()


def test_the_trigger_is_not_scoped_to_one_product():
    """The failure this guards is silent and total.

    An agent in a NetSuite-only org reading a QuickBooks-only trigger never
    matches it, never loads the skill, tells the human "you'll need to book this
    by hand" — and the proposal is never raised. Nothing downstream can recover
    it, because nothing downstream ran.

    Named products are allowed and help matching; naming ONE is the defect. The
    assertion is conditional rather than a required list so that adding a third
    target does not mean editing this test.
    """
    text = propose_description()
    named = [p for p in ("QuickBooks", "NetSuite") if p in text]
    assert len(named) != 1, (
        f"the trigger names {named[0]} and no other product — an org on a "
        "different system will not recognise itself in it. Name the others too, "
        "or name none and describe the act."
    )


def test_the_trigger_is_not_scoped_to_journal_entries():
    """The larger half of the same defect, and the one `grep -i quickbooks`
    cannot see.

    22 of the 24 currently proposable actions are not journal entries — they
    create and edit accounts, customers, vendors, items, invoices and transfers.
    A trigger whose every act noun is ledger-shaped ("journal entry", "accrual",
    "reclass") does not fire for "create that expense account" on the SHIPPED
    QuickBooks catalog, let alone for a purchase order later.
    """
    text = propose_description()
    # Word boundaries, not substrings: "account" otherwise matches inside
    # "accounting system" and passes on the very text this exists to reject,
    # and a bare "po" matches inside "proposal".
    others = re.findall(
        r"\b(accounts?|customers?|vendors?|items?|invoices?|purchase orders?|POs?)\b",
        text,
    )
    assert others, (
        "the trigger names no act beyond a journal entry, so it will not fire "
        "for the 22 proposable actions that are not one"
    )


def test_the_trigger_keeps_the_by_hand_clause():
    """The one trigger that survives a new target.

    Product names and act nouns both go stale as the catalog grows, and this
    file ships PINNED — a customer's disk keeps whatever list shipped with it.
    "About to tell someone to do it by hand" needs no maintenance and fires on
    a system nobody had heard of when this was written.
    """
    assert re.search(r"by hand", propose_description(), re.I), (
        "the trigger lost its 'by hand' clause — the only part of it that "
        "still fires for a target added after this version shipped"
    )


def test_the_trigger_is_not_scoped_to_an_accounting_system():
    """Category, not just acts. `create_invoice` and a future purchase order are
    writes to a system of record; "accounting system" is the narrower reading an
    agent can use to rule the skill out."""
    assert "accounting system" not in propose_description().lower(), (
        "the trigger scopes itself to an 'accounting system', which reads as "
        "the ledger and excludes the procurement and billing writes"
    )


def test_the_skill_does_not_suppress_the_rationale():
    """`rationale` is what the approver decides FROM, and it is never posted.

    The skill told the agent to leave it off, on the reasoning that attached
    evidence says the same thing twice on the queue page. The queue renders both
    distinctly, so the premise was wrong — and the skill's own flow makes
    evidence optional, so following the rule could leave an approver holding a
    payload, a 240-char ledger memo, and nothing else.
    """
    text = flat(PROPOSE)
    assert "Do not write a `rationale`" not in text, (
        "SKILL.md still tells the agent to suppress the rationale"
    )
    assert not re.search(r"[Dd]o not send a `?rationale`?", text), (
        "SKILL.md still tells the agent not to send a rationale"
    )
    assert near(text, "rationale", "based it on", window=600), (
        "SKILL.md does not tell the agent what a rationale must contain — "
        "'why this write, and what you based it on' is the standard"
    )


def test_the_command_does_not_suppress_the_rationale():
    """Same rule, second home. The command is the path that ALWAYS attaches
    evidence, so it is where the 'the evidence says it already' reasoning was
    most plausible — and it is still wrong, because its own tail lets the user
    proceed without evidence."""
    text = flat(PROPOSE_COMMAND)
    assert "Send no `rationale`" not in text, (
        "commands/propose.md still tells the agent to send no rationale"
    )


def test_the_posted_memo_is_still_ring_fenced():
    """The half of the rationale change that was right independently.

    `privateNote` and a NetSuite line `memo` are written into the customer's
    books permanently. Restoring the rationale must not reopen the memo as the
    place to explain yourself — those are opposite fields with opposite audiences.
    """
    text = flat(PROPOSE)
    assert near(text, "memo", "short", window=300), (
        "SKILL.md no longer says to keep the posted memo short"
    )


COMMANDS_DIR = ROOT / "commands"


def allowed_tools(command_path):
    """The `allowed-tools:` entries of a command's frontmatter, as a list.

    These decide whether a tool call is PRE-AUTHORIZED. An entry that never
    matches is not a no-op: the call falls through to whatever the session's
    permission mode does with an unauthorized tool, which in manual mode is a
    prompt a human clicks past — and in auto mode is a classifier that refuses.
    So a stale entry is invisible in manual testing and fatal in auto.
    """
    text = command_path.read_text(encoding="utf-8")
    match = re.search(r"^allowed-tools:(.*)$", text, re.MULTILINE)
    assert match, f"{command_path.name} has no allowed-tools: line"
    return [t.strip() for t in match.group(1).split(",") if t.strip()]


def test_propose_allows_the_interpreter_windows_actually_has():
    """Windows has no `python3`, so a rule naming only it can never match there.

    `python3` on Windows is an App Execution Alias that prints "Python was not
    found…" and exits without running anything. An agent on Windows correctly
    falls back to `python` — and lands outside `Bash(python3:*)`. Reproduced on
    a Windows VM: the evidence upload was refused by the auto-mode classifier,
    the proposal went out with no audit trail, and the same run passed in manual
    mode only because a human approved what the rule failed to cover.
    """
    tools = allowed_tools(PROPOSE_COMMAND)
    assert "Bash(python:*)" in tools, (
        "commands/propose.md does not allow Bash(python:*) — on Windows the "
        "evidence script cannot be run under any rule, so the upload is refused "
        "in auto mode and the proposal is raised unbacked"
    )
    assert "Bash(python3:*)" in tools, (
        "commands/propose.md dropped Bash(python3:*) — macOS and Linux agents "
        "use python3 and would now be the ones refused"
    )


def test_propose_does_not_hardcode_python3_in_its_instructions():
    """The body must not tell a Windows agent to run a command that cannot work.

    Every invocation is written `<py> <script>`, with the platform choice stated
    once. A literal `python3 <script>` reintroduces the failure the allow-list
    fix above only half-covers: the agent obeys, hits the Store stub, and has to
    improvise its way back to a command that may not match a rule.
    """
    text = PROPOSE_COMMAND.read_text(encoding="utf-8")
    body = text.split("---", 2)[-1]
    assert "python3 <script>" not in body, (
        "commands/propose.md instructs `python3 <script>` — that command does "
        "not run on Windows. Use the `<py> <script>` placeholder."
    )
    assert "<py> <script>" in body, "the `<py> <script>` placeholder is gone"


def test_propose_attaches_evidence_through_the_cli():
    """The attach step must name `dough evidence upload`, not a plan file.

    The plan file was a join the MODEL performed by hand: `declare` printed
    `paths` to stdout, `evidence.begin` returned `uploads` over MCP, and the
    model was the only place the two met. Step 5 told it to write the join to a
    file and named no tool for the job, so the agent improvised — on macOS, a
    shell heredoc carrying a signed-URL JWT, which the auto-mode classifier
    refused. `dough evidence upload` does the join itself, so no URL and no token
    ever reaches the model.
    """
    body = PROPOSE_COMMAND.read_text(encoding="utf-8").split("---", 2)[-1]
    assert "dough evidence upload" in body, (
        "commands/propose.md no longer routes evidence through the CLI - the "
        "model is back to joining `paths` and `uploads` by hand"
    )


def test_propose_forbids_building_the_plan_file_with_a_heredoc():
    """The fallback still writes a plan file. It must name the tool for it.

    An instruction that says "write a file" and names no tool is what produced
    the improvisation in the first place. On the fallback path the plan still
    embeds a signed URL and token, so a heredoc there fails exactly as it did
    before.
    """
    body = PROPOSE_COMMAND.read_text(encoding="utf-8").split("---", 2)[-1]
    assert "Write tool" in body, (
        "the fallback does not name the Write tool, so an agent will improvise "
        "the plan file again"
    )
    assert "heredoc" in body, (
        "the fallback no longer warns against a shell heredoc - that is the "
        "exact command the auto-mode classifier refuses"
    )


def test_propose_allows_a_file_writing_tool():
    """`allowed-tools` must permit the fallback's file write.

    A rule that never matches is not a no-op: the call falls through to whatever
    the permission mode does with an unauthorized tool, which in auto mode is a
    classifier that refuses. The fallback writes a plan file, so `Write` has to
    be allowed or the fallback fails the same way the heredoc did.
    """
    tools = allowed_tools(PROPOSE_COMMAND)
    assert "Write" in tools, (
        "commands/propose.md does not allow Write - the evidence fallback writes "
        "a plan file and would be refused in auto mode"
    )
    assert "Bash(dough:*)" in tools, (
        "commands/propose.md dropped Bash(dough:*) - the primary evidence path "
        "is a `dough` invocation and nothing else authorizes it"
    )


def test_propose_keeps_the_python_fallback_runnable():
    """The CLI path needs a binary that may not be installed yet.

    `dough evidence` ships in a CLI release that rolls out separately from this
    plugin, and `dough plugin refresh` does not upgrade the binary. A new plugin
    against an old CLI must not dead-end, so the script and its interpreter rules
    stay until that skew is gone.
    """
    body = PROPOSE_COMMAND.read_text(encoding="utf-8").split("---", 2)[-1]
    assert "<py> <script> upload --plan" in body, "the fallback upload is gone"
    assert "<py> <script> declare" in body, "the fallback declare is gone"
    assert SCRIPT.exists(), "collect_evidence.py was deleted while the fallback still needs it"


def test_every_dough_mcp_tool_is_allowed_under_all_three_server_names():
    """One tool, three ids, depending on how the customer connected Dough.

    A plugin-provided server is namespaced `mcp__plugin_<plugin>_<server>__…`
    and the claude.ai connector is `mcp__claude_ai_dough__…`; the bare
    `mcp__dough__…` we used to write alone matches neither. That mismatch is why
    `proposals.evidence.begin` was refused for every auto-mode user rather than
    for some unlucky subset.
    """
    for command in sorted(COMMANDS_DIR.glob("*.md")):
        tools = allowed_tools(command)
        for tool in tools:
            if not tool.startswith("mcp__dough__"):
                continue
            suffix = tool[len("mcp__dough__"):]
            for prefix in ("mcp__plugin_dough_dough__", "mcp__claude_ai_dough__"):
                assert prefix + suffix in tools, (
                    f"{command.name} allows {tool} but not {prefix}{suffix} — "
                    "the tool is unauthorized for anyone who connected Dough "
                    "that way, and auto mode refuses it"
                )
