"""The assembled app: what it serves, what it refuses, and the headers on every response."""

import pytest
from conftest import PASSWORD, add_account, make_settings, sign_in
from quart.testing.app import LifespanError

from cra.app.web.factory import create_app


async def test_public_routes_answer_without_a_session(client):
    health = await (await client.get("/api/health")).get_json()
    assert health["ok"] is True
    assert health["library"]["papers"] == 19
    config = await (await client.get("/api/config")).get_json()
    assert config["auth"] == {
        "institution": False,
        "login_url": "auth/login",
        "logout_url": "auth/logout",
        "contact": "",
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
        await add_account(app, "alice")
        response = await client.post(
            f"{prefix}/auth/password",
            json={"username": "alice", "password": PASSWORD},
        )
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
async def test_requests_a_foreign_page_could_make_are_refused(
    app, client, headers, data
):
    await sign_in(app, client)
    response = await client.post("/api/chat/reset", headers=headers, data=data)
    assert response.status_code == 403
    same_site = await client.post(
        "/api/chat/reset",
        headers={"Origin": "http://localhost", "Content-Type": "application/json"},
        data="{}",
    )
    assert same_site.status_code == 200


async def test_a_large_body_is_refused_before_it_is_read(app, client):
    await sign_in(app, client)
    response = await client.post(
        "/api/chat",
        headers={"Content-Type": "application/json"},
        data=b'{"prompt": "' + b"x" * (1024 * 1024 + 1) + b'"}',
    )
    assert response.status_code == 413
