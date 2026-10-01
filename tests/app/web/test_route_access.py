"""Asking for an account after an institutional sign-in, and deciding."""

import pytest
from conftest import make_settings, session_user, sign_in
from oidc_mock import ISSUER, SETTINGS, MockIdp, institution_sign_in

from cra.app.web import route_auth
from cra.app.web.factory import create_app

PROFILE = "https://www.tum.de/people/ada-lovelace"
# a PI in the toy library
GROUP = "1001"


@pytest.fixture
def idp(respx_mock):
    idp = MockIdp()
    idp.claims["email"] = "ada@tum.de"
    idp.install(respx_mock)
    return idp


@pytest.fixture
async def app(tmp_path, idp):
    app = create_app(make_settings(tmp_path, **SETTINGS))
    async with app.test_app():
        yield app


@pytest.fixture
def repo(app):
    return app.extensions["cra"].repo


@pytest.fixture
async def asker(app, idp):
    """A browser that has signed in at its institution without an account."""
    client = app.test_client()
    await institution_sign_in(client, idp)
    return client


@pytest.fixture
async def admin(app):
    return await sign_in(app, app.test_client(), "ops", role="admin")


async def ask(client, **fields):
    body = {"group": GROUP, "profile_url": PROFILE, **fields}
    response = await client.post("/api/access-requests", json=body)
    return response.status_code, await response.get_json()


async def status_of(client):
    return (await (await client.get("/api/access-requests/me")).get_json())["request"]


async def the_request(admin):
    (row,) = (await (await admin.get("/api/admin/access-requests")).get_json())[
        "requests"
    ]
    return row


async def test_the_form_shows_the_verified_identity(asker):
    body = await (await asker.get("/api/access-requests/me")).get_json()
    assert body["identity"] == {
        "name": "Ada Lovelace",
        "email": "ada@tum.de",
        "institution": "Technische Universität München",
    }


async def test_the_form_offers_the_library_groups(asker):
    body = await (await asker.get("/api/access-requests/me")).get_json()
    assert any(group["smid"] == GROUP for group in body["groups"])


async def test_an_approved_request_lets_the_next_institutional_sign_in_through(
    app, asker, admin, repo, idp
):
    assert (await ask(asker, message="PhD student"))[0] == 201
    row = await the_request(admin)
    assert (row["name"], row["institution"], row["group"]) == (
        "Ada Lovelace",
        "Technische Universität München",
        "Prof. Dr. Ariadne Thalassor — Thalassor Group (TUM)",
    )
    assert row["profile_url"] == PROFILE

    response = await admin.post(f"/api/admin/access-requests/{row['id']}/approve")
    assert response.status_code == 200
    await institution_sign_in(asker, idp)
    assert await session_user(asker) == "Ada Lovelace"
    # the address is what re-binds the person when the pairwise subject changes
    invitation = await repo.get_registered_email("ada@tum.de")
    assert invitation.user_id == (await response.get_json())["user"]


async def test_asking_twice_shows_where_the_first_request_stands(asker):
    await ask(asker)
    assert (await ask(asker))[0] == 409
    assert (await status_of(asker))["status"] == "pending"


async def decline(admin, note="Not a cluster member."):
    row = await the_request(admin)
    await admin.post(
        f"/api/admin/access-requests/{row['id']}/reject", json={"note": note}
    )
    return row


async def test_a_declined_request_shows_the_note(asker, admin):
    await ask(asker)
    await decline(admin)
    status = await status_of(asker)
    assert (status["status"], status["note"]) == ("rejected", "Not a cluster member.")


async def test_a_declined_request_cannot_be_approved(asker, admin):
    await ask(asker)
    row = await decline(admin)
    approve = await admin.post(f"/api/admin/access-requests/{row['id']}/approve")
    assert approve.status_code == 404


async def test_deleting_a_request_lets_the_person_ask_again(asker, admin):
    await ask(asker)
    row = await the_request(admin)
    assert (
        await admin.delete(f"/api/admin/access-requests/{row['id']}")
    ).status_code == 200
    assert await status_of(asker) is None
    assert (await ask(asker))[0] == 201


async def test_after_the_approved_account_is_deleted_the_person_may_ask_again(
    asker, admin, idp, repo
):
    await ask(asker)
    row = await the_request(admin)
    approved = await admin.post(f"/api/admin/access-requests/{row['id']}/approve")
    await repo.delete_user((await approved.get_json())["user"], keep_invitation=False)
    await institution_sign_in(asker, idp)
    assert await status_of(asker) is None


async def test_an_approval_without_a_home_organisation_adds_no_invitation(
    app, admin, repo, respx_mock
):
    """Such an entry could be claimed from any identity provider that asserts
    the address."""
    idp = MockIdp()
    idp.claims["email"] = "ada@tum.de"
    del idp.claims["schac_home_organization"]
    idp.install(respx_mock)
    client = app.test_client()
    await institution_sign_in(client, idp)
    await ask(client)
    row = await the_request(admin)
    await admin.post(f"/api/admin/access-requests/{row['id']}/approve")
    assert await repo.get_registered_email("ada@tum.de") is None


async def test_approving_an_identity_bound_meanwhile_is_refused(asker, admin, repo):
    await ask(asker)
    row = await the_request(admin)
    someone = await repo.create_user("Ada")
    await repo.add_identity(ISSUER, "pairwise-1", someone.id)
    response = await admin.post(f"/api/admin/access-requests/{row['id']}/approve")
    assert response.status_code == 409


async def test_asking_needs_an_institutional_sign_in(app):
    assert (await ask(app.test_client()))[0] == 403


async def test_the_sign_in_behind_a_request_goes_stale(app, idp, monkeypatch):
    monkeypatch.setattr(route_auth, "PENDING_LIFETIME_S", -1)
    client = app.test_client()
    await institution_sign_in(client, idp)
    assert (await ask(client))[0] == 403


@pytest.mark.parametrize(
    "url",
    [
        "http://www.tum.de/people/ada",
        "javascript:alert(1)",
        "https://user:pw@www.tum.de/",
        "https://localhost/",
        "https://www.tum.de/" + "x" * 500,
        "",
        # accepted by Python's urlsplit, refused by a browser's URL parser
        "https://www.tum.de:99999/",
        "https://www.tum<x.de/",
        "https://www.tum%.de/",
    ],
    ids=[
        "plain http",
        "script",
        "credentials",
        "no domain",
        "too long",
        "missing",
        "port out of range",
        "angle bracket in host",
        "percent in host",
    ],
)
async def test_an_unusable_profile_link_is_refused(asker, url):
    status, body = await ask(asker, profile_url=url)
    assert status == 400
    assert "link" in body["error"]


@pytest.mark.parametrize(
    ("fields", "status"),
    [
        ({"group": "no-such-pi"}, 400),
        ({"group": "", "group_other": ""}, 400),
        ({"group": "", "group_other": "Visiting group of Prof. X"}, 201),
    ],
    ids=["unknown pi", "no group", "other group"],
)
async def test_the_group_comes_from_the_library_or_is_named(asker, fields, status):
    assert (await ask(asker, **fields))[0] == status
