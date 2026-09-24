"""The admin console API."""

import pytest
from conftest import make_settings

from cra.app.policy import KEYS, Policy, PolicyError
from cra.app.web.factory import create_app


@pytest.fixture
async def admin_app(tmp_path):
    app = create_app(
        make_settings(
            tmp_path, auth_dev_user="root", auth_admins=["root", "future@tum.de"]
        )
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


async def test_everyone_who_can_sign_in_is_in_one_list(admin, admin_app):
    repo = admin_app.extensions["cra"].repo
    await admin.post("/api/admin/emails", json={"email": "ada@tum.de", "role": "admin"})
    await repo.add_registered_email("bound@tum.de", "cli")
    user = await repo.create_user("bound")
    await repo.link_registered_email("bound@tum.de", user.id)

    people = (await json_of(await admin.get("/api/admin/people")))["people"]
    by_kind = {}
    for person in people:
        by_kind.setdefault(person["kind"], []).append(person)

    assert [(p["name"], p["role"], p["self"]) for p in by_kind["account"]] == [
        ("root", "admin", True),
        ("bound", "user", False),
    ]
    assert by_kind["account"][1]["email"] == "bound@tum.de"
    assert [(p["email"], p["role"]) for p in by_kind["invitation"]] == [
        ("ada@tum.de", "admin")
    ]
    # a configured admin who has not signed in yet is visible; root already has
    # an account, so it appears once, as that account
    assert [p["email"] for p in by_kind["configured"]] == ["future@tum.de"]


async def test_an_invitation_decides_the_role_of_the_account_it_creates(
    admin, admin_app
):
    from cra.app.auth.binding import Claims, resolve_login

    repo = admin_app.extensions["cra"].repo
    await admin.post("/api/admin/emails", json={"email": "ada@tum.de", "role": "admin"})
    outcome = await resolve_login(
        repo,
        Claims(issuer="https://idp", sub="s1", email="ada@tum.de", given_name="Ada"),
    )
    assert (await repo.get_user(outcome.user_id)).role == "admin"


async def test_an_invitation_role_can_be_changed_before_it_is_used(admin):
    await admin.post("/api/admin/emails", json={"email": "ada@tum.de"})
    response = await admin.put("/api/admin/emails/ada@tum.de", json={"role": "admin"})
    assert await json_of(response) == {"email": "ada@tum.de", "role": "admin"}
    people = (await json_of(await admin.get("/api/admin/people")))["people"]
    assert [p["role"] for p in people if p["kind"] == "invitation"] == ["admin"]
    assert (
        await admin.put("/api/admin/emails/ada@tum.de", json={"role": "wizard"})
    ).status_code == 400
    assert (
        await admin.put("/api/admin/emails/nobody@tum.de", json={"role": "admin"})
    ).status_code == 404


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
    me = (await json_of(await admin.get("/api/admin/people")))["people"][0]["id"]
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


async def invitations(client):
    people = (await json_of(await client.get("/api/admin/people")))["people"]
    return [p["email"] for p in people if p["kind"] == "invitation"]


async def test_invitations_are_normalised_and_not_repeated(admin):
    assert (
        await admin.post("/api/admin/emails", json={"email": " Ada@Example.ORG "})
    ).status_code == 200
    assert await invitations(admin) == ["ada@example.org"]
    again = await admin.post("/api/admin/emails", json={"email": "ada@example.org"})
    assert again.status_code == 400
    assert (
        await admin.post("/api/admin/emails", json={"email": "nonsense"})
    ).status_code == 400
    assert (
        await admin.post(
            "/api/admin/emails", json={"email": "a@b.de", "role": "wizard"}
        )
    ).status_code == 400
    assert (await admin.delete("/api/admin/emails/ada@example.org")).status_code == 200
    assert await invitations(admin) == []


async def test_settings_show_their_value_and_where_it_comes_from(admin):
    body = await json_of(await admin.get("/api/admin/policy"))
    by_key = {s["key"]: s for s in body["settings"]}
    assert by_key.keys() == KEYS.keys()
    assert by_key["user_chat_daily_limit"]["source"] == "configuration"
    assert by_key["user_chat_daily_limit"]["value"] == 200


async def test_changing_a_setting_takes_effect_and_survives_a_restart(admin, admin_app):
    response = await admin.put(
        "/api/admin/policy/user_chat_daily_limit", json={"value": 5}
    )
    assert await json_of(response) == {
        "key": "user_chat_daily_limit",
        "value": 5,
        "source": "database",
    }
    ctx = admin_app.extensions["cra"]
    assert ctx.policy["user_chat_daily_limit"] == 5
    assert (await Policy.load(ctx.settings, ctx.repo))["user_chat_daily_limit"] == 5

    await admin.put("/api/admin/policy/user_chat_daily_limit", json={"reset": True})
    assert ctx.policy["user_chat_daily_limit"] == 200
    assert (await Policy.load(ctx.settings, ctx.repo))["user_chat_daily_limit"] == 200


async def test_a_setting_reaches_the_landing_page_without_signing_in(admin, admin_app):
    await admin.put("/api/admin/policy/notice", json={"value": "Maintenance on Friday"})
    anonymous = admin_app.test_client()
    config = await json_of(await anonymous.get("/api/config"))
    assert config["notice"] == "Maintenance on Friday"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("llm_max_tool_rounds", "0"),
        ("llm_max_tool_rounds", "not a number"),
        ("user_chat_daily_limit", "-1"),
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


async def test_library_page_reports_the_loaded_bundle(admin):
    body = await json_of(await admin.get("/api/admin/library"))
    assert body["counts"]["papers"] == 19
    assert body["manifest"]["schema_version"] == "1.0"


def test_policy_rejects_unknown_keys_outside_the_registry(tmp_path):
    policy = Policy(make_settings(tmp_path))
    with pytest.raises(PolicyError, match="unknown setting"):
        policy.set("nope", 1)


# --- replacing the library while the service runs ---------------------------


@pytest.fixture
async def updatable(tmp_path):
    """An admin session whose library is a versioned root."""
    from library_builder import write_library

    from cra.core.library.versions import init_root

    root = tmp_path / "library"
    init_root(root, write_library(tmp_path / "bundle"))
    app = create_app(
        make_settings(
            tmp_path, library_path=root, auth_dev_user="root", auth_admins=["root"]
        )
    )
    async with app.test_app():
        client = app.test_client()
        await client.get("/auth/login")
        yield app, client, root


def tarball(directory, path):
    import tarfile

    with tarfile.open(path, "w:gz") as tar:
        tar.add(directory, arcname=".")
    return path


async def upload(client, path):
    from quart.datastructures import FileStorage

    with path.open("rb") as handle:
        storage = FileStorage(handle, filename="bundle.tar.gz")
        # multipart is what a cross-site form sends too; the header says it is ours
        return await client.post(
            "/api/admin/library",
            files={"bundle": storage},
            headers={"X-Requested-With": "cra"},
        )


async def test_a_fixed_bundle_reports_that_it_cannot_be_updated(admin):
    body = await json_of(await admin.get("/api/admin/library"))
    assert body["updatable"] is False
    assert body["versions"] == []
    assert body["counts"]["papers"] == 19


async def test_a_root_lists_its_versions(updatable):
    _, client, _ = updatable
    body = await json_of(await client.get("/api/admin/library"))
    assert body["updatable"] is True
    assert [v["active"] for v in body["versions"]] == [True]


async def test_uploading_a_bundle_replaces_the_live_library(updatable, tmp_path):
    from library_builder import PAPERS, write_library

    app, client, _ = updatable
    smaller = write_library(tmp_path / "smaller")
    (smaller / "pis.json").write_text("[]")
    import json as json_module

    manifest = json_module.loads((smaller / "manifest.json").read_text())
    manifest["counts"]["pis"] = 0
    (smaller / "manifest.json").write_text(json_module.dumps(manifest))
    from cra.core.library import manifest as manifest_

    manifest_.write(smaller, manifest["counts"])

    response = await upload(client, tarball(smaller, tmp_path / "new.tar.gz"))
    body = await json_of(response)
    assert response.status_code == 200
    assert body["counts"]["pis"] == 0
    assert body["counts"]["papers"] == len(PAPERS)

    # the running application serves the new one without a restart
    assert app.extensions["cra"].library.counts["pis"] == 0
    health = await json_of(await client.get("/api/health"))
    assert health["library"]["pis"] == 0

    listing = await json_of(await client.get("/api/admin/library"))
    assert [v["name"] for v in listing["versions"] if v["active"]] == [body["version"]]
    assert len(listing["versions"]) == 2


async def test_a_broken_upload_leaves_the_running_library_alone(updatable, tmp_path):
    from library_builder import write_library

    app, client, _ = updatable
    before = app.extensions["cra"].library.counts
    # a bundle whose manifest no longer matches its contents
    broken = write_library(tmp_path / "broken")
    (broken / "pis.json").write_text('[{"smid": "9"}]')

    response = await upload(client, tarball(broken, tmp_path / "broken.tar.gz"))
    assert response.status_code == 400
    assert app.extensions["cra"].library.counts == before
    listing = await json_of(await client.get("/api/admin/library"))
    assert len(listing["versions"]) == 1, "a rejected upload leaves no version behind"


async def test_an_archive_without_a_bundle_is_refused(updatable, tmp_path):
    _, client, _ = updatable
    junk = tmp_path / "junk"
    junk.mkdir()
    (junk / "readme.txt").write_text("nothing here")
    response = await upload(client, tarball(junk, tmp_path / "junk.tar.gz"))
    assert response.status_code == 400
    assert "unpacked" in (await json_of(response))["error"]


async def test_rolling_back_to_an_earlier_version(updatable, tmp_path):
    _, client, _ = updatable
    first = (await json_of(await client.get("/api/admin/library")))["versions"][0][
        "name"
    ]
    from library_builder import write_library

    await upload(
        client, tarball(write_library(tmp_path / "second"), tmp_path / "s.tar.gz")
    )

    response = await client.post(f"/api/admin/library/{first}/activate")
    assert response.status_code == 200
    assert (await json_of(response))["version"] == first
    listing = await json_of(await client.get("/api/admin/library"))
    assert [v["name"] for v in listing["versions"] if v["active"]] == [first]


async def test_activating_an_unknown_version_is_a_404(updatable):
    _, client, _ = updatable
    assert (await client.post("/api/admin/library/nope/activate")).status_code == 404


async def test_uploading_to_a_fixed_bundle_is_refused(admin, tmp_path):
    from library_builder import write_library

    response = await upload(
        admin, tarball(write_library(tmp_path / "b"), tmp_path / "b.tar.gz")
    )
    assert response.status_code == 409
    assert "init-root" in (await json_of(response))["error"]


async def test_an_invitation_names_where_it_may_be_claimed_from(admin):
    response = await admin.post(
        "/api/admin/emails",
        json={"email": "ada@tum.de", "home_organization": " TUM.de "},
    )
    assert (await json_of(response))["home_organization"] == "TUM.de"
    people = (await json_of(await admin.get("/api/admin/people")))["people"]
    invitation = next(p for p in people if p["kind"] == "invitation")
    assert invitation["home_organization"] == "tum.de"


async def test_admin_actions_are_logged_with_the_actor(admin, admin_app, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="cra.app.web.auditlog")
    await admin.post("/api/admin/emails", json={"email": "ada@tum.de"})
    actions = [r.fields["action"] for r in caplog.records if r.getMessage() == "audit"]
    assert actions == ["invite"]
    actor = next(r.fields["by"] for r in caplog.records if r.getMessage() == "audit")
    assert actor == (await admin_app.extensions["cra"].repo.list_users())[0].id
