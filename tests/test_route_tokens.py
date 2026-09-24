"""Minting, listing and revoking MCP tokens through the web API."""

import pytest
from conftest import make_settings

from cra.app.web.factory import create_app

HEADER = "X-Forwarded-User"


@pytest.fixture
async def app(tmp_path):
    app = create_app(
        make_settings(
            tmp_path,
            auth_dev_user="",
            auth_user_header=HEADER,
            auth_admins=["root"],
            mcp_server_enabled=True,
        )
    )
    async with app.test_app():
        yield app


async def signed_in(app, name: str):
    client = app.test_client()
    await client.get("/auth/login", headers={HEADER: name})
    return client


@pytest.fixture
async def ada(app):
    return await signed_in(app, "ada")


@pytest.fixture
async def grace(app):
    return await signed_in(app, "grace")


async def mint(client, label: str = "laptop", days: int = 90) -> dict:
    response = await client.post("/api/tokens", json={"label": label, "days": days})
    assert response.status_code == 201
    return await response.get_json()


async def listing(client, path: str = "/api/tokens") -> list[dict]:
    return (await (await client.get(path)).get_json())["tokens"]


async def test_the_value_is_in_the_mint_response_and_nowhere_else(ada):
    minted = await mint(ada)
    assert minted["token"].startswith("cra1_")
    listed = await listing(ada)
    assert [t["id"] for t in listed] == [minted["id"]]
    assert all("token" not in t for t in listed)


async def test_nobody_sees_another_persons_tokens(ada, grace):
    await mint(ada)
    assert await listing(grace) == []


async def test_revoking_someone_elses_token_answers_like_an_unknown_one(ada, grace):
    minted = await mint(ada)
    assert (await grace.delete(f"/api/tokens/{minted['id']}")).status_code == 404
    assert [t["state"] for t in await listing(ada)] == ["active"]


async def test_the_owner_can_revoke_their_token(ada):
    minted = await mint(ada)
    assert (await ada.delete(f"/api/tokens/{minted['id']}")).status_code == 200
    assert [t["state"] for t in await listing(ada)] == ["revoked"]


async def test_an_admin_can_revoke_anyones_token(app, ada):
    minted = await mint(ada)
    root = await signed_in(app, "root")
    everyone = await listing(root, "/api/admin/tokens")
    assert [(t["id"], t["owner"]) for t in everyone] == [(minted["id"], "ada")]
    revoked = await root.delete(f"/api/admin/tokens/{minted['id']}")
    assert revoked.status_code == 200
    assert [t["state"] for t in await listing(ada)] == ["revoked"]


@pytest.mark.parametrize(
    "body",
    [
        {"label": "", "days": 90},
        {"label": "x" * 101, "days": 90},
        {"label": "laptop", "days": 7},
        {"label": "laptop", "days": "90"},
        {"label": "laptop", "days": True},
    ],
    ids=["no label", "long label", "unoffered expiry", "days as text", "days as bool"],
)
async def test_a_request_that_makes_no_sense_mints_nothing(ada, body):
    assert (await ada.post("/api/tokens", json=body)).status_code == 400
    assert await listing(ada) == []


async def test_deactivating_an_account_revokes_its_tokens(app, ada):
    await mint(ada)
    ctx = app.extensions["cra"]
    ada_id = next(u.id for u in await ctx.repo.list_users() if u.display_name == "ada")
    root = await signed_in(app, "root")
    await root.put(f"/api/admin/users/{ada_id}", json={"is_active": False})
    assert [t["state"] for t in await listing(root, "/api/admin/tokens")] == ["revoked"]


async def test_there_are_no_tokens_without_an_endpoint(tmp_path):
    app = create_app(make_settings(tmp_path, mcp_server_enabled=False))
    async with app.test_app():
        client = app.test_client()
        await client.get("/auth/login")
        response = await client.post("/api/tokens", json={"label": "x", "days": 90})
    assert response.status_code == 404


async def test_the_configuration_says_where_the_endpoint_is(ada):
    config = await (await ada.get("/api/config")).get_json()
    assert config["mcp"]["path"] == "mcp"
    assert 90 in config["mcp"]["token_days"]["choices"]
