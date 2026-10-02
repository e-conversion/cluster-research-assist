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


async def test_the_database_names_its_version_and_size(repo):
    info = await repo.database_info()
    assert info["dialect"] in ("sqlite", "postgresql")
    assert info["version"]
    assert info["size"] > 0


async def test_an_address_names_the_accounts_that_use_it(repo):
    ada = await repo.create_local_user("ada", "Ada", "Ada@Uni.de", "user")
    await repo.create_local_user("bob", "Bob", "bob@uni.de", "user")
    await repo.add_registered_email("ada.l@uni.de", "test")
    await repo.link_registered_email("ada.l@uni.de", ada.id)
    for address in ("ada@uni.de", "ADA.L@uni.de"):
        assert [c.username for c in await repo.credentials_by_email(address)] == ["ada"]


async def test_only_recent_answers_are_returned(repo):
    user = await repo.create_user("A")
    conversation = await repo.create_conversation(user.id)
    await repo.add_message(conversation.id, "user", "q")
    await repo.add_message(conversation.id, "assistant", "a", {"usage": {"total": 9}})
    recent = await repo.answers_since(utcnow() - timedelta(minutes=1))
    assert [meta for _, meta in recent] == [{"usage": {"total": 9}}]
    assert await repo.answers_since(utcnow() + timedelta(minutes=1)) == []


async def test_active_users_count_people_not_sessions(repo):
    user = await repo.create_user("A")
    for sid in ("one", "two"):
        await repo.create_session(sid, utcnow() + timedelta(hours=1), user.id)
    await repo.create_session("anonymous", utcnow() + timedelta(hours=1))
    assert await repo.active_users(utcnow() - timedelta(minutes=5)) == 1
