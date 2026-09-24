"""One's own account: the export and deleting it."""

import pytest
from conftest import make_settings, session_user

from cra.app.web.factory import create_app

HEADER = "X-Forwarded-User"


@pytest.fixture
async def app(tmp_path):
    app = create_app(
        make_settings(
            tmp_path, auth_dev_user="", auth_user_header=HEADER, auth_admins=["root"]
        )
    )
    async with app.test_app():
        yield app


@pytest.fixture
def repo(app):
    return app.extensions["cra"].repo


async def signed_in(app, name: str):
    client = app.test_client()
    await client.get("/auth/login", headers={HEADER: name})
    return client


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
    root = await signed_in(app, "root")
    await signed_in(app, "ada")
    await repo.set_user_role(await user_id(repo, "ada"), "admin")
    assert (
        "CRA_AUTH_ADMINS"
        in (await (await root.get("/api/me")).get_json())["delete_blocked"]
    )
    assert (await root.delete("/api/me")).status_code == 403
    assert await repo.get_user(await user_id(repo, "root")) is not None


async def test_the_last_admin_cannot_delete_themselves(app, repo):
    ada = await signed_in(app, "ada")
    await repo.set_user_role(await user_id(repo, "ada"), "admin")
    assert (await ada.delete("/api/me")).status_code == 409


async def test_an_admin_who_is_not_the_last_can_delete_themselves(app, repo):
    await signed_in(app, "root")
    ada = await signed_in(app, "ada")
    await repo.set_user_role(await user_id(repo, "ada"), "admin")
    assert (await ada.delete("/api/me")).status_code == 204
