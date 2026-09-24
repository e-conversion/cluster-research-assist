from datetime import timedelta

import pytest

from cra.app.history.repository import utcnow, valid_email
from cra.app.history.tables import new_id


async def test_registered_emails_are_normalised(repo):
    await repo.add_registered_email("  Ada@Example.ORG ", created_by="cli")
    row = await repo.get_registered_email("ada@example.org")
    assert row is not None
    assert row.user_id is None
    assert [e.email for e in await repo.list_registered_emails()] == ["ada@example.org"]
    assert await repo.remove_registered_email("ADA@example.org") is True
    assert await repo.remove_registered_email("ada@example.org") is False


async def test_identity_lookup_and_listing(repo):
    user = await repo.create_user("Ada")
    await repo.add_identity("https://idp", "sub-1", user.id, "tum.de")
    await repo.add_identity("https://idp2", "sub-2", user.id)
    found = await repo.get_identity("https://idp", "sub-1")
    assert found is not None
    assert found.user_id == user.id
    assert found.home_organization == "tum.de"
    assert await repo.get_identity("https://idp", "sub-2") is None
    assert [i.issuer for i in await repo.list_identities(user.id)] == [
        "https://idp",
        "https://idp2",
    ]


async def test_user_activation_flag(repo):
    user = await repo.create_user("Ada")
    assert await repo.set_user_active(user.id, False) is True
    assert (await repo.get_user(user.id)).is_active is False
    assert await repo.set_user_active("nope", False) is False


async def test_deleting_a_user_cascades_to_identities_and_sessions(repo):
    user = await repo.create_user("Ada")
    await repo.add_identity("i", "s", user.id)
    await repo.create_session("sid", utcnow() + timedelta(hours=1), user.id)
    async with repo._sessions() as s, s.begin():
        await s.delete(await s.get(type(user), user.id))
    assert await repo.get_identity("i", "s") is None
    assert await repo.get_session("sid") is None


async def test_sessions_expire_and_are_purged(repo):
    await repo.create_session("live", utcnow() + timedelta(hours=1))
    await repo.create_session("dead", utcnow() - timedelta(seconds=1))
    await repo.create_session("dead2", utcnow() - timedelta(seconds=1))
    assert await repo.get_session("dead") is None
    assert await repo.purge_expired_sessions() == 1
    live = await repo.get_session("live")
    assert live is not None
    assert live.data == {}


async def test_session_data_and_user_are_updated(repo):
    user = await repo.create_user("Ada")
    await repo.create_session("sid", utcnow() + timedelta(hours=1))
    await repo.update_session("sid", user_id=user.id, data={"oidc": {"state": "x"}})
    row = await repo.get_session("sid")
    assert row.user_id == user.id
    assert row.data == {"oidc": {"state": "x"}}
    await repo.delete_session("sid")
    assert await repo.get_session("sid") is None


@pytest.mark.parametrize(
    ("address", "ok"),
    [
        ("Ada@Example.org", True),
        ("ab12cde@mytum.de", True),
        ("first.last+tag@sub.uni.de", True),
        ("ada", False),
        ("ada@localhost", False),
        ("a@b@c.de", False),
        ("ada@exämple.de", False),
        ('"ada l"@example.org', False),
        ("ada@example.org (comment)", False),
        ("ada..l@example.org", False),
        ("", False),
    ],
)
def test_only_plain_addresses_can_be_invited(address, ok):
    assert valid_email(address) is ok


def test_ids_can_be_passed_on_the_command_line():
    """An id starting with "-" is read by argparse as an option."""
    ids = {new_id() for _ in range(2000)}
    assert not [i for i in ids if not i.isalnum()]
