"""Bearer tokens for the outward MCP endpoint."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from cra.app.auth import tokens
from cra.app.history.repository import utcnow
from cra.app.history.tables import McpToken


@pytest.fixture
async def owner(repo):
    return await repo.create_user("Ada")


async def test_a_minted_token_verifies_as_itself(repo, owner):
    row, value = await tokens.mint(repo, owner.id, "laptop")
    verified = await tokens.verify(repo, value)
    assert verified is not None
    assert (verified.id, verified.user_id) == (row.id, owner.id)


async def test_only_the_hash_is_stored(repo, owner):
    _, value = await tokens.mint(repo, owner.id, "laptop")
    async with repo._sessions() as s:
        stored = list(await s.scalars(select(McpToken)))
    assert [r.token_hash for r in stored] == [tokens.hash_value(value)]


async def revoke(repo, row, owner):
    await repo.revoke_token(row.id)


async def expire(repo, row, owner):
    async with repo._sessions() as s, s.begin():
        (await s.get(McpToken, row.id)).expires_at = utcnow() - timedelta(seconds=1)


async def deactivate_owner(repo, row, owner):
    await repo.set_user_active(owner.id, False)


async def delete_owner(repo, row, owner):
    await repo.delete_user(owner.id)


@pytest.mark.parametrize(
    "end", [revoke, expire, deactivate_owner, delete_owner], ids=lambda f: f.__name__
)
async def test_a_token_that_has_ended_is_refused(repo, owner, end):
    row, value = await tokens.mint(repo, owner.id, "laptop")
    await end(repo, row, owner)
    assert await tokens.verify(repo, value) is None


@pytest.mark.parametrize(
    "presented",
    ["", "cra1_", "cra1_not-a-token", "cra1.payload.signature", "Bearer cra1_x"],
)
async def test_anything_but_a_minted_value_is_refused(repo, owner, presented):
    await tokens.mint(repo, owner.id, "laptop")
    assert await tokens.verify(repo, presented) is None


async def test_use_is_recorded_at_most_once_a_minute(repo, owner):
    row, value = await tokens.mint(repo, owner.id, "laptop")
    first = utcnow()
    await tokens.verify(repo, value, now=first)
    await tokens.verify(repo, value, now=first + tokens.TOUCH_INTERVAL / 2)
    assert (await repo.get_token(row.id)).last_used_at == first

    later = first + tokens.TOUCH_INTERVAL
    await tokens.verify(repo, value, now=later)
    assert (await repo.get_token(row.id)).last_used_at == later


@pytest.mark.parametrize(
    ("label", "days", "complaint"),
    [
        ("  ", 90, "label"),
        ("x" * (tokens.LABEL_MAX + 1), 90, "at most"),
        ("laptop", 0, "1 to"),
        ("laptop", tokens.MAX_DAYS + 1, "1 to"),
    ],
    ids=["no label", "long label", "no time", "too long"],
)
async def test_a_token_that_makes_no_sense_is_not_minted(
    repo, owner, label, days, complaint
):
    with pytest.raises(ValueError, match=complaint):
        await tokens.mint(repo, owner.id, label, days)


async def test_revoking_is_limited_to_the_owner_when_one_is_named(repo, owner):
    row, _ = await tokens.mint(repo, owner.id, "laptop")
    other = await repo.create_user("Grace")
    assert await repo.revoke_token(row.id, user_id=other.id) is False
    assert await repo.revoke_token(row.id, user_id=owner.id) is True


async def test_revoking_every_token_of_an_account_leaves_others_alone(repo, owner):
    _, mine = await tokens.mint(repo, owner.id, "laptop")
    other = await repo.create_user("Grace")
    _, theirs = await tokens.mint(repo, other.id, "desktop")
    assert await repo.revoke_tokens_of(owner.id) == 1
    assert await tokens.verify(repo, mine) is None
    assert await tokens.verify(repo, theirs) is not None


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc", "abc"),
        ("bearer  abc ", "abc"),
        ("Basic abc", ""),
        ("abc", ""),
        ("", ""),
    ],
)
def test_the_token_is_read_from_the_authorization_header(header, expected):
    assert tokens.bearer(header) == expected
