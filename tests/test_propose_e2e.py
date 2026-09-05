"""End-to-end: run the real model against a fake Dough MCP and inspect what it sent.

This is the only test that exercises `commands/propose.md` itself. Everything
else tests the script; this tests the instructions.

It costs tokens and takes ~a minute, so it is opt-in:

    DOUGH_E2E=1 .venv/bin/python -m pytest tests/test_propose_e2e.py -v -s

Assertions are invariants, not transcripts of behaviour — a model may
legitimately order its work differently. What must hold is that evidence was
declared before it was proposed, that the bytes really arrived intact, and that
the proposal payload stayed small.

Evidence travels via `dough evidence upload`, not over MCP, so a fake `dough`
(tests/fake_dough.py) stands in for the binary. The fake MCP still SERVES
`proposals.evidence.begin`, deliberately: the removed path has to remain
available for `test_the_removed_mcp_path_is_never_taken` to be worth anything.
"""

import hashlib
import http.server
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
FAKE_MCP = REPO / "tests" / "fake_dough_mcp.py"
FAKE_CLI = REPO / "tests" / "fake_dough.py"
COMMAND = REPO / "commands" / "propose.md"

# The transcript alone is hundreds of KB. If any of it got inlined into the tool
# call, this ceiling is the thing that notices.
MAX_TRANSCRIPT_ARG_BYTES = 8_000

pytestmark = pytest.mark.skipif(
    not os.environ.get("DOUGH_E2E"),
    reason="costs tokens and needs auth; set DOUGH_E2E=1 to run",
)


def command_body():
    """The command's instructions, with the harness-provided bits filled in.

    The `propose` skill is not loadable headless, so that one line is replaced
    with the accounting facts it would have established. Everything else — the
    whole evidence flow this test exists to check — is verbatim.
    """
    text = COMMAND.read_text(encoding="utf-8")
    body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.DOTALL)
    body = body.replace("${CLAUDE_PLUGIN_ROOT}", str(REPO))
    body = body.replace(
        "Load the `propose` skill and follow it to build the entry. $ARGUMENTS",
        "Propose this journal entry to quickbooks (kind `journal_entry`):\n"
        "accrue the September contractor invoices in ./contractors.csv.\n"
        "Debit account id 54, credit account id 33, for the CSV's total.\n"
        "Use txnDate 2026-09-30.",
    )
    return body


def read_log(path):
    """Events in the order they were appended.

    Several processes write here across a run — the MCP server (respawned each
    turn) and the sink — so line position is the only trustworthy ordering.
    """
    entries = []
    if path.exists():
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if line.strip():
                entries.append({**json.loads(line), "_line": index})
    return entries


def start_sink(log, uploads):
    """The PUT target evidence.begin hands out.

    Owned by the test, not by the MCP server: Claude Code respawns the server
    every turn, so a sink living in there would change port mid-run and every
    previously-minted URL would start refusing connections.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_PUT(self):
            body = self.rfile.read(int(self.headers.get("content-length", 0)))
            parts = self.path.strip("/").split("/", 1)
            evidence_id, key = parts if len(parts) > 1 else ("", parts[0])
            (uploads / f"{evidence_id}_{key}").write_bytes(body)
            with open(log, "a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "kind": "upload",
                            "evidenceId": evidence_id,
                            "key": key,
                            "bytes": len(body),
                            "sha256": hashlib.sha256(body).hexdigest(),
                        }
                    )
                    + "\n"
                )
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def run_agent(tmp_path_factory):
    work = tmp_path_factory.mktemp("propose_e2e")
    log = work / "calls.jsonl"
    uploads = work / "uploads"
    uploads.mkdir()

    # The evidence the agent should discover, read, and attach.
    (work / "contractors.csv").write_text(
        "vendor,invoice,amount\n"
        "Acme Consulting,4471,5200.50\n"
        "Nordic Design,4482,3200.00\n",
        encoding="utf-8",
    )

    sink = start_sink(log, uploads)
    sink_url = f"http://127.0.0.1:{sink.server_address[1]}"

    # `dough evidence upload` is now the ONLY evidence path, so the agent needs a
    # `dough` on PATH. The real binary is built from Dough-Alpha and needs a
    # login; tests/fake_dough.py stands in at the interface (and is covered by
    # tests/test_fake_dough.py, which runs without DOUGH_E2E).
    bin_dir = work / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "dough"
    shim.write_text(
        "#!/bin/sh\n"
        f'DOUGH_FAKE_LOG="{log}" DOUGH_FAKE_SINK="{sink_url}" '
        f'exec "{sys.executable}" "{FAKE_CLI}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    agent_env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}

    mcp_config = work / "mcp.json"
    mcp_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "dough": {
                        "command": sys.executable,
                        "args": [str(FAKE_MCP)],
                        "env": {
                            "DOUGH_FAKE_LOG": str(log),
                            "DOUGH_FAKE_SINK": sink_url,
                        },
                    }
                }
            }
        )
    )

    def agent(prompt, *extra):
        return subprocess.run(
            [
                "claude", "-p", prompt,
                "--mcp-config", str(mcp_config),
                "--strict-mcp-config",
                # Deliberately WITHOUT Write and without evidence.begin. Both
                # existed only for the removed plan-file path; leaving either
                # here would let a regression pass by taking the old route.
                "--allowedTools", "Bash", "Read",
                "mcp__dough__proposals__propose",
                "--permission-mode", "bypassPermissions",
                *extra,
            ],
            cwd=work,
            env=agent_env,
            capture_output=True,
            text=True,
            timeout=600,
        )

    # Two turns on purpose. The command tells the agent to stop for confirmation
    # before uploading, so a one-turn run either stalls at that gate or has to be
    # told to ignore it — and an instruction to skip the gate is precisely the
    # instruction that would make this test meaningless. So: turn one is expected
    # to stop AT the gate, turn two answers it.
    first = agent(command_body())
    at_gate = read_log(log)

    second = agent("Yes — upload the evidence and raise the proposal.", "--continue")

    # Keep the agent's own account of both turns; when an assertion fails, what
    # it said is the fastest way to see what it did.
    (work / "agent-turn1.txt").write_text(first.stdout, encoding="utf-8")
    (work / "agent-turn2.txt").write_text(second.stdout, encoding="utf-8")
    print(f"\n  run artifacts: {work}")

    entries = read_log(log)
    sink.shutdown()
    if not entries:
        pytest.fail(
            "the agent made no MCP calls\n"
            f"--- turn 1 ---\n{first.stdout[-2000:]}\n"
            f"--- turn 2 ---\n{second.stdout[-2000:]}\n"
            f"--- stderr ---\n{second.stderr[-1000:]}"
        )
    return {
        "work": work,
        "uploads": uploads,
        "entries": entries,
        "at_gate": at_gate,
        "result": second,
    }


def calls(entries, tool):
    return [e for e in entries if e["kind"] == "tool_call" and e["tool"] == tool]


def declarations(entries):
    """What `dough evidence upload` declared, in order.

    Evidence no longer passes through an MCP call, so the declaration is logged
    by the CLI rather than observed on the wire. Same objects[], same meaning.
    """
    return [e for e in entries if e["kind"] == "declare"]


def test_nothing_is_uploaded_before_the_user_confirms(run_agent):
    """The gate is the whole consent story: this command ships a user's local
    files and their entire session to a server. It must not move until asked."""
    uploaded_at_gate = [e for e in run_agent["at_gate"] if e["kind"] == "upload"]
    proposed_at_gate = calls(run_agent["at_gate"], "proposals__propose")
    assert not uploaded_at_gate, "bytes left the machine before the user said yes"
    assert not proposed_at_gate, "proposed before the user said yes"


def test_evidence_is_declared_before_it_is_proposed(run_agent):
    began = declarations(run_agent["entries"])
    proposed = calls(run_agent["entries"], "proposals__propose")
    assert began, "never ran `dough evidence upload`"
    assert proposed, "never called propose"
    assert began[0]["_line"] < proposed[0]["_line"]


def test_the_removed_mcp_path_is_never_taken(run_agent):
    """The fallback is gone from the instructions; prove it is gone in practice.

    Without this, an agent that ignored the CLI and reached for the old tool
    would still satisfy every other assertion here — the evidence would arrive,
    just by the route the whole change exists to close. The tool is still served
    by the fake MCP precisely so that taking it would SUCCEED, which is what
    makes this assertion mean something.
    """
    assert not calls(run_agent["entries"], "proposals__evidence__begin"), (
        "took the removed proposals.evidence.begin path instead of the CLI"
    )


def test_every_declared_object_carries_a_real_sha256(run_agent):
    objects = declarations(run_agent["entries"])[0]["objects"]
    assert objects, "declared nothing"
    for o in objects:
        assert re.fullmatch(r"[0-9a-f]{64}", o["sha256"]), o
        assert o["bytes"] > 0


def test_the_session_transcript_is_among_the_evidence(run_agent):
    objects = declarations(run_agent["entries"])[0]["objects"]
    assert [o for o in objects if o["role"] == "transcript"], objects


def test_the_uploaded_bytes_match_what_was_declared(run_agent):
    objects = declarations(run_agent["entries"])[0]["objects"]
    uploads = {e["key"]: e for e in run_agent["entries"] if e["kind"] == "upload"}
    assert uploads, "nothing was uploaded"
    for o in objects:
        assert o["key"] in uploads, f"{o['key']} was declared but never uploaded"
        assert uploads[o["key"]]["sha256"] == o["sha256"], f"{o['key']} arrived corrupted"
        assert uploads[o["key"]]["bytes"] == o["bytes"], f"{o['key']} arrived truncated"


def test_the_proposal_cites_the_evidence_it_uploaded(run_agent):
    proposed = calls(run_agent["entries"], "proposals__propose")[0]
    transcript = proposed["args"].get("transcript") or {}
    assert transcript.get("evidenceId"), "propose carried no evidenceId"
    assert transcript["evidenceId"].startswith("ev_")


def test_the_proposal_payload_stayed_small(run_agent):
    """The whole design exists so bytes do not ride in the tool call."""
    proposed = calls(run_agent["entries"], "proposals__propose")[0]
    transcript = proposed["args"].get("transcript") or {}
    size = len(json.dumps(transcript))
    uploaded_total = sum(
        e["bytes"] for e in run_agent["entries"] if e["kind"] == "upload"
    )
    print(f"\n  transcript arg: {size} B   evidence uploaded: {uploaded_total} B")
    assert size < MAX_TRANSCRIPT_ARG_BYTES, (
        f"transcript argument was {size} B — something got inlined"
    )
    assert uploaded_total > size, "more evidence rode in the payload than went to storage"


def test_the_journal_entry_balances(run_agent):
    payload = calls(run_agent["entries"], "proposals__propose")[0]["args"]["payload"]
    lines = payload.get("lines", [])
    assert len(lines) >= 2
    debits = sum(l["amount"] for l in lines if l["postingType"] == "Debit")
    credits = sum(l["amount"] for l in lines if l["postingType"] == "Credit")
    assert round(debits, 2) == round(credits, 2) == 8400.50


def test_a_rationale_is_sent(run_agent):
    """Inverted deliberately — this asserted the opposite until 0.17.0.

    The old rule said the attached evidence WAS the account of how the entry was
    reached, so a rationale alongside it only competed with it. That rested on
    the queue page rendering the two as one wall of text; it renders them
    distinctly, so the reason was never true. Evidence shows what the numbers
    came from, the rationale says why this write, and an approver handed neither
    has only the amounts — `privateNote` cannot absorb it, being posted to the
    books.

    The length floor is what makes this more than a presence check: "September
    accrual." satisfies a non-empty assertion and tells an approver nothing.
    """
    args = calls(run_agent["entries"], "proposals__propose")[0]["args"]
    rationale = args.get("rationale") or ""
    print(f"\n  rationale ({len(rationale)} chars): {rationale!r}")
    assert rationale, "sent no rationale — the approver has only the payload"
    assert len(rationale) >= 60, (
        f"rationale is {len(rationale)} chars — too short to say what this was "
        f"based on: {rationale!r}"
    )


def test_the_posted_memo_stays_short(run_agent):
    """privateNote lands in the customer's QuickBooks ledger permanently."""
    note = calls(run_agent["entries"], "proposals__propose")[0]["args"]["payload"].get(
        "privateNote", ""
    )
    print(f"\n  privateNote ({len(note)} chars): {note!r}")
    assert len(note) <= 240, f"memo is {len(note)} chars — a paragraph reached the ledger"
