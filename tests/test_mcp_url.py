"""The published MCP server url must be a literal production url (USE-736).

0.30.2 shipped `${DOUGH_MCP_URL:-https://app.usedough.ai/api/mcp}` (USE-625).
Claude Code expands that template, but the org-installed plugin record the Claude
Desktop app keeps is stored verbatim: the app does not expand `${VAR}` in an
org-provided MCP server. Before OAuth it checks that the session's url equals
the record's, and a template never equals its expansion, so every org on 0.30.2+
got "This session's copy of the connector points at a different server URL".

A developer who needs another Dough rewrites the url in a local checkout
(UPDATING.md, "Point a checkout at another Dough"); the artifact customers
install never reads the environment.
"""

import json
import pathlib
from urllib.parse import urlsplit

MCP_JSON = pathlib.Path(__file__).resolve().parents[1] / ".mcp.json"
PRODUCTION = "https://app.usedough.ai/api/mcp"


def _server() -> dict:
    return json.loads(MCP_JSON.read_text(encoding="utf-8"))["mcpServers"]["dough"]


def test_url_has_no_environment_reference() -> None:
    url = _server()["url"]
    assert "$" not in url, f"the published url must be a literal, got {url}"


def test_url_is_production() -> None:
    assert _server()["url"] == PRODUCTION


def test_url_parses_as_an_https_url() -> None:
    parts = urlsplit(_server()["url"])
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "app.usedough.ai", "/api/mcp")


def test_no_server_field_reads_the_environment() -> None:
    """Headers or any later field are stored verbatim by the Desktop app too."""
    assert "$" not in MCP_JSON.read_text(encoding="utf-8")


def test_still_an_http_server() -> None:
    assert _server()["type"] == "http"
