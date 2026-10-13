"""Integration test: the url in `.mcp.json` is a live Dough MCP server that a
client can sign in to.

Talks to the real server over the network, with no credentials, and follows the
same discovery a client does before OAuth (MCP authorization spec):

1. an unauthenticated `initialize` is refused with 401 and a `WWW-Authenticate:
   Bearer` challenge naming the protected-resource metadata;
2. that metadata's `resource` is exactly the url in `.mcp.json` (clients bind
   the token to it; a mismatch is the USE-736 class of sign-in failure);
3. the authorization server it names publishes authorize, token and dynamic
   client registration endpoints, with PKCE S256.

Read-only and unauthenticated, so it runs in CI on every PR. It fails when the
network is down, which is the point: it must not skip itself into a pass.
"""

import json
import pathlib
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

MCP_JSON = pathlib.Path(__file__).resolve().parents[1] / ".mcp.json"
TIMEOUT_SECONDS = 30
USER_AGENT = "dough-plugin-tests"


def _mcp_url() -> str:
    return json.loads(MCP_JSON.read_text(encoding="utf-8"))["mcpServers"]["dough"]["url"]


def _get_json(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        assert response.status == 200, f"GET {url} -> {response.status}"
        return json.loads(response.read())


def _unauthenticated_initialize(url: str) -> urllib.error.HTTPError:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": USER_AGENT, "version": "0"},
            },
        }
    ).encode()
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raise AssertionError(
                f"POST {url} without a token answered {response.status}; "
                "a Dough MCP server must ask for sign-in"
            )
    except urllib.error.HTTPError as error:
        return error


def _resource_metadata_url(challenge: str) -> str:
    match = re.search(r'resource_metadata="([^"]+)"', challenge)
    assert match, f"WWW-Authenticate names no resource_metadata: {challenge!r}"
    return match.group(1)


def test_server_asks_for_sign_in_and_names_its_metadata() -> None:
    url = _mcp_url()
    error = _unauthenticated_initialize(url)
    assert error.code == 401, f"POST {url} without a token -> {error.code}, expected 401"
    challenge = error.headers.get("WWW-Authenticate", "")
    assert challenge.startswith("Bearer"), f"expected a Bearer challenge, got {challenge!r}"
    metadata_url = _resource_metadata_url(challenge)
    assert urlsplit(metadata_url).netloc == urlsplit(url).netloc


def test_protected_resource_is_exactly_the_plugin_url() -> None:
    url = _mcp_url()
    challenge = _unauthenticated_initialize(url).headers.get("WWW-Authenticate", "")
    metadata = _get_json(_resource_metadata_url(challenge))
    assert metadata["resource"] == url, (
        f"the server calls itself {metadata['resource']!r} but the plugin connects to "
        f"{url!r}; clients bind the token to the resource and refuse a mismatch"
    )
    assert metadata["authorization_servers"], "no authorization server to sign in with"


def test_authorization_server_supports_the_client_sign_in_flow() -> None:
    url = _mcp_url()
    challenge = _unauthenticated_initialize(url).headers.get("WWW-Authenticate", "")
    issuer = _get_json(_resource_metadata_url(challenge))["authorization_servers"][0]
    metadata = _get_json(issuer.rstrip("/") + "/.well-known/oauth-authorization-server")
    assert metadata["issuer"] == issuer
    for endpoint in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        assert urlsplit(metadata[endpoint]).scheme == "https", f"{endpoint}: {metadata.get(endpoint)}"
    assert "authorization_code" in metadata["grant_types_supported"]
    assert "S256" in metadata["code_challenge_methods_supported"]
