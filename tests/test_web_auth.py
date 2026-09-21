import pytest
from conftest import make_settings
from quart.testing.app import LifespanError

from cra.app.web.factory import create_app
from cra.app.web.sessions import COOKIE_NAME


def cookie_value(response):
    header = response.headers["set-cookie"]
    return header.split(";")[0].split("=", 1)[1]


async def test_public_routes_answer_without_a_session(client):
    assert (await (await client.get("/api/health")).get_json())["ok"] is True
    config = await (await client.get("/api/config")).get_json()
    assert config["auth"] == {
        "provider": "dev",
        "login_url": "auth/login",
        "logout_url": "auth/logout",
    }
    assert (await client.get("/")).status_code == 200
    assert (await client.get("/static/js/app.js")).status_code == 200


async def test_every_other_route_requires_a_session(app, client):
    public = {
        "/",
        "/api/health",
        "/api/config",
        "/auth/login",
        "/auth/callback",
        "/auth/logout",
    }
    checked = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static" or rule.rule in public:
            continue
        method = "POST" if "POST" in rule.methods else "GET"
        response = await client.open(rule.rule, method=method)
        assert response.status_code == 401, rule.rule
        assert (await response.get_json())["login_url"] == "/auth/login"
        checked.append(rule.rule)
    assert "/api/session" in checked


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
    assert (await client.get("/api/session")).status_code == 401


async def test_logout_ends_the_session(client):
    await client.get("/auth/login")
    response = await client.post("/auth/logout")
    assert (await response.get_json()) == {"ok": True, "redirect": "/"}
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert (await client.get("/api/session")).status_code == 401


async def test_deactivated_user_is_locked_out(app, client):
    await client.get("/auth/login")
    repo = app.extensions["cra"].repo
    identity = await repo.get_identity("dev", "alice")
    await repo.set_user_active(identity.user_id, False)
    assert (await client.get("/api/session")).status_code == 401
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
        assert (await client.get("/api/health")).status_code == 404
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


async def test_serving_refuses_an_outdated_schema(tmp_path):
    app = create_app(make_settings(tmp_path, history_auto_migrate=False))
    with pytest.raises(LifespanError, match="cra db upgrade"):
        async with app.test_app():
            pass
