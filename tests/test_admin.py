"""The admin console API."""

import pytest
from conftest import make_settings

from cra.app.policy import KEYS, Policy, PolicyError
from cra.app.web.factory import create_app


@pytest.fixture
async def admin_app(tmp_path):
    app = create_app(
        make_settings(tmp_path, auth_dev_user="root", auth_admins=["root"])
    )
    async with app.test_app():
        yield app


@pytest.fixture
async def admin(admin_app):
    client = admin_app.test_client()
    await client.get("/auth/login")
    return client


async def json_of(response):
    return await response.get_json()


async def test_accounts_are_listed_with_their_role(admin, admin_app):
    body = await json_of(await admin.get("/api/admin/users"))
    assert [(u["display_name"], u["role"], u["self"]) for u in body["users"]] == [
        ("root", "admin", True)
    ]


async def test_an_admin_promotes_and_disables_another_account(admin, admin_app):
    repo = admin_app.extensions["cra"].repo
    other = await repo.create_user("ada")
    assert (
        await admin.put(f"/api/admin/users/{other.id}", json={"role": "admin"})
    ).status_code == 200
    assert (await repo.get_user(other.id)).role == "admin"
    assert (
        await admin.put(f"/api/admin/users/{other.id}", json={"is_active": False})
    ).status_code == 200
    assert (await repo.get_user(other.id)).is_active is False


@pytest.mark.parametrize(
    ("payload", "method"),
    [({"role": "user"}, "put"), ({"is_active": False}, "put"), (None, "delete")],
    ids=["demote", "disable", "delete"],
)
async def test_an_admin_cannot_lock_themselves_out(admin, admin_app, payload, method):
    me = (await json_of(await admin.get("/api/admin/users")))["users"][0]["id"]
    call = getattr(admin, method)
    response = await (
        call(f"/api/admin/users/{me}", json=payload)
        if payload
        else call(f"/api/admin/users/{me}")
    )
    assert response.status_code == 400
    assert "own" in (await json_of(response))["error"]


async def test_deleting_an_account_frees_its_address(admin, admin_app):
    repo = admin_app.extensions["cra"].repo
    await admin.post("/api/admin/emails", json={"email": "Ada@Example.org"})
    user = await repo.create_user("ada")
    await repo.link_registered_email("ada@example.org", user.id)
    assert (await admin.delete(f"/api/admin/users/{user.id}")).status_code == 200
    assert await repo.get_user(user.id) is None
    assert (await repo.get_registered_email("ada@example.org")).user_id is None


async def test_the_allow_list_normalises_and_rejects_duplicates(admin):
    assert (
        await admin.post("/api/admin/emails", json={"email": " Ada@Example.ORG "})
    ).status_code == 200
    body = await json_of(await admin.get("/api/admin/emails"))
    assert [e["email"] for e in body["emails"]] == ["ada@example.org"]
    again = await admin.post("/api/admin/emails", json={"email": "ada@example.org"})
    assert again.status_code == 400
    assert (
        await admin.post("/api/admin/emails", json={"email": "nonsense"})
    ).status_code == 400
    assert (await admin.delete("/api/admin/emails/ada@example.org")).status_code == 200
    assert (await json_of(await admin.get("/api/admin/emails")))["emails"] == []


async def test_settings_show_their_value_and_where_it_comes_from(admin):
    body = await json_of(await admin.get("/api/admin/policy"))
    by_key = {s["key"]: s for s in body["settings"]}
    assert by_key.keys() == KEYS.keys()
    assert by_key["anonymous_chat_daily_limit"]["source"] == "configuration"
    assert by_key["anonymous_chat_daily_limit"]["value"] == 20


async def test_changing_a_setting_takes_effect_and_survives_a_restart(
    admin, admin_app, tmp_path
):
    response = await admin.put(
        "/api/admin/policy/anonymous_chat_daily_limit", json={"value": 5}
    )
    assert (await json_of(response))["source"] == "database"
    assert admin_app.extensions["cra"].policy["anonymous_chat_daily_limit"] == 5

    ctx = admin_app.extensions["cra"]
    reloaded = await Policy.load(ctx.settings, ctx.repo)
    assert reloaded["anonymous_chat_daily_limit"] == 5

    await admin.put(
        "/api/admin/policy/anonymous_chat_daily_limit", json={"reset": True}
    )
    assert ctx.policy["anonymous_chat_daily_limit"] == 20
    assert (await Policy.load(ctx.settings, ctx.repo))[
        "anonymous_chat_daily_limit"
    ] == 20


async def test_a_setting_reaches_the_public_configuration(admin, admin_app):
    await admin.put("/api/admin/policy/notice", json={"value": "Maintenance on Friday"})
    await admin.put("/api/admin/policy/anonymous_chat_enabled", json={"value": "false"})
    anonymous = admin_app.test_client()
    config = await json_of(await anonymous.get("/api/config"))
    assert config["notice"] == "Maintenance on Friday"
    assert config["anonymous_chat"] is False
    assert (await json_of(await anonymous.get("/api/session")))["can_chat"] is False


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("anonymous_chat_daily_limit", "0"),
        ("anonymous_chat_daily_limit", "not a number"),
        ("anonymous_chat_enabled", "maybe"),
        ("tool_modules", "papers,papers"),
        ("notice", "x" * 501),
    ],
)
async def test_invalid_settings_are_refused(admin, key, value):
    response = await admin.put(f"/api/admin/policy/{key}", json={"value": value})
    assert response.status_code == 400
    assert await json_of(await admin.get("/api/admin/policy"))


async def test_unknown_setting_is_a_404(admin):
    assert (
        await admin.put("/api/admin/policy/nope", json={"value": 1})
    ).status_code == 404


async def test_corpus_page_reports_the_loaded_bundle(admin):
    body = await json_of(await admin.get("/api/admin/corpus"))
    assert body["counts"]["papers"] == 19
    assert body["manifest"]["schema_version"] == "1.0"


def test_policy_rejects_unknown_keys_outside_the_registry(tmp_path):
    policy = Policy(make_settings(tmp_path))
    with pytest.raises(PolicyError, match="unknown setting"):
        policy.set("nope", 1)
