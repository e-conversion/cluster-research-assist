"""Keeping an account's source tokens across sessions, sealed."""

import pytest
from conftest import make_settings, sign_in
from cryptography.fernet import Fernet
from fakes import ELAB, make_host

from cra.app.web.factory import create_app

KEY = Fernet.generate_key().decode()


@pytest.fixture
async def kept_app(tmp_path, toy):
    """The toy source, with tokens kept for the account."""
    settings = make_settings(
        tmp_path,
        mcp_elab_url=ELAB.url,
        mcp_elab_register_url=ELAB.register_url,
        mcp_elab_base_url=ELAB.default_base_url,
        source_token_key=KEY,
    )
    app = create_app(settings)
    async with app.test_app():
        app.extensions["cra"].remote = make_host(toy)
        yield app
        await app.extensions["cra"].remote.aclose()


async def elab_status(client):
    return (await (await client.get("/api/session")).get_json())["connected"]["elab"]


async def test_a_connection_follows_the_account_to_a_new_session(kept_app):
    first = await sign_in(kept_app, kept_app.test_client())
    await first.post("/api/session/connect/elab", json={"token": "good"})
    await first.post("/auth/logout")
    elsewhere = await sign_in(kept_app, kept_app.test_client())
    assert await elab_status(elsewhere) == {"active": True, "tools": 2}


async def test_the_stored_token_is_not_readable_without_the_key(kept_app):
    client = await sign_in(kept_app, kept_app.test_client())
    await client.post("/api/session/connect/elab", json={"token": "good"})
    repo = kept_app.extensions["cra"].repo
    user_id = (await repo.get_credential_by_username("alice")).user_id
    (row,) = await repo.source_connections_of(user_id)
    assert "good" not in row.sealed
    assert Fernet(KEY).decrypt(row.sealed.encode()) == b"good"


async def test_disconnecting_forgets_the_stored_token(kept_app):
    client = await sign_in(kept_app, kept_app.test_client())
    await client.post("/api/session/connect/elab", json={"token": "good"})
    await client.delete("/api/session/connect/elab")
    elsewhere = await sign_in(kept_app, kept_app.test_client())
    assert (await elab_status(elsewhere))["active"] is False


async def test_a_token_sealed_under_another_key_is_dropped(kept_app):
    client = await sign_in(kept_app, kept_app.test_client())
    repo = kept_app.extensions["cra"].repo
    user_id = (await repo.get_credential_by_username("alice")).user_id
    other = Fernet(Fernet.generate_key()).encrypt(b"good").decode()
    await repo.save_source_connection(user_id, "elab", other)
    assert (await elab_status(client))["active"] is False
    assert await repo.source_connections_of(user_id) == []


@pytest.mark.parametrize("how", ["deactivate", "password reset"])
async def test_locking_an_account_forgets_its_connections(kept_app, how):
    client = await sign_in(kept_app, kept_app.test_client())
    await client.post("/api/session/connect/elab", json={"token": "good"})
    admin = await sign_in(kept_app, kept_app.test_client(), "ops", role="admin")
    repo = kept_app.extensions["cra"].repo
    user_id = (await repo.get_credential_by_username("alice")).user_id
    if how == "deactivate":
        await admin.put(f"/api/admin/users/{user_id}", json={"is_active": False})
    else:
        await admin.post(f"/api/admin/users/{user_id}/password-reset")
    assert await repo.source_connections_of(user_id) == []


async def test_without_a_key_nothing_is_stored(connected_app):
    client = await sign_in(connected_app, connected_app.test_client())
    await client.post("/api/session/connect/elab", json={"token": "good"})
    repo = connected_app.extensions["cra"].repo
    user_id = (await repo.get_credential_by_username("alice")).user_id
    assert await repo.source_connections_of(user_id) == []
