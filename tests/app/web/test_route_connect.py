"""Connecting a session to an external source."""

import pytest
from conftest import sign_in
from fakes import ELAB


@pytest.fixture
async def client(connected_app):
    client = connected_app.test_client()
    await sign_in(connected_app, client)
    return client


async def test_the_session_reports_what_is_connected(connected_app, client):
    config = await (await client.get("/api/config")).get_json()
    assert config["sources"]["elab"]["label"] == "eLabFTW"
    assert config["sources"]["elab"]["base_url"] == ELAB.default_base_url

    before = await (await client.get("/api/session")).get_json()
    assert before["connected"] == {"elab": {"active": False, "tools": 0}}

    connected = await client.post("/api/session/connect/elab", json={"token": "good"})
    assert (await connected.get_json())["tools"] == 2

    after = await (await client.get("/api/session")).get_json()
    assert after["connected"] == {"elab": {"active": True, "tools": 2}}
    assert after["tools"]["elab"] == 2
    assert after["tools"]["total"] == after["tools"]["local"] + 2

    await client.delete("/api/session/connect/elab")
    gone = await (await client.get("/api/session")).get_json()
    assert gone["connected"] == {"elab": {"active": False, "tools": 0}}


async def test_a_bad_token_is_answered_with_a_message(client):
    response = await client.post("/api/session/connect/elab", json={"token": "wrong"})
    assert response.status_code == 400
    assert "401" in (await response.get_json())["error"]


async def test_an_unknown_source_is_not_found(client):
    response = await client.post("/api/session/connect/nope", json={"token": "x"})
    assert response.status_code == 404


async def test_signing_out_takes_the_tokens(connected_app, client):
    await client.post("/api/session/connect/elab", json={"token": "good"})
    remote = connected_app.extensions["cra"].remote
    await client.post("/auth/logout")
    assert len(remote._pool) == 0
