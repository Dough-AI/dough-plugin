"""A stand-in for the `dough` binary, for the propose end-to-end test.

The real CLI is built from Dough-Alpha and needs a login, so it cannot run here.
What this test actually owns is the *plugin* side of the seam: that the command
reaches for `dough evidence upload` at all, that it does so only after the user
consents, and that what it declares is what arrives. Byte-level correctness of
the upload itself belongs to the CLI's own unit tests (`src/cli/evidence.test.ts`
in Dough-Alpha) and is not re-litigated here.

So this implements the CLI at its INTERFACE — same subcommands, same stdout
shape, same "unknown command" exit — and reuses `collect_evidence.py`'s own
transcript-finding and hashing so the declaration is computed the way the real
one computes it rather than by a second implementation that could agree with a
bug.

Driven by two environment variables, matching the fake MCP server:
  DOUGH_FAKE_LOG   append-only JSONL the test reads back
  DOUGH_FAKE_SINK  base URL that accepts the PUTs
"""

import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "propose" / "scripts"))

from collect_evidence import _put, declare_objects, find_transcript  # noqa: E402

USAGE = """dough evidence — attach a session and its files to a proposal

  dough evidence upload --session <id> [--file <path>]...

Sends the SESSION TRANSCRIPT as well as each --file.
"""


def log(event):
    path = os.environ.get("DOUGH_FAKE_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def evidence_upload(argv):
    session_id, files = None, []
    # `find_transcript` does `Path(home)`, so this must be a real path, never
    # None. Same default as collect_evidence.py's `--home`.
    home, cwd = os.path.expanduser("~"), os.getcwd()
    index = 0
    while index < len(argv):
        if argv[index] == "--session" and index + 1 < len(argv):
            session_id = argv[index + 1]
            index += 2
        elif argv[index] == "--file" and index + 1 < len(argv):
            files.append(argv[index + 1])
            index += 2
        elif argv[index] == "--home" and index + 1 < len(argv):
            home = argv[index + 1]
            index += 2
        elif argv[index] == "--cwd" and index + 1 < len(argv):
            cwd = argv[index + 1]
            index += 2
        else:
            index += 1

    missing = [f for f in files if not Path(f).is_file()]
    if missing:
        sys.stderr.write(f"Not a file: {', '.join(missing)}\n")
        return 2

    transcript = find_transcript(cwd, session_id, home)
    objects, paths = declare_objects(transcript, files)

    # Declared BEFORE any bytes move — the invariant the real CLI holds, and the
    # reason an object that fails to upload is still recorded as `missing`.
    evidence_id = f"ev_{uuid.uuid4().hex[:16]}"
    log({"kind": "declare", "evidenceId": evidence_id, "objects": objects})

    # The notice the real command prints, on stderr so stdout stays parseable.
    sys.stderr.write(
        f"Attaching {len(objects)} object{'s' if len(objects) != 1 else ''}: "
        + ", ".join(f"{o['filename']} ({o['bytes']} B)" for o in objects)
        + "\n"
    )

    sink = os.environ["DOUGH_FAKE_SINK"].rstrip("/")
    uploaded, failed = [], []
    for obj in objects:
        try:
            _put(f"{sink}/{evidence_id}/{obj['key']}", {}, paths[obj["key"]])
            uploaded.append({"key": obj["key"]})
        except Exception as error:  # noqa: BLE001 - reported, not raised
            failed.append({"key": obj["key"], "error": str(error)})

    json.dump(
        {
            "evidenceId": evidence_id,
            "expiresAt": "2099-01-01T00:00:00.000Z",
            "uploaded": uploaded,
            "failed": failed,
            "rejected": [],
        },
        sys.stdout,
    )
    print()
    return 1 if failed else 0


def main(argv):
    if not argv:
        sys.stderr.write(USAGE)
        return 2

    # `dough plugin refresh` is step 0 and is explicitly best-effort.
    if argv[0] == "plugin":
        print("dough plugin: already up to date")
        return 0

    if argv[0] == "evidence":
        rest = argv[1:]
        if not rest or rest[0] in ("--help", "-h", "help"):
            print(USAGE)
            return 0
        if rest[0] == "upload":
            return evidence_upload(rest[1:])
        sys.stderr.write(f"unknown command 'evidence {rest[0]}'\n")
        return 2

    if argv[0] in ("--version", "-v"):
        print("dough 0.1.46 (fake)")
        return 0

    sys.stderr.write(f"unknown command '{argv[0]}'\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
