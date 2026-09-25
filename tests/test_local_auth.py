"""Password accounts: signing in, one-time links, changing and resetting."""

import asyncio
import io
from datetime import timedelta

import pytest
from conftest import PASSWORD, add_account, session_user, sign_in

from cra.app.auth import local
from cra.app.history.repository import utcnow
from cra.app.web.route_auth import (
    PASSWORD_TRIES_PER_ADDRESS,
    PASSWORD_TRIES_PER_USERNAME,
    WRONG_PASSWORD,
)
from cra.cli import main

NEW_PASSWORD = "a different long passphrase"


@pytest.fixture
def repo(app):
    return app.extensions["cra"].repo


async def login(client, username="alice", password=PASSWORD, address="10.0.0.1"):
    return await client.post(
        "/auth/password",
        json={"username": username, "password": password},
        headers={"X-Forwarded-For": address},
    )


async def admin_client(app):
    return await sign_in(app, app.test_client(), "ops", role="admin")


async def create_via_console(admin, username="bob", **fields):
    response = await admin.post(
        "/api/admin/local-users",
        json={
            "username": username,
            "name": fields.get("name", "Bob Builder"),
            **fields,
        },
    )
    return response.status_code, await response.get_json()


def token_of(link: str) -> str:
    return link.rsplit("/", 1)[1]


async def redeem(client, link_or_token: str, password: str = NEW_PASSWORD):
    return await client.post(
        "/auth/set-password",
        json={"token": token_of(link_or_token), "password": password},
    )


# signing in


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("alice", "wrong password entirely"),
        ("nobody", PASSWORD),
        ("fresh", PASSWORD),
        ("alice", "x" * (local.PASSWORD_MAX + 1)),
    ],
    ids=["wrong password", "unknown user", "no password set yet", "overlong"],
)
async def test_every_refusal_looks_the_same(app, client, repo, username, password):
    await add_account(app, "alice")
    await repo.create_local_user("fresh", "Fresh")
    response = await login(client, username, password)
    assert response.status_code == 401
    assert (await response.get_json())["error"] == WRONG_PASSWORD
    assert await session_user(client) is None


async def test_an_unknown_username_still_costs_a_hash_verification(
    app, client, monkeypatch
):
    """Otherwise the response time tells which usernames exist."""
    verified = []

    class Counting(local.PasswordHasher):
        def verify(self, *args):
            verified.append(1)
            return super().verify(*args)

    monkeypatch.setattr(
        local, "HASHER", Counting(time_cost=1, memory_cost=8, parallelism=1)
    )
    await login(client, "nobody")
    assert verified == [1]


async def test_a_username_is_matched_regardless_of_case(app, client):
    await add_account(app, "alice")
    assert (await login(client, "  Alice ")).status_code == 200


async def test_an_outdated_hash_is_upgraded_at_sign_in(app, client, repo, monkeypatch):
    user_id = await add_account(app, "alice")
    before = (await repo.get_credential(user_id)).password_hash
    monkeypatch.setattr(
        local, "HASHER", local.PasswordHasher(time_cost=2, memory_cost=8, parallelism=1)
    )
    assert (await login(client)).status_code == 200
    after = (await repo.get_credential(user_id)).password_hash
    assert after != before
    assert "t=2" in after


@pytest.mark.parametrize(
    ("limit", "vary"),
    [
        (PASSWORD_TRIES_PER_ADDRESS, "username"),
        (PASSWORD_TRIES_PER_USERNAME, "address"),
    ],
    ids=["per address", "per username"],
)
async def test_guessing_is_rate_limited(app, client, limit, vary):
    await add_account(app, "alice")
    statuses = []
    for i in range(limit[0] + 1):
        if vary == "username":
            response = await login(client, f"user{i}", "guess")
        else:
            response = await login(client, "alice", "guess", address=f"10.0.1.{i}")
        statuses.append(response.status_code)
    assert statuses[:-1] == [401] * limit[0]
    assert statuses[-1] == 429


async def test_signing_in_does_not_count_against_ones_own_username(app, client):
    """Only failures do, or anyone could keep a known username locked out."""
    await add_account(app, "alice")
    for i in range(PASSWORD_TRIES_PER_USERNAME[0] + 1):
        assert (await login(client, address=f"10.0.2.{i}")).status_code == 200


async def test_a_body_that_is_not_an_object_is_refused_not_a_crash(client):
    response = await client.post("/auth/password", json=[1])
    assert response.status_code == 401


# usernames and passwords


@pytest.mark.parametrize(
    "username",
    ["a", "-leading-dash", "has space", "ümlaut", "x" * 65, "admin", "Root"],
    ids=["short", "dash", "space", "non-ascii", "long", "admin", "root any case"],
)
def test_unusable_usernames_are_refused(username):
    with pytest.raises(local.CredentialError):
        local.check_username(username)


@pytest.mark.parametrize(
    "password",
    ["short", "x" * (local.PASSWORD_MAX + 1), "Grace.Hopper"],
    ids=["short", "long", "the username"],
)
def test_weak_passwords_are_refused(password):
    with pytest.raises(local.CredentialError):
        local.check_password(password, "grace.hopper")


# the console and one-time links


async def test_an_account_created_in_the_console_signs_in_after_its_link(app):
    admin = await admin_client(app)
    status, created = await create_via_console(admin, email="Bob@TUM.de")
    assert status == 201
    assert created["link"].startswith("/#/set-password/")

    client = app.test_client()
    assert (await login(client, "bob")).status_code == 401
    assert (await redeem(client, created["link"])).status_code == 200
    assert await session_user(client) == "Bob Builder"
    user = await app.extensions["cra"].repo.get_user(created["id"])
    assert user.email == "bob@tum.de"
    assert (await login(app.test_client(), "bob", NEW_PASSWORD)).status_code == 200


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"username": "admin"}, "too easy to guess"),
        ({"username": "ops"}, "taken"),
        ({"name": " "}, "needs a name"),
        ({"email": "not-an-address"}, "not an email address"),
        ({"role": "owner"}, "role must be"),
    ],
    ids=["reserved", "taken", "no name", "bad email", "bad role"],
)
async def test_the_console_refuses_an_unusable_account(app, fields, message):
    admin = await admin_client(app)
    status, body = await create_via_console(admin, **{"username": "bob", **fields})
    assert status == 400
    assert message in body["error"]


@pytest.mark.parametrize(
    "spoil",
    ["used", "expired", "superseded", "unknown"],
)
async def test_a_spent_or_stale_link_is_refused(app, monkeypatch, spoil):
    admin = await admin_client(app)
    if spoil == "expired":
        monkeypatch.setattr(local, "LINK_LIFETIME", -timedelta(seconds=1))
    _, created = await create_via_console(admin)
    link = created["link"]
    if spoil == "used":
        await redeem(app.test_client(), link)
    elif spoil == "superseded":
        await admin.post(f"/api/admin/users/{created['id']}/password-reset")
    else:
        link = "/#/set-password/made-up"
    response = await redeem(app.test_client(), link, "yet another passphrase")
    assert response.status_code == 400
    assert "ask an admin for a new one" in (await response.get_json())["error"]


async def test_the_link_names_the_account_it_is_for(app):
    admin = await admin_client(app)
    _, created = await create_via_console(admin)
    response = await app.test_client().post(
        "/auth/password-link", json={"token": token_of(created["link"])}
    )
    assert (await response.get_json())["username"] == "bob"


async def test_a_too_short_password_does_not_spend_the_link(app):
    admin = await admin_client(app)
    _, created = await create_via_console(admin)
    client = app.test_client()
    assert (await redeem(client, created["link"], "short")).status_code == 400
    assert (await redeem(client, created["link"])).status_code == 200


async def test_two_racing_redemptions_let_exactly_one_through(app):
    admin = await admin_client(app)
    _, created = await create_via_console(admin)
    responses = await asyncio.gather(
        *(redeem(app.test_client(), created["link"]) for _ in range(2))
    )
    assert sorted(r.status_code for r in responses) == [200, 400]


async def test_a_reset_revokes_the_old_password_everywhere(app):
    admin = await admin_client(app)
    alice = await sign_in(app, app.test_client(), "alice")
    user_id = (
        await app.extensions["cra"].repo.get_credential_by_username("alice")
    ).user_id

    response = await admin.post(f"/api/admin/users/{user_id}/password-reset")
    assert response.status_code == 200
    assert await session_user(alice) is None
    assert (await login(app.test_client())).status_code == 401
    link = (await response.get_json())["link"]
    assert (await redeem(app.test_client(), link)).status_code == 200
    assert (await login(app.test_client(), password=NEW_PASSWORD)).status_code == 200


async def test_a_reset_revokes_the_accounts_mcp_tokens(app, repo):
    admin = await admin_client(app)
    user_id = await add_account(app, "alice")
    await repo.create_token(user_id, "laptop", "0" * 64, utcnow() + timedelta(days=1))
    await admin.post(f"/api/admin/users/{user_id}/password-reset")
    (token,) = await repo.list_tokens(user_id)
    assert token.revoked_at is not None


async def test_an_institutional_account_has_no_password_to_reset(app, repo):
    admin = await admin_client(app)
    user = await repo.create_user("Ada Lovelace")
    response = await admin.post(f"/api/admin/users/{user.id}/password-reset")
    assert response.status_code == 400


# changing one's own password


async def test_changing_the_password_signs_out_every_other_session(app):
    here = await sign_in(app, app.test_client(), "alice")
    elsewhere = await sign_in(app, app.test_client(), "alice")
    response = await here.put(
        "/api/me/password", json={"current": PASSWORD, "new": NEW_PASSWORD}
    )
    assert response.status_code == 200
    assert await session_user(here) == "alice"
    assert await session_user(elsewhere) is None
    assert (await login(app.test_client())).status_code == 401
    assert (await login(app.test_client(), password=NEW_PASSWORD)).status_code == 200


@pytest.mark.parametrize(
    ("current", "new", "message"),
    [
        ("not my password", NEW_PASSWORD, "current password is not right"),
        (PASSWORD, "short", "at least"),
    ],
    ids=["wrong current", "weak new"],
)
async def test_a_bad_password_change_is_refused(app, current, new, message):
    client = await sign_in(app, app.test_client(), "alice")
    response = await client.put(
        "/api/me/password", json={"current": current, "new": new}
    )
    assert response.status_code == 400
    assert message in (await response.get_json())["error"]


async def test_the_account_page_names_the_username(app):
    client = await sign_in(app, app.test_client(), "alice")
    assert (await (await client.get("/api/me")).get_json())["username"] == "alice"


# the command line


@pytest.fixture
def env(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        f"CRA_LIBRARY_PATH=/c\nCRA_HISTORY_URL=sqlite+aiosqlite:///{tmp_path}/cra.sqlite\n"
    )
    assert main(["--env-file", str(path), "db", "upgrade"]) == 0
    return path


def cli_verify(env, username, password) -> bool:
    from cra.app.history.engine import make_engine, make_session_factory
    from cra.app.history.repository import Repository

    async def run() -> bool:
        engine = make_engine(f"sqlite+aiosqlite:///{env.parent}/cra.sqlite")
        verified = await local.verify(
            Repository(make_session_factory(engine)), username, password
        )
        await engine.dispose()
        return verified.ok

    return asyncio.run(run())


def test_the_first_admin_is_created_on_the_command_line(env, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    cli = ["--env-file", str(env), "users", "create", "grace", "--name", "Grace H"]
    assert main([*cli, "--admin", "--password-stdin"]) == 0
    assert "it can sign in now" in capsys.readouterr().out
    assert cli_verify(env, "grace", PASSWORD)


def test_the_command_line_refuses_a_guessable_admin_name(env, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    cli = ["--env-file", str(env), "users", "create", "admin", "--name", "A"]
    assert main([*cli, "--admin", "--password-stdin"]) == 1
    assert "too easy to guess" in capsys.readouterr().err


def test_a_forgotten_password_gets_a_link_on_the_command_line(env, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    base = ["--env-file", str(env), "users"]
    main([*base, "create", "grace", "--name", "Grace H", "--password-stdin"])
    assert main([*base, "password-link", "grace"]) == 0
    assert "/#/set-password/" in capsys.readouterr().out
    assert not cli_verify(env, "grace", PASSWORD)
