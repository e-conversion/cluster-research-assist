import re

import pytest
from conftest import make_settings, session_user
from quart.testing.app import LifespanError

from cra.app.web.factory import create_app
from cra.app.web.sessions import COOKIE_NAME


def cookie_value(response):
    header = response.headers["set-cookie"]
    return header.split(";")[0].split("=", 1)[1]


async def test_public_routes_answer_without_a_session(client):
    health = await (await client.get("/api/health")).get_json()
    assert health["ok"] is True
    assert health["library"]["papers"] == 19
    config = await (await client.get("/api/config")).get_json()
    assert config["auth"] == {
        "provider": "dev",
        "login_url": "auth/login",
        "logout_url": "auth/logout",
    }
    assert (await client.get("/")).status_code == 200
    assert (await client.get("/static/js/app.js")).status_code == 200


async def test_static_files_must_be_revalidated(client):
    """Otherwise a browser keeps running the previous deployment's frontend."""
    for path in ("/static/js/admin.js", "/static/css/app.css"):
        response = await client.get(path)
        assert response.headers["Cache-Control"] == "no-cache", path
    page = await client.get("/admin", headers={"Accept": "application/json"})
    assert page.status_code == 401


# Every route, classified on purpose. A new route fails this test until it is
# listed, which is what keeps "public by default" from becoming an oversight.
PUBLIC_ROUTES = {
    "/",
    "/brand/<name>",
    "/api/health",
    "/api/config",
    "/auth/login",
    "/auth/callback",
    "/auth/logout",
}
USER_ROUTES = {
    "/api/session",
    "/api/feedback",
    "/api/session/model",
    "/api/session/params",
    "/api/chat",
    "/api/chat/stop",
    "/api/chat/reset",
    "/api/session/connect/<kind>",
    "/api/session/register/<kind>",
    "/api/conversations",
    "/api/conversations/<conversation_id>",
    "/api/conversations/<conversation_id>/open",
    "/api/publication-map",
    "/api/publication-map/lookup",
    "/api/collaboration-graph",
    "/api/tokens",
    "/api/tokens/<token_id>",
    "/api/me",
    "/api/me/export",
    "/api/stats",
}
ADMIN_ROUTES = {
    "/admin",
    "/api/admin/people",
    "/api/admin/users/<user_id>",
    "/api/admin/emails",
    "/api/admin/emails/<path:email>",
    "/api/admin/feedback",
    "/api/admin/feedback/<int:feedback_id>",
    "/api/admin/policy",
    "/api/admin/policy/<key>",
    "/api/admin/library",
    "/api/admin/library/<version>/activate",
    "/api/admin/tokens",
    "/api/admin/tokens/<token_id>",
}


def test_every_route_is_classified(app):
    routes = {r.rule for r in app.url_map.iter_rules() if r.endpoint != "static"}
    assert routes == PUBLIC_ROUTES | USER_ROUTES | ADMIN_ROUTES


async def test_anonymous_visitors_reach_every_public_route(client):
    for rule in sorted(PUBLIC_ROUTES):
        response = await client.open(
            rule, method="POST" if rule.endswith("logout") else "GET"
        )
        assert response.status_code != 401, rule


def as_request(app, template: str) -> tuple[str, str]:
    """A callable path and a supported method for a route template."""
    rule = next(r for r in app.url_map.iter_rules() if r.rule == template)
    method = next(m for m in ("GET", "POST", "PUT", "DELETE") if m in rule.methods)
    # a placeholder the route's converter accepts, or it 404s before the guard
    path = re.sub(r"<int:[^>]+>", "1", template)
    return re.sub(r"<[^>]+>", "placeholder", path), method


@pytest.mark.parametrize("template", sorted(ADMIN_ROUTES))
async def test_admin_routes_refuse_anonymous_and_ordinary_users(app, client, template):
    rule, method = as_request(app, template)
    anonymous = await client.open(rule, method=method)
    assert anonymous.status_code == 401
    assert (await anonymous.get_json())["login_url"] == "/auth/login"

    await client.get("/auth/login")
    signed_in = await client.open(rule, method=method)
    assert signed_in.status_code == 403
    assert (await signed_in.get_json())["error"] == "admin_required"


async def test_a_browser_asking_for_a_page_is_sent_to_sign_in(client):
    response = await client.get("/admin", headers={"Accept": "text/html"})
    assert response.status_code == 302
    assert response.headers["location"] == "/auth/login"


async def test_signing_in_raises_the_tier(client):
    await client.get("/auth/login")
    body = await (await client.get("/api/session")).get_json()
    assert (body["role"], body["tier"], body["signed_in"]) == ("user", "internal", True)
    assert body["is_admin"] is False


async def test_configured_admin_addresses_are_promoted_at_sign_in(tmp_path):
    app = create_app(make_settings(tmp_path, auth_dev_user="ada", auth_admins=["Ada"]))
    async with app.test_app():
        client = app.test_client()
        await client.get("/auth/login")
        body = await (await client.get("/api/session")).get_json()
        assert body["is_admin"] is True
        assert (await client.get("/admin")).status_code == 200


async def test_dev_login_creates_the_user_and_signs_in(app, client):
    response = await client.get("/auth/login")
    assert response.status_code == 302
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Path=/" in cookie
    session = await (await client.get("/api/session")).get_json()
    assert session["user"] == "alice"
    repo = app.extensions["cra"].repo
    identity = await repo.get_identity("dev", "alice")
    assert (await repo.get_user(identity.user_id)).last_login_at is not None


async def test_login_rotates_the_cookie(client):
    first = cookie_value(await client.get("/auth/login"))
    second = cookie_value(await client.get("/auth/login"))
    assert first != second
    client.set_cookie("localhost", COOKIE_NAME, first)
    assert await session_user(client) is None


async def test_logout_ends_the_session(client):
    await client.get("/auth/login")
    response = await client.post("/auth/logout")
    assert (await response.get_json()) == {"ok": True, "redirect": "/"}
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert await session_user(client) is None


async def test_deactivated_user_is_locked_out(app, client):
    await client.get("/auth/login")
    repo = app.extensions["cra"].repo
    identity = await repo.get_identity("dev", "alice")
    await repo.set_user_active(identity.user_id, False)
    assert await session_user(client) is None
    assert (await client.get("/auth/login")).status_code == 403


async def test_dev_provider_trusts_the_configured_proxy_header(tmp_path):
    app = create_app(
        make_settings(tmp_path, auth_dev_user="", auth_user_header="X-Forwarded-User")
    )
    async with app.test_app():
        client = app.test_client()
        assert (await client.get("/auth/login")).status_code == 400
        response = await client.get("/auth/login", headers={"X-Forwarded-User": "bob"})
        assert response.status_code == 302
        assert (await (await client.get("/api/session")).get_json())["user"] == "bob"


async def test_base_path_mounts_everything_under_the_prefix(tmp_path):
    prefix = "/nomad-oasis/api/everse"
    app = create_app(make_settings(tmp_path, base_path=prefix))
    async with app.test_app():
        client = app.test_client()
        # nothing answers outside the prefix
        assert (await client.get("/api/health")).status_code in (401, 404)
        assert (await client.get(f"{prefix}/api/health")).status_code == 200
        assert (await client.get(f"{prefix}/")).status_code == 200
        assert (await client.get(f"{prefix}/static/js/app.js")).status_code == 200
        response = await client.get(f"{prefix}/auth/login")
        assert response.headers["location"] == f"{prefix}/"
        assert f"Path={prefix}" in response.headers["set-cookie"]
        assert (await client.get(f"{prefix}/api/session")).status_code == 200
        assert (await (await client.get(f"{prefix}/api/session")).get_json())[
            "user"
        ] == "alice"


async def test_serving_refuses_a_broken_library_bundle(tmp_path):
    (tmp_path / "papers.csv").write_text("article_doi\n")
    app = create_app(make_settings(tmp_path, library_path=tmp_path))
    with pytest.raises(LifespanError, match="manifest.json missing"):
        async with app.test_app():
            pass


async def test_serving_refuses_an_outdated_schema(tmp_path):
    app = create_app(make_settings(tmp_path, history_auto_migrate=False))
    with pytest.raises(LifespanError, match="cra db upgrade"):
        async with app.test_app():
            pass


async def test_the_session_reports_the_tools_the_caller_may_use(client):
    await client.get("/auth/login")
    body = await (await client.get("/api/session")).get_json()
    # every tool but semantic search, which needs an encoder this test has not
    # configured; the library's vectors alone still answer "papers like this one"
    assert body["tools"]["local"] == 19
    assert body["tools"]["total"] == 19
    health = await (await client.get("/api/health")).get_json()
    assert health["tools"] == 19


async def test_the_session_hands_back_the_conversation_it_is_in(client, app):
    ctx = app.extensions["cra"]
    await client.get("/auth/login")
    body = await (await client.get("/api/session")).get_json()
    assert (body["messages"], body["turns"], body["conversation"]) == ([], 0, None)

    # what a finished turn leaves behind
    principal_id = (await ctx.repo.list_users())[0].id
    conversation = await ctx.repo.create_conversation(principal_id, "About perovskites")
    await ctx.repo.add_message(conversation.id, "user", "which papers?")
    await ctx.repo.add_message(
        conversation.id,
        "assistant",
        "These two.",
        {"model": "m", "tools": [{"name": "t"}]},
    )
    from cra.app.web.sessions import COOKIE_NAME

    cookie = next(c for c in client.cookie_jar if c.name == COOKIE_NAME)
    state = await ctx.sessions.load(cookie.value)
    state.data = {"conversation": conversation.id}
    await ctx.sessions.save(state)

    body = await (await client.get("/api/session")).get_json()
    assert body["turns"] == 1
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][1]["meta"]["model"] == "m"


async def two_people(tmp_path):
    """An app where the proxy header decides who is signing in."""
    return create_app(
        make_settings(tmp_path, auth_dev_user="", auth_user_header="X-Forwarded-User")
    )


async def test_signing_in_as_someone_else_does_not_continue_their_conversation(
    tmp_path,
):
    """Shared lab machine: A walks away signed in, B signs in on top."""
    app = await two_people(tmp_path)
    async with app.test_app():
        ctx = app.extensions["cra"]
        client = app.test_client()
        await client.get("/auth/login", headers={"X-Forwarded-User": "ada"})
        ada = (await ctx.repo.list_users())[0]
        conversation = await ctx.repo.create_conversation(ada.id, "Ada's")
        await ctx.repo.add_message(conversation.id, "user", "my secret question")
        await client.post(f"/api/conversations/{conversation.id}/open")
        assert (await (await client.get("/api/session")).get_json())["turns"] == 1

        await client.get("/auth/login", headers={"X-Forwarded-User": "bob"})
        session = await (await client.get("/api/session")).get_json()
        assert session["user"] == "bob"
        assert (session["conversation"], session["messages"]) == (None, [])


async def test_a_session_naming_someone_elses_conversation_shows_nothing(app, client):
    ctx = app.extensions["cra"]
    await client.get("/auth/login")
    bob = await ctx.repo.create_user("bob")
    theirs = await ctx.repo.create_conversation(bob.id, "Private")
    await ctx.repo.add_message(theirs.id, "user", "secret")
    cookie = next(c for c in client.cookie_jar if c.name == COOKIE_NAME)
    state = await ctx.sessions.load(cookie.value)
    state.data = {"conversation": theirs.id}
    await ctx.sessions.save(state)

    body = await (await client.get("/api/session")).get_json()
    assert (body["conversation"], body["messages"]) == (None, [])
    # and a question would start a fresh one rather than append to theirs
    from cra.app.web import route_chat

    principal = type("P", (), {"user_id": (await ctx.repo.list_users())[0].id})()
    started = await route_chat._conversation(ctx, state, principal)
    assert started != theirs.id


async def test_every_response_carries_the_security_headers(client):
    response = await client.get("/")
    csp = response.headers["Content-Security-Policy"]
    script_src = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    assert "'unsafe-inline'" not in script_src
    assert "https://cdn.jsdelivr.net" in script_src
    assert "img-src 'self' data:" in csp
    assert "frame-ancestors 'self'" in csp
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "Referrer-Policy" in response.headers
    # plain-http local run: no HSTS, or the browser would refuse http next time
    assert "Strict-Transport-Security" not in response.headers
    api = await client.get("/api/health")
    assert "Content-Security-Policy" in api.headers


async def test_hsts_is_sent_when_cookies_are_secure(tmp_path):
    app = create_app(make_settings(tmp_path, cookie_secure=True))
    async with app.test_app():
        response = await app.test_client().get("/api/health")
        assert response.headers["Strict-Transport-Security"].startswith("max-age=")


@pytest.mark.parametrize(
    ("headers", "data"),
    [
        ({"Origin": "https://evil.example", "Content-Type": "application/json"}, "{}"),
        ({"Sec-Fetch-Site": "cross-site", "Content-Type": "application/json"}, "{}"),
        ({"Content-Type": "application/x-www-form-urlencoded"}, "prompt=hi"),
        ({"Content-Type": "text/plain"}, "{}"),
    ],
    ids=["foreign origin", "browser says cross-site", "urlencoded form", "text form"],
)
async def test_requests_a_foreign_page_could_make_are_refused(client, headers, data):
    await client.get("/auth/login")
    response = await client.post("/api/chat/reset", headers=headers, data=data)
    assert response.status_code == 403
    same_site = await client.post(
        "/api/chat/reset",
        headers={"Origin": "http://localhost", "Content-Type": "application/json"},
        data="{}",
    )
    assert same_site.status_code == 200


async def test_a_large_body_is_refused_before_it_is_read(client):
    await client.get("/auth/login")
    response = await client.post(
        "/api/chat",
        headers={"Content-Type": "application/json"},
        data=b'{"prompt": "' + b"x" * (1024 * 1024 + 1) + b'"}',
    )
    assert response.status_code == 413


async def test_a_session_ends_after_its_absolute_lifetime_however_busy(repo):
    from datetime import timedelta

    from cra.app.history.repository import utcnow
    from cra.app.web.sessions import SessionStore

    store = SessionStore(repo, timedelta(hours=12), timedelta(hours=1))
    user = await repo.create_user("ada")
    cookie, state = await store.create(user.id)
    row = await repo.get_session(state.id)
    assert row.expires_at <= row.created_at + timedelta(hours=1)
    await repo.update_session(state.id, created_at=utcnow() - timedelta(hours=2))
    assert await store.load(cookie) is None
    assert await repo.get_session(state.id) is None
