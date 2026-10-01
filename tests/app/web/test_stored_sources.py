"""Keeping an account's source tokens across sessions, sealed, and the key a
deployment holds for the accounts it names."""

import json
from dataclasses import replace

import pytest
from conftest import make_settings, sign_in
from cryptography.fernet import Fernet
from fakes import ELAB, Toy, make_host

from cra.app.web.factory import create_app
from cra.assistant.mcpclient.host import RemoteHost
from cra.assistant.mcpclient.pool import RemotePool
from cra.core.connectors.sources import Source

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


SHARED = replace(ELAB, shared_token="good", shared_token_for=("alice",))


def make_shared_host(toy: Toy, source: Source = SHARED) -> RemoteHost:
    return RemoteHost({"elab": source}, RemotePool(600.0, toy.transport))


async def make_shared_app(tmp_path, toy, source: Source = SHARED):
    settings = make_settings(
        tmp_path,
        mcp_elab_url=ELAB.url,
        mcp_elab_register_url=ELAB.register_url,
        mcp_elab_base_url=ELAB.default_base_url,
    )
    app = create_app(settings)
    async with app.test_app():
        app.extensions["cra"].remote = make_shared_host(toy, source)
        yield app
        await app.extensions["cra"].remote.aclose()


@pytest.fixture
async def shared_app(tmp_path, toy):
    """An app whose one source carries a key this deployment holds for alice."""
    async for app in make_shared_app(tmp_path, toy):
        yield app


async def test_a_named_account_is_connected_without_a_registration(shared_app):
    """The named account pastes nothing: the source is there on the first request."""
    client = await sign_in(shared_app, shared_app.test_client())
    assert await elab_status(client) == {"active": True, "tools": 2}


async def test_an_account_the_source_does_not_name_stays_unconnected(shared_app):
    """Everybody else keeps registering their own token."""
    client = await sign_in(shared_app, shared_app.test_client(), "bob")
    assert await elab_status(client) == {"active": False, "tools": 0}


async def test_the_source_can_name_a_user_id(tmp_path, toy):
    async for app in make_shared_app(
        tmp_path, toy, replace(SHARED, shared_token_for=())
    ):
        client = await sign_in(app, app.test_client())
        repo = app.extensions["cra"].repo
        user_id = (await repo.get_credential_by_username("alice")).user_id
        app.extensions["cra"].remote.sources["elab"] = replace(
            SHARED, shared_token_for=(user_id,)
        )
        assert await elab_status(client) == {"active": True, "tools": 2}


async def test_a_shared_key_without_names_is_inert(tmp_path, toy):
    """A key nobody is named for must not be handed out by accident."""
    async for app in make_shared_app(
        tmp_path, toy, replace(SHARED, shared_token_for=())
    ):
        client = await sign_in(app, app.test_client())
        assert await elab_status(client) == {"active": False, "tools": 0}


async def test_a_shared_key_never_reaches_the_browser(shared_app):
    client = await sign_in(shared_app, shared_app.test_client())
    config = await (await client.get("/api/config")).get_json()
    assert config["sources"]["elab"]["label"] == "eLabFTW"
    assert "shared_token" not in config["sources"]["elab"]
    assert "good" not in json.dumps(config)
    session = await (await client.get("/api/session")).get_json()
    assert "good" not in json.dumps(session)


async def test_a_shared_key_unlocks_only_the_read_only_tools(shared_app):
    """The toy offers three tools; the one that writes is withheld."""
    client = await sign_in(shared_app, shared_app.test_client())
    session = await (await client.get("/api/session")).get_json()
    assert session["tools"]["elab"] == 2


async def test_an_account_that_registers_its_own_key_keeps_it(shared_app):
    """The shared key fills the gap; it never replaces what a person brought."""
    client = await sign_in(shared_app, shared_app.test_client())
    assert (await elab_status(client))["active"] is True
    await client.post("/api/session/connect/elab", json={"token": "good"})
    assert (await elab_status(client)) == {"active": True, "tools": 2}
    await client.delete("/api/session/connect/elab")
    assert (await elab_status(client))["active"] is False


async def test_a_source_without_a_shared_key_stays_opt_in(connected_app):
    client = await sign_in(connected_app, connected_app.test_client())
    session = await (await client.get("/api/session")).get_json()
    assert session["connected"]["elab"]["active"] is False
