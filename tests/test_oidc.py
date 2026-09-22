from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from conftest import make_settings, session_user
from oidc_mock import CLIENT_ID, CLIENT_SECRET, ISSUER, MockIdp
from quart.testing.app import LifespanError

from cra.app.auth.principal import LoginDenied
from cra.app.web.factory import create_app
from cra.app.web.sessions import COOKIE_NAME


@pytest.fixture
def idp(respx_mock):
    idp = MockIdp()
    idp.install(respx_mock)
    return idp


@pytest.fixture
async def oidc_app(tmp_path, idp):
    settings = make_settings(
        tmp_path,
        auth_provider="oidc",
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret=CLIENT_SECRET,
        oidc_redirect_uri="https://cra.test/auth/callback",
        auth_admin_contact="admin@cra.test",
    )
    app = create_app(settings)
    async with app.test_app():
        yield app


@pytest.fixture
def repo(oidc_app):
    return oidc_app.extensions["cra"].repo


async def start_login(client, idp):
    """GET /auth/login and hand back the state the provider expects."""
    response = await client.get("/auth/login")
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    idp.nonce = query["nonce"][0]
    return query


async def test_login_redirects_with_pkce_and_stores_the_pending_state(oidc_app, idp):
    client = oidc_app.test_client()
    response = await client.get("/auth/login")
    url = urlparse(response.headers["location"])
    query = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{ISSUER}/authorize"
    assert query["client_id"] == [CLIENT_ID]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://cra.test/auth/callback"]
    assert COOKIE_NAME in response.headers["set-cookie"]
    assert await session_user(client) is None


async def test_full_login_binds_identity_and_signs_in(oidc_app, idp, repo):
    await repo.add_registered_email("ada@example.org", "cli")
    client = oidc_app.test_client()
    query = await start_login(client, idp)
    response = await client.get(f"/auth/callback?code=c&state={query['state'][0]}")
    assert response.status_code == 302
    assert response.headers["location"] == "/"
    session = await (await client.get("/api/session")).get_json()
    assert session["user"] == "Ada Lovelace"
    identity = await repo.get_identity(ISSUER, "pairwise-1")
    assert identity.home_organization == "tum.de"
    # the token exchange authenticated as the confidential client, with a verifier
    assert idp.token_requests[0]["authorization"].startswith("Basic ")


@pytest.mark.parametrize(
    ("claims", "text"),
    [
        ({"email": "nobody@example.org"}, "not yet registered for this service"),
        ({"email": None}, "did not transmit an email address"),
    ],
    ids=["unregistered", "no email"],
)
async def test_denied_logins_render_the_handover_messages(oidc_app, idp, claims, text):
    if claims["email"] is None:
        del idp.claims["email"]
    else:
        idp.claims.update(claims)
    client = oidc_app.test_client()
    query = await start_login(client, idp)
    response = await client.get(f"/auth/callback?code=c&state={query['state'][0]}")
    body = (await response.get_data()).decode()
    assert response.status_code == 403
    assert text in body
    assert "admin@cra.test" in body
    assert "pairwise-1" not in body


async def test_inactive_user_sees_the_disabled_page(oidc_app, idp, repo):
    await repo.add_registered_email("ada@example.org", "cli")
    client = oidc_app.test_client()
    query = await start_login(client, idp)
    await client.get(f"/auth/callback?code=c&state={query['state'][0]}")
    user_id = (await repo.get_identity(ISSUER, "pairwise-1")).user_id
    await repo.set_user_active(user_id, False)
    assert await session_user(client) is None
    query = await start_login(client, idp)
    response = await client.get(f"/auth/callback?code=c&state={query['state'][0]}")
    assert response.status_code == 403
    assert "disabled" in (await response.get_data()).decode()


@pytest.mark.parametrize(
    "tamper",
    ["state", "nonce", "expired", "audience", "provider_error", "no_session"],
)
async def test_protocol_errors_fail_closed(oidc_app, idp, repo, respx_mock, tamper):
    await repo.add_registered_email("ada@example.org", "cli")
    client = oidc_app.test_client()
    query = await start_login(client, idp)
    state = query["state"][0]
    overrides = {}
    if tamper == "state":
        state = "forged"
    elif tamper == "nonce":
        idp.nonce = "other"
    elif tamper == "expired":
        overrides = {"exp": 1}
    elif tamper == "audience":
        overrides = {"aud": "someone-else"}
    if overrides:
        idp.install(respx_mock, **overrides)
    url = f"/auth/callback?code=c&state={state}"
    if tamper == "provider_error":
        url = f"/auth/callback?error=access_denied&state={state}"
    if tamper == "no_session":
        client = oidc_app.test_client()
    response = await client.get(url)
    assert response.status_code == 400
    assert "Sign-in failed" in (await response.get_data()).decode()
    assert await repo.list_users() == []
    assert await session_user(client) is None


async def test_logout_redirects_to_the_end_session_endpoint(oidc_app, idp, repo):
    await repo.add_registered_email("ada@example.org", "cli")
    client = oidc_app.test_client()
    query = await start_login(client, idp)
    await client.get(f"/auth/callback?code=c&state={query['state'][0]}")
    response = await client.post("/auth/logout")
    body = await response.get_json()
    assert body["redirect"].startswith(f"{ISSUER}/logout?post_logout_redirect_uri=")
    assert await session_user(client) is None


async def test_provider_refuses_a_discovery_document_for_another_issuer(
    tmp_path, respx_mock, idp
):
    respx_mock.get(f"{ISSUER}/.well-known/openid-configuration").mock(
        return_value=httpx.Response(200, json={"issuer": "https://other"})
    )
    settings = make_settings(
        tmp_path,
        auth_provider="oidc",
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret=CLIENT_SECRET,
        oidc_redirect_uri="https://cra.test/auth/callback",
    )
    app = create_app(settings)
    with pytest.raises(LifespanError, match="discovery issuer"):
        async with app.test_app():
            pass


def test_login_denied_enum_covers_every_message():
    from cra.app.web.route_auth import MESSAGES

    assert set(MESSAGES) == set(LoginDenied)
