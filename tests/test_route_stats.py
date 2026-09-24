"""Stats for nerds: totals over the stored answers."""

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


async def answer(repo, conversation_id, model, seconds, tools, error=None):
    await repo.add_message(
        conversation_id,
        "assistant",
        "…",
        {"model": model, "elapsed": seconds, "tools": tools, "error": error},
    )


async def stats(client):
    return await (await client.get("/api/stats")).get_json()


async def test_nothing_asked_yet_counts_as_zero(client):
    body = await stats(client)
    assert body["usage"]["turns"] == 0
    assert body["usage"]["avg_latency_ms"] == 0
    assert (body["tools"], body["models"]) == ([], [])


async def test_answers_are_totalled_by_model_and_tool(app, client):
    repo = app.extensions["cra"].repo
    user = (await repo.list_users())[0]
    conversation = await repo.create_conversation(user.id, "t")
    await repo.add_message(conversation.id, "user", "question")
    search = {"name": "search_papers", "ms": 100, "ok": True}
    await answer(repo, conversation.id, "m1", 1.0, [search, search])
    await answer(
        repo,
        conversation.id,
        "m2",
        3.0,
        [{"name": "search_papers", "ms": 400, "ok": False}],
        error="boom",
    )

    body = await stats(client)
    assert body["usage"] == {
        "turns": 2,
        "people": 1,
        "conversations": 1,
        "error_turns": 1,
        "avg_latency_ms": 2000,
        "feedback": 0,
    }
    assert body["tools"] == [
        {"name": "search_papers", "calls": 3, "errors": 1, "avg_ms": 200}
    ]
    assert {m["name"]: m["turns"] for m in body["models"]} == {"m1": 1, "m2": 1}
