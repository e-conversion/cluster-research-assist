"""Browser sessions: the cookie, its rotation and its end."""

from conftest import PASSWORD, add_account, session_user, sign_in

from cra.app.web.sessions import COOKIE_NAME


def cookie_value(response):
    header = response.headers["set-cookie"]
    return header.split(";")[0].split("=", 1)[1]


async def password_login(client, username="alice", password=PASSWORD):
    return await client.post(
        "/auth/password", json={"username": username, "password": password}
    )


async def test_password_sign_in_sets_a_hardened_cookie_and_records_it(app, client):
    user_id = await add_account(app, "alice")
    response = await password_login(client)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Path=/" in cookie
    assert await session_user(client) == "alice"
    repo = app.extensions["cra"].repo
    assert (await repo.get_user(user_id)).last_login_at is not None


async def test_login_rotates_the_cookie(app, client):
    await add_account(app, "alice")
    first = cookie_value(await password_login(client))
    second = cookie_value(await password_login(client))
    assert first != second
    client.set_cookie("localhost", COOKIE_NAME, first)
    assert await session_user(client) is None


async def test_logout_ends_the_session(app, client):
    await sign_in(app, client)
    response = await client.post("/auth/logout")
    assert (await response.get_json()) == {"ok": True, "redirect": "/"}
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert await session_user(client) is None


async def test_deactivated_user_is_locked_out(app, client):
    await sign_in(app, client)
    repo = app.extensions["cra"].repo
    user_id = (await repo.get_credential_by_username("alice")).user_id
    await repo.set_user_active(user_id, False)
    assert await session_user(client) is None
    assert (await password_login(client)).status_code == 403


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
