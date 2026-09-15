"""The MCP endpoint must be overridable, and must default to production.

Why this exists (USE-625): the URL was a hard-coded literal, so the plugin could
only ever talk to production. That made `plugin` mode — the mode every customer
actually runs — impossible to exercise against a local Dough or a tunnel, and it
is why every end-to-end run of the hosted-agent work was done in `direct` mode
instead. The transport customers use was the one never tested.

Both halves matter and they pull in opposite directions, so both are pinned:

* the default must stay production, or every existing install breaks the moment
  this ships;
* the override must be a real expansion, not decoration.

Claude Code's `${VAR:-default}` expansion was verified against the installed CLI
on 2026-09-15 by pointing DOUGH_MCP_URL at a local listener and confirming the
request arrived there, and by confirming an unset variable reaches
app.usedough.ai.
"""

import json
import pathlib
import re

MCP_JSON = pathlib.Path(__file__).resolve().parents[1] / ".mcp.json"
PRODUCTION = "https://app.usedough.ai/api/mcp"


def _url() -> str:
    config = json.loads(MCP_JSON.read_text())
    return config["mcpServers"]["dough"]["url"]


def test_mcp_json_is_valid_json() -> None:
    json.loads(MCP_JSON.read_text())


def test_url_is_overridable_by_environment() -> None:
    url = _url()
    assert url.startswith("${"), f"the endpoint is hard-coded again: {url}"
    assert "DOUGH_MCP_URL" in url


def test_override_falls_back_to_production() -> None:
    """An install that sets nothing must reach production, exactly as before."""
    url = _url()
    match = re.fullmatch(r"\$\{(\w+):-(.+)\}", url)
    assert match, f"expected ${{VAR:-default}}, got {url}"
    variable, default = match.groups()
    assert variable == "DOUGH_MCP_URL"
    assert default == PRODUCTION


def test_still_an_http_server() -> None:
    config = json.loads(MCP_JSON.read_text())
    assert config["mcpServers"]["dough"]["type"] == "http"
