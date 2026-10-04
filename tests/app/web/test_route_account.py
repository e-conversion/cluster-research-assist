"""One's own account: the export, the profile and deleting it."""

import pytest
from conftest import PASSWORD, add_account, make_settings, session_user, sign_in

from cra.app.auth import local
from cra.app.web.factory import create_app
from cra.app.web.route_account import PROFILE_CHANGES_PER_ACCOUNT

CONFIGURED_ADMIN = "root@tum.de"


@pytest.fixture
async def app(tmp_path):
    app = create_app(make_settings(tmp_path, auth_admins=[CONFIGURED_ADMIN]))
    async with app.test_app():
        yield app


@pytest.fixture
def repo(app):
    return app.extensions["cra"].repo


async def signed_in(app, name: str, role: str = "user"):
    return await sign_in(app, app.test_client(), name, role)


async def user_id(repo, name: str) -> str:
    return next(u.id for u in await repo.list_users() if u.display_name == name)


async def test_the_export_holds_ones_own_conversations_only(app, repo):
    ada = await signed_in(app, "ada")
    await signed_in(app, "grace")
    mine = await repo.create_conversation(await user_id(repo, "ada"), "mine")
    await repo.add_message(mine.id, "user", "what is a catalyst?")
    await repo.create_conversation(await user_id(repo, "grace"), "theirs")

    response = await ada.get("/api/me/export")
    assert response.headers["Content-Disposition"].startswith("attachment")
    body = await response.get_json()
    assert [c["title"] for c in body["conversations"]] == ["mine"]
    assert body["conversations"][0]["messages"][0]["content"] == "what is a catalyst?"


async def test_deleting_ends_the_account_and_the_session(app, repo):
    ada = await signed_in(app, "ada")
    ada_id = await user_id(repo, "ada")
    assert (await ada.delete("/api/me")).status_code == 204
    assert await repo.get_user(ada_id) is None
    assert await session_user(ada) is None


async def test_deleting_takes_the_invitation_with_it(app, repo):
    ada = await signed_in(app, "ada")
    await repo.add_registered_email("ada@tum.de", "cli")
    await repo.link_registered_email("ada@tum.de", await user_id(repo, "ada"))
    await ada.delete("/api/me")
    assert await repo.get_registered_email("ada@tum.de") is None


async def test_a_configured_admin_cannot_delete_themselves(app, repo):
    root = await signed_in(app, "rooty", "admin")
    await repo.add_registered_email(CONFIGURED_ADMIN, "cli")
    await repo.link_registered_email(CONFIGURED_ADMIN, await user_id(repo, "rooty"))
    await signed_in(app, "ada")
    await repo.set_user_role(await user_id(repo, "ada"), "admin")
    assert (
        "CRA_AUTH_ADMINS"
        in (await (await root.get("/api/me")).get_json())["delete_blocked"]
    )
    assert (await root.delete("/api/me")).status_code == 403
    assert await repo.get_user(await user_id(repo, "rooty")) is not None


async def test_the_last_admin_cannot_delete_themselves(app, repo):
    ada = await signed_in(app, "ada")
    await repo.set_user_role(await user_id(repo, "ada"), "admin")
    assert (await ada.delete("/api/me")).status_code == 409


async def test_an_admin_who_is_not_the_last_can_delete_themselves(app, repo):
    await signed_in(app, "rooty", "admin")
    ada = await signed_in(app, "ada")
    await repo.set_user_role(await user_id(repo, "ada"), "admin")
    assert (await ada.delete("/api/me")).status_code == 204


async def profile(client) -> dict:
    return await (await client.get("/api/me")).get_json()


@pytest.mark.parametrize(
    ("field", "typed", "stored"),
    [
        ("name", "  Ada Lovelace ", "Ada Lovelace"),
        ("username", "Ada.L", "ada.l"),
        ("email", "Ada@TUM.de", "ada@tum.de"),
    ],
)
async def test_a_profile_field_can_be_changed(app, field, typed, stored):
    ada = await signed_in(app, "ada")
    response = await ada.patch("/api/me", json={field: typed})
    assert response.status_code == 200
    assert (await profile(ada))[field] == stored


async def test_a_new_username_replaces_the_old_one_at_sign_in(app):
    ada = await signed_in(app, "ada")
    await ada.patch("/api/me", json={"username": "lovelace"})
    statuses = {}
    for username in ("ada", "lovelace"):
        response = await app.test_client().post(
            "/auth/password", json={"username": username, "password": PASSWORD}
        )
        statuses[username] = response.status_code
    assert statuses == {"ada": 401, "lovelace": 200}


async def test_a_new_email_address_signs_in(app):
    ada = await signed_in(app, "ada")
    await ada.patch("/api/me", json={"email": "ada@tum.de"})
    response = await app.test_client().post(
        "/auth/password", json={"username": "ada@tum.de", "password": PASSWORD}
    )
    assert response.status_code == 200


async def test_the_contact_address_can_be_removed(app):
    await add_account(app, "ada", email="ada@tum.de")
    ada = await signed_in(app, "ada")
    await ada.patch("/api/me", json={"email": ""})
    assert (await profile(ada))["email"] == ""


async def test_the_session_takes_the_new_name(app):
    ada = await signed_in(app, "ada")
    await ada.patch("/api/me", json={"name": "Ada Lovelace"})
    assert await session_user(ada) == "Ada Lovelace"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"name": "  "}, "needs a name"),
        ({"name": "x" * 201}, "at most 200"),
        ({"username": "a"}, "2 to 64"),
        ({"username": "admin"}, "too easy to guess"),
        ({"username": "grace"}, "is taken"),
        ({"username": "grace@tum.de"}, "is taken"),
        ({"email": "not an address"}, "not an email address"),
        ({"email": "grace@tum.de"}, "another account"),
        ({"email": "invited@lmu.de"}, "another account"),
        ({"email": "hopper@mit.edu"}, "another account"),
    ],
    ids=[
        "empty name",
        "long name",
        "short username",
        "reserved username",
        "taken username",
        "username is another's address",
        "malformed address",
        "another's contact address",
        "another's invitation",
        "another's username",
    ],
)
async def test_a_bad_profile_change_is_refused(app, repo, change, message):
    grace = await add_account(app, "grace", email="grace@tum.de")
    await repo.add_registered_email("invited@lmu.de", "cli")
    await repo.link_registered_email("invited@lmu.de", grace)
    await add_account(app, "hopper@mit.edu")
    ada = await signed_in(app, "ada")
    response = await ada.patch("/api/me", json=change)
    assert response.status_code == 400
    assert message in (await response.get_json())["error"]


async def test_one_refused_field_leaves_the_others_unchanged(app):
    ada = await signed_in(app, "ada")
    await ada.patch("/api/me", json={"name": "Ada Lovelace", "username": "admin"})
    assert (await profile(ada))["name"] == "ada"


async def test_an_institutional_account_has_no_username_to_change(repo):
    user = await repo.create_user("Ada Lovelace")
    with pytest.raises(local.CredentialError, match="institution"):
        await local.update_profile(repo, user.id, username="ada")


async def test_profile_changes_are_rate_limited(app):
    ada = await signed_in(app, "ada")
    limit, _ = PROFILE_CHANGES_PER_ACCOUNT
    statuses = [
        (await ada.patch("/api/me", json={"username": "admin"})).status_code
        for _ in range(limit + 1)
    ]
    assert statuses == [400] * limit + [429]
