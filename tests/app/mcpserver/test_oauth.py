"""Signing in to the outward MCP endpoint.

The client is the MCP SDK's own OAuth client, as Claude Code and other SDK
based clients use it: it discovers the server from the 401, registers itself,
and sends "the browser" to the consent page. The browser is a second HTTP
client with a cookie jar that signs in with a password and answers.
"""

import contextlib
import json
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import httpx2
import pytest
from conftest import PASSWORD, add_account, lifespan, make_settings
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.provider import AuthorizationParams, AuthorizeError, TokenError
from mcp.shared.auth import (
    AuthorizationCodeResult,
    InvalidRedirectUriError,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from pydantic import AnyUrl

from cra.app.auth import tokens
from cra.app.mcpserver import oauth
from cra.app.mcpserver.dispatcher import wrap
from cra.app.web.factory import create_app

# http is accepted for a public URL on the loopback address only
BASE = "http://localhost"
REDIRECT = "http://localhost:33418/callback"


def make_endpoint(tmp_path, **overrides):
    values = {
        "mcp_server_enabled": True,
        "mcp_server_require_token": True,
        "public_url": BASE,
        **overrides,
    }
    return wrap(create_app(make_settings(tmp_path, **values)))


@pytest.fixture
async def endpoint(tmp_path):
    async with lifespan(make_endpoint(tmp_path)) as app:
        await add_account(app._app, "alice")
        yield app


def http(app, **kwargs) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url=BASE, **kwargs
    )


class Memory:
    """The SDK client's token storage."""

    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client: OAuthClientInformationFull | None = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, client_info):
        self.client = client_info


class Person:
    """The browser the client sends to the authorization endpoint."""

    def __init__(self, app, approve: bool = True) -> None:
        self.browser = http(app)
        self.approve = approve
        self.landed: str = ""

    async def visit(self, authorization_url: str) -> None:
        to_consent = await self.browser.get(authorization_url)
        assert to_consent.status_code == 302, to_consent.text
        consent = to_consent.headers["location"]
        # not signed in yet: off to sign in, and back
        to_sign_in = await self.browser.get(consent)
        assert to_sign_in.headers["location"] == "/"
        signed_in = await self.browser.post(
            "/auth/password", json={"username": "alice", "password": PASSWORD}
        )
        page = await self.browser.get(signed_in.json()["redirect"])
        assert page.status_code == 200
        found = re.search(r'data-request="([^"]+)"', page.text)
        assert found is not None, page.text
        handle = found.group(1)
        answer = await self.browser.post(
            "/oauth/consent/answer", json={"request": handle, "approve": self.approve}
        )
        self.landed = answer.json()["redirect"]

    async def callback(self) -> AuthorizationCodeResult:
        query = parse_qs(urlsplit(self.landed).query)
        if "code" not in query:
            raise RuntimeError(query["error"][0])
        return AuthorizationCodeResult(code=query["code"][0], state=query["state"][0])


def provider(person: Person, storage: Memory) -> OAuthClientProvider:
    return OAuthClientProvider(
        server_url=f"{BASE}/mcp",
        client_metadata=OAuthClientMetadata(
            client_name="Test client",
            redirect_uris=[REDIRECT],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        ),
        storage=storage,
        redirect_handler=person.visit,
        callback_handler=person.callback,
    )


@contextlib.asynccontextmanager
async def signed_in_session(app, person: Person, storage: Memory):
    async with (
        http(app, auth=provider(person, storage)) as client,
        streamable_http_client(f"{BASE}/mcp", http_client=client) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        # a round trip lets the "initialized" notification finish; leaving
        # while it runs cancels its database read and poisons the pooled
        # SQLite connection for the test's own queries afterwards
        await session.send_ping()
        yield session


async def test_a_client_signs_in_and_calls_a_tool(endpoint):
    async with signed_in_session(endpoint, Person(endpoint), Memory()) as session:
        names = {tool.name for tool in (await session.list_tools()).tools}
    assert "search_papers" in names


async def test_the_grant_is_listed_among_the_persons_tokens(endpoint):
    async with signed_in_session(endpoint, Person(endpoint), Memory()):
        pass
    repo = endpoint._ctx.repo
    user = await repo.get_credential_by_username("alice")
    (row,) = await repo.list_tokens(user.user_id)
    assert (row.label, tokens.describe(row)["signed_in"]) == ("Test client", True)


async def test_a_refused_request_gives_the_client_nothing(endpoint):
    person = Person(endpoint, approve=False)
    # the SDK client gives up; how it wraps the error is its own business
    with pytest.raises(BaseException):  # noqa: B017, PT011
        async with signed_in_session(endpoint, person, Memory()):
            pass
    assert parse_qs(urlsplit(person.landed).query)["error"] == ["access_denied"]
    repo = endpoint._ctx.repo
    user = await repo.get_credential_by_username("alice")
    assert await repo.list_tokens(user.user_id) == []


async def test_a_revoked_grant_stops_working(endpoint):
    storage = Memory()
    async with signed_in_session(endpoint, Person(endpoint), storage):
        pass
    repo = endpoint._ctx.repo
    user = await repo.get_credential_by_username("alice")
    (row,) = await repo.list_tokens(user.user_id)
    await repo.revoke_token(row.id)
    async with http(endpoint) as client:
        response = await client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {storage.tokens.access_token}"},
            json={},
        )
    assert response.status_code == 401


async def test_a_refusal_points_to_the_sign_in(endpoint):
    async with http(endpoint) as client:
        refused = await client.post("/mcp", json={})
        resource = await client.get("/.well-known/oauth-protected-resource/mcp")
        server = await client.get("/.well-known/oauth-authorization-server")
    challenge = refused.headers["www-authenticate"]
    assert (
        'resource_metadata="http://localhost/.well-known/oauth-protected-resource/mcp"'
        in challenge
    )
    # compared character by character by clients: no trailing slash anywhere
    assert resource.json()["resource"] == "http://localhost/mcp"
    assert resource.json()["authorization_servers"] == ["http://localhost"]
    assert server.json()["issuer"] == "http://localhost"
    assert "none" in server.json()["token_endpoint_auth_methods_supported"]


@pytest.mark.parametrize(
    ("path", "key"),
    [
        ("/.well-known/oauth-protected-resource/mcp", "resource"),
        ("/.well-known/oauth-protected-resource", "resource"),
        ("/.well-known/oauth-authorization-server", "issuer"),
        ("/.well-known/oauth-authorization-server/mcp", "issuer"),
    ],
)
async def test_metadata_is_found_where_clients_look(endpoint, path, key):
    async with http(endpoint) as client:
        response = await client.get(path)
    assert response.status_code == 200
    assert key in response.json()


async def test_an_unknown_discovery_document_is_not_found(endpoint):
    async with http(endpoint) as client:
        response = await client.get("/.well-known/openid-configuration")
    assert response.status_code == 404


async def test_the_sign_in_is_absent_without_a_public_url(tmp_path):
    async with (
        lifespan(make_endpoint(tmp_path, public_url="")) as app,
        http(app) as client,
    ):
        refused = await client.post("/mcp", json={})
        metadata = await client.get("/.well-known/oauth-authorization-server")
        consent = await client.get("/oauth/consent?request=x")
    assert "resource_metadata" not in refused.headers["www-authenticate"]
    assert metadata.status_code == 404
    assert consent.status_code == 404


# ---- the grant's life, through the provider the SDK's handlers call


def provider_of(endpoint, http=None) -> oauth.Provider:
    ctx = endpoint._ctx
    return oauth.Provider(ctx.repo, ctx.settings, http or ctx.http)


async def approved_code(endpoint, client: OAuthClientInformationFull) -> str:
    """A code for alice, as the consent page would leave one."""
    repo = endpoint._ctx.repo
    provider = provider_of(endpoint)
    params = AuthorizationParams(
        state="s",
        scopes=None,
        code_challenge="c" * 43,
        redirect_uri=REDIRECT,
        redirect_uri_provided_explicitly=True,
    )
    consent = await provider.authorize(client, params)
    handle = parse_qs(urlsplit(consent).query)["request"][0]
    user = await repo.get_credential_by_username("alice")
    landed = await oauth.answer(repo, handle, user.user_id, approve=True)
    assert landed is not None
    return parse_qs(urlsplit(landed).query)["code"][0]


@pytest.fixture
async def registered(endpoint):
    client = OAuthClientInformationFull(
        client_id="test-client",
        redirect_uris=[REDIRECT],
        token_endpoint_auth_method="none",
    )
    await provider_of(endpoint).register_client(client)
    return client


async def test_a_code_is_redeemed_once(endpoint, registered):
    provider = provider_of(endpoint)
    code = await provider.load_authorization_code(
        registered, await approved_code(endpoint, registered)
    )
    await provider.exchange_authorization_code(registered, code)
    with pytest.raises(TokenError, match="invalid_grant"):
        await provider.exchange_authorization_code(registered, code)


async def test_a_code_belongs_to_the_client_it_was_issued_to(endpoint, registered):
    provider = provider_of(endpoint)
    other = registered.model_copy(update={"client_id": "someone-else"})
    code = await approved_code(endpoint, registered)
    assert await provider.load_authorization_code(other, code) is None


async def test_renewing_replaces_both_values(endpoint, registered):
    provider = provider_of(endpoint)
    code = await provider.load_authorization_code(
        registered, await approved_code(endpoint, registered)
    )
    first = await provider.exchange_authorization_code(registered, code)
    refresh = await provider.load_refresh_token(registered, first.refresh_token)
    second = await provider.exchange_refresh_token(registered, refresh, [])
    assert await provider.load_access_token(first.access_token) is None
    assert await provider.load_refresh_token(registered, first.refresh_token) is None
    assert await provider.load_access_token(second.access_token) is not None


async def test_a_refresh_token_is_spent_by_its_first_use(endpoint, registered):
    """Two renewals racing with one refresh token: only one wins."""
    provider = provider_of(endpoint)
    code = await provider.load_authorization_code(
        registered, await approved_code(endpoint, registered)
    )
    first = await provider.exchange_authorization_code(registered, code)
    refresh = await provider.load_refresh_token(registered, first.refresh_token)
    await provider.exchange_refresh_token(registered, refresh, [])
    with pytest.raises(TokenError, match="invalid_grant"):
        await provider.exchange_refresh_token(registered, refresh, [])


async def test_an_expired_access_value_is_refused(endpoint, registered, monkeypatch):
    monkeypatch.setattr(oauth, "ACCESS_LIFETIME", timedelta(seconds=-1))
    provider = provider_of(endpoint)
    code = await provider.load_authorization_code(
        registered, await approved_code(endpoint, registered)
    )
    issued = await provider.exchange_authorization_code(registered, code)
    assert await tokens.verify(endpoint._ctx.repo, issued.access_token) is None


async def test_a_request_for_another_resource_is_refused(endpoint, registered):
    provider = provider_of(endpoint)
    params = AuthorizationParams(
        state=None,
        scopes=None,
        code_challenge="c" * 43,
        redirect_uri=REDIRECT,
        redirect_uri_provided_explicitly=True,
        resource="https://elsewhere.example/mcp",
    )
    with pytest.raises(AuthorizeError, match="invalid_target"):
        await provider.authorize(registered, params)


@pytest.mark.parametrize(
    ("uri", "allowed"),
    [
        ("https://claude.ai/api/mcp/auth_callback", True),
        ("https://chatgpt.com/connector_platform_oauth_redirect", True),
        ("https://sub.claude.ai/cb", True),
        ("http://localhost:6274/callback", True),
        ("http://127.0.0.1/callback", True),
        ("http://[::1]:8000/cb", True),
        ("cursor://anysphere.cursor-retrieval/oauth/callback", True),
        ("https://evilclaude.ai/cb", False),
        ("https://attacker.example/cb", False),
        ("http://claude.ai/cb", False),
        ("http://192.168.1.5/cb", False),
        ("javascript://claude.ai/%0aalert(1)", False),
        ("https://claude.ai/cb#fragment", False),
    ],
)
def test_where_a_client_may_send_people_back(uri, allowed):
    assert oauth.redirect_allowed(uri, ["claude.ai", "chatgpt.com"]) is allowed


async def test_registering_an_unknown_return_address_is_refused(endpoint):
    async with http(endpoint) as client:
        response = await client.post(
            "/oauth/register",
            json={
                "redirect_uris": ["https://attacker.example/cb"],
                "token_endpoint_auth_method": "none",
            },
        )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"


async def test_only_the_consent_page_is_returned_to_after_sign_in(endpoint):
    """Whatever else a session holds under the key is not followed."""
    ctx = endpoint._ctx
    async with http(endpoint) as browser:
        cookie, state = await ctx.sessions.create()
        state.data["return_to"] = "https://attacker.example/"
        await ctx.sessions.save(state)
        browser.cookies.set("cra_session", cookie)
        signed_in = await browser.post(
            "/auth/password", json={"username": "alice", "password": PASSWORD}
        )
    assert signed_in.json()["redirect"] is None


# ---- clients that name themselves by a published document (CIMD)

DOCUMENT_URL = "https://claude.ai/oauth/mcp-oauth-client-metadata"
DOCUMENT = {
    "client_id": DOCUMENT_URL,
    "client_name": "Claude",
    "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "none",
}


def serving(document, status=200):
    """An HTTP client whose every request gets ``document``, and the list of
    requests it saw."""
    seen = []

    def answer(request):
        seen.append(str(request.url))
        body = (
            document if isinstance(document, bytes) else json.dumps(document).encode()
        )
        return httpx.Response(status, content=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(answer)), seen


async def test_a_published_client_is_read_from_its_document(endpoint):
    http, _ = serving(DOCUMENT)
    client = await provider_of(endpoint, http).get_client(DOCUMENT_URL)
    assert (client.client_name, client.token_endpoint_auth_method) == ("Claude", "none")


async def test_a_client_that_prefers_a_signed_assertion_but_can_do_without(endpoint):
    """ChatGPT on the web names private_key_jwt first and lists "none" too."""
    url = "https://chatgpt.com/oauth/abc/client.json"
    document = DOCUMENT | {
        "client_id": url,
        "redirect_uris": ["https://chatgpt.com/connector/oauth/abc"],
        "token_endpoint_auth_method": "private_key_jwt",
        "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
    }
    http, _ = serving(document)
    client = await provider_of(endpoint, http).get_client(url)
    assert client.token_endpoint_auth_method == "none"


async def test_a_published_client_is_read_once_a_day(endpoint):
    http, seen = serving(DOCUMENT)
    provider = provider_of(endpoint, http)
    await provider.get_client(DOCUMENT_URL)
    await provider.get_client(DOCUMENT_URL)
    assert seen == [DOCUMENT_URL]


@pytest.mark.parametrize(
    ("url", "document"),
    [
        (
            "https://attacker.example/client",
            DOCUMENT | {"client_id": "https://attacker.example/client"},
        ),
        (DOCUMENT_URL, DOCUMENT | {"client_id": "https://claude.ai/other"}),
        (DOCUMENT_URL, DOCUMENT | {"redirect_uris": ["https://attacker.example/cb"]}),
        (DOCUMENT_URL, DOCUMENT | {"token_endpoint_auth_method": "private_key_jwt"}),
        (DOCUMENT_URL, b"[" + b" " * oauth.DOCUMENT_MAX_BYTES + b"]"),
        (DOCUMENT_URL, b"not json"),
    ],
    ids=[
        "untrusted host",
        "names another id",
        "untrusted return address",
        "signed assertion",
        "too large",
        "not json",
    ],
)
async def test_a_published_client_that_does_not_hold_up_is_unknown(
    endpoint, url, document
):
    http, seen = serving(document)
    assert await provider_of(endpoint, http).get_client(url) is None
    if url != DOCUMENT_URL:
        assert seen == []  # never fetched at all


async def test_the_server_says_it_reads_client_documents(endpoint):
    async with http(endpoint) as client:
        server = await client.get("/.well-known/oauth-authorization-server")
    assert server.json()["client_id_metadata_document_supported"] is True


CODEX = {
    "client_id": "https://chatgpt.com/oauth/codex/abc/client.json",
    "client_name": "Codex",
    "redirect_uris": ["http://127.0.0.1/callback/abc", "http://localhost/callback/abc"],
    "token_endpoint_auth_method": "none",
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
}


@pytest.mark.parametrize(
    ("uri", "allowed"),
    [
        ("http://127.0.0.1:56947/callback/abc", True),
        ("http://localhost:8080/callback/abc", True),
        ("http://127.0.0.1/callback/abc", True),
        ("http://127.0.0.1:56947/callback/other", False),
        ("http://localhost:8080/callback/abc?x=1", False),
        ("https://127.0.0.1:56947/callback/abc", False),
    ],
)
async def test_a_native_app_returns_to_any_loopback_port(endpoint, uri, allowed):
    """RFC 8252 §7.3: Codex registers no port and listens on whichever it gets."""
    http, _ = serving(CODEX)
    client = await provider_of(endpoint, http).get_client(CODEX["client_id"])
    try:
        client.validate_redirect_uri(AnyUrl(uri))
    except InvalidRedirectUriError:
        assert not allowed
    else:
        assert allowed
