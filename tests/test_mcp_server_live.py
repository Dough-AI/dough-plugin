"""Integration test: the url in `.mcp.json` is a live Dough MCP server that a
client can sign in to.

Talks to the real server over the network, with no credentials, and follows the
same discovery a client does before OAuth (MCP authorization spec):

1. an unauthenticated `initialize` is refused with 401 and a `WWW-Authenticate:
   Bearer` challenge naming the protected-resource metadata;
2. that metadata's `resource` is exactly the url in `.mcp.json` (clients bind
   the token to it; a mismatch is the USE-736 class of sign-in failure);
3. that server is named Dough, and the authorization server it names (on the
   same host) publishes authorize, token and dynamic
   client registration endpoints, with PKCE S256.

Read-only and unauthenticated, so it runs in CI on every PR. A blip is retried;
a network that stays down fails, which is the point: it must not skip itself
into a pass. Offline, deselect it with `-k 'not mcp_server_live'`.
"""

import json
import pathlib
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import pytest

MCP_JSON = pathlib.Path(__file__).resolve().parents[1] / ".mcp.json"
TIMEOUT_SECONDS = 30
USER_AGENT = "dough-plugin-tests"
ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _mcp_url() -> str:
    return json.loads(MCP_JSON.read_text(encoding="utf-8"))["mcpServers"]["dough"]["url"]


def _open(request: urllib.request.Request):
    """urlopen, retrying a blip (no connection, 429, 5xx) a bounded number of
    times. A server that stays down still fails the test."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS)
        except urllib.error.HTTPError as error:
            if error.code not in RETRYABLE_STATUS or attempt == ATTEMPTS:
                raise
        except urllib.error.URLError:
            if attempt == ATTEMPTS:
                raise
        time.sleep(RETRY_DELAY_SECONDS)
    raise AssertionError("unreachable")


def _get_json(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
    )
    with _open(request) as response:
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
        with _open(request) as response:
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


@pytest.fixture(scope="module")
def challenge() -> urllib.error.HTTPError:
    return _unauthenticated_initialize(_mcp_url())


@pytest.fixture(scope="module")
def resource(challenge) -> dict:
    return _get_json(_resource_metadata_url(challenge.headers.get("WWW-Authenticate", "")))


def test_server_asks_for_sign_in_and_names_its_metadata(challenge) -> None:
    url = _mcp_url()
    assert challenge.code == 401, f"POST {url} without a token -> {challenge.code}, expected 401"
    header = challenge.headers.get("WWW-Authenticate", "")
    assert header.startswith("Bearer"), f"expected a Bearer challenge, got {header!r}"
    assert urlsplit(_resource_metadata_url(header)).netloc == urlsplit(url).netloc


def test_protected_resource_is_dough_at_exactly_the_plugin_url(resource) -> None:
    url = _mcp_url()
    assert resource["resource"] == url, (
        f"the server calls itself {resource['resource']!r} but the plugin connects to "
        f"{url!r}; clients bind the token to the resource and refuse a mismatch"
    )
    assert resource.get("resource_name") == "Dough", f"not Dough's server: {resource}"


def test_authorization_server_supports_the_client_sign_in_flow(resource) -> None:
    issuer = resource["authorization_servers"][0]
    parts = urlsplit(issuer)
    assert parts.netloc == urlsplit(_mcp_url()).netloc, f"sign-in is delegated to {issuer}"
    # RFC 8414 puts the well-known segment before any issuer path; Dough's has none.
    assert parts.path in ("", "/"), f"issuer has a path, build its metadata url per RFC 8414: {issuer}"
    metadata = _get_json(f"{parts.scheme}://{parts.netloc}/.well-known/oauth-authorization-server")
    assert metadata["issuer"] == issuer
    for endpoint in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        assert urlsplit(metadata[endpoint]).scheme == "https", f"{endpoint}: {metadata.get(endpoint)}"
    assert "authorization_code" in metadata["grant_types_supported"]
    assert "S256" in metadata["code_challenge_methods_supported"]
