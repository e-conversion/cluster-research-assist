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
    "/api/conversations",
    "/api/conversations/<conversation_id>",
    "/api/conversations/<conversation_id>/open",
    "/api/publication-map",
    "/api/collaboration-graph",
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
    assert body["tools"]["local"] == 16
    assert body["tools"]["total"] == 16
    health = await (await client.get("/api/health")).get_json()
    assert health["tools"] == 16


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
