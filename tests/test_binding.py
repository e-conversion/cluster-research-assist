"""Every branch of the first-login binding table from the SSO handover."""

import pytest

from cra.app.auth.binding import Claims, resolve_login
from cra.app.auth.principal import LoginDenied

ISS = "https://idp.test"


def claims(sub="s1", email="Ada@Example.org", **kw):
    return Claims(
        issuer=ISS, sub=sub, email=email, given_name="Ada", family_name="L", **kw
    )


@pytest.fixture
def sqlite_only(db_url):
    if "sqlite" not in db_url:
        pytest.skip("binding logic is backend independent")


async def test_registered_email_creates_and_binds_a_user(repo):
    await repo.add_registered_email("ada@example.org", "cli")
    outcome = await resolve_login(repo, claims(home_organization="tum.de"))
    assert outcome.denied is None
    user = await repo.get_user(outcome.user_id)
    assert user.display_name == "Ada L"
    assert (await repo.get_registered_email("ada@example.org")).user_id == user.id
    assert (await repo.get_identity(ISS, "s1")).home_organization == "tum.de"


async def test_known_identity_logs_in_without_email(repo):
    await repo.add_registered_email("ada@example.org", "cli")
    first = await resolve_login(repo, claims())
    again = await resolve_login(repo, claims(email=""))
    assert again.user_id == first.user_id
    assert again.denied is None


async def test_second_identity_binds_to_the_same_user(repo):
    await repo.add_registered_email("ada@example.org", "cli")
    first = await resolve_login(repo, claims(sub="s1"))
    second = await resolve_login(repo, claims(sub="s2"))
    assert second.user_id == first.user_id
    assert len(await repo.list_identities(first.user_id)) == 2


@pytest.mark.parametrize(
    ("kw", "denied"),
    [
        ({"email": "nobody@example.org"}, LoginDenied.NOT_REGISTERED),
        ({"email": ""}, LoginDenied.NO_EMAIL),
    ],
    ids=["unregistered", "missing email"],
)
async def test_unknown_identity_is_denied_without_creating_records(repo, kw, denied):
    outcome = await resolve_login(repo, claims(**kw))
    assert outcome.denied is denied
    assert outcome.user_id is None
    assert await repo.list_users() == []
    assert await repo.get_identity(ISS, "s1") is None


@pytest.mark.parametrize(
    "known_identity", [True, False], ids=["by identity", "by email"]
)
async def test_inactive_user_is_denied(repo, known_identity):
    await repo.add_registered_email("ada@example.org", "cli")
    first = await resolve_login(repo, claims(sub="s1"))
    await repo.set_user_active(first.user_id, False)
    outcome = await resolve_login(repo, claims(sub="s1" if known_identity else "s2"))
    assert outcome.denied is LoginDenied.INACTIVE
