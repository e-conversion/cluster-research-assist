"""Past conversations: listing, reopening, renaming and deleting."""

import pytest
from conftest import make_settings

from cra.app.web.factory import create_app


@pytest.fixture
async def app(tmp_path):
    app = create_app(make_settings(tmp_path, auth_dev_user="ada"))
    async with app.test_app():
        yield app


@pytest.fixture
async def client(app):
    client = app.test_client()
    await client.get("/auth/login")
    return client


async def json_of(response):
    return await response.get_json()


async def conversation_with(app, client, title, *turns):
    """A conversation as a finished turn would leave it."""
    ctx = app.extensions["cra"]
    user = (await ctx.repo.list_users())[0]
    made = await ctx.repo.create_conversation(user.id, title)
    for role, content in turns:
        await ctx.repo.add_message(
            made.id, role, content, {"model": "m"} if role == "assistant" else None
        )
    return made


async def titles(client):
    body = await json_of(await client.get("/api/conversations"))
    return [c["title"] for c in body["conversations"]]


async def test_nothing_is_listed_before_anything_is_asked(client):
    assert await titles(client) == []


async def test_conversations_are_listed_most_recent_first(app, client):
    await conversation_with(app, client, "About perovskites", ("user", "which papers?"))
    await conversation_with(
        app, client, "About batteries", ("user", "who works on cells?")
    )
    assert await titles(client) == ["About batteries", "About perovskites"]


async def test_reopening_one_returns_it_and_continues_it(app, client):
    first = await conversation_with(
        app,
        client,
        "About perovskites",
        ("user", "which papers?"),
        ("assistant", "These two."),
    )
    body = await json_of(await client.post(f"/api/conversations/{first.id}/open"))
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][1]["meta"]["model"] == "m"

    # the session now continues that conversation
    session = await json_of(await client.get("/api/session"))
    assert session["conversation"] == first.id
    assert session["turns"] == 1
    assert [
        c["current"]
        for c in (await json_of(await client.get("/api/conversations")))[
            "conversations"
        ]
    ] == [True]


async def test_renaming_and_deleting(app, client):
    made = await conversation_with(app, client, "Untitled thoughts", ("user", "hello"))
    renamed = await client.put(
        f"/api/conversations/{made.id}", json={"title": "  Battery ageing  "}
    )
    assert (await json_of(renamed))["title"] == "Battery ageing"
    assert await titles(client) == ["Battery ageing"]
    assert (
        await client.put(f"/api/conversations/{made.id}", json={"title": " "})
    ).status_code == 400

    assert (await client.delete(f"/api/conversations/{made.id}")).status_code == 200
    assert await titles(client) == []


async def test_deleting_the_open_one_starts_a_fresh_page(app, client):
    made = await conversation_with(
        app, client, "About perovskites", ("user", "which papers?")
    )
    await client.post(f"/api/conversations/{made.id}/open")
    await client.delete(f"/api/conversations/{made.id}")
    session = await json_of(await client.get("/api/session"))
    assert session["conversation"] is None
    assert session["messages"] == []


async def test_another_persons_conversation_is_not_reachable(app, client):
    ctx = app.extensions["cra"]
    someone_else = await ctx.repo.create_user("bob")
    theirs = await ctx.repo.create_conversation(someone_else.id, "Private")
    await ctx.repo.add_message(theirs.id, "user", "secret question")

    assert await titles(client) == []
    for call, kwargs in (
        (client.post, {"path": f"/api/conversations/{theirs.id}/open"}),
        (client.delete, {"path": f"/api/conversations/{theirs.id}"}),
    ):
        assert (await call(**kwargs)).status_code == 404
    renamed = await client.put(
        f"/api/conversations/{theirs.id}", json={"title": "mine now"}
    )
    assert renamed.status_code == 404
    assert (await ctx.repo.get_conversation(theirs.id)).title == "Private"
