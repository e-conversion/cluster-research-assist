"""The chat route: the stream that carries an answer, and its pacing."""

import asyncio
import json

import pytest
from conftest import make_settings, sign_in
from fakes import FakeOpenAI, text_chunk, tool_chunk

from cra.app.web import route_chat
from cra.app.web.factory import create_app
from cra.app.web.route_chat import _frame, paced


def events_of(body: str) -> list[dict]:
    out = []
    for block in body.split("\n\n"):
        lines = [line for line in block.splitlines() if line.startswith("data:")]
        if lines:
            out.append(json.loads(lines[0][5:]))
    return out


@pytest.fixture
async def app(tmp_path):
    app = create_app(
        make_settings(tmp_path, llm_model="m", llm_api_key="k", llm_models=["m"])
    )
    async with app.test_app():
        yield app


@pytest.fixture
async def client(app):
    client = app.test_client()
    await sign_in(app, client)
    return client


@pytest.fixture
def scripted(monkeypatch):
    """The model answers from a script; the title comes for free."""

    def script(rounds):
        fake = FakeOpenAI(rounds)
        monkeypatch.setattr(route_chat, "make_client", lambda _settings: fake)
        return fake

    async def suggest(settings, model, question, answer):
        return "A short title"

    monkeypatch.setattr(route_chat.title_, "suggest", suggest)
    return script


async def test_an_answer_streams_as_events_and_lands_in_the_history(
    client, app, scripted
):
    fake = scripted(
        [
            [tool_chunk(0, id="c1", name="library_status", arguments="{}")],
            [text_chunk("There are "), text_chunk("19 papers.")],
        ]
    )
    # Quart cancels a response after RESPONSE_TIMEOUT; an answer with tool
    # rounds outlives the default minute, so the route has to opt out of it.
    app.config["RESPONSE_TIMEOUT"] = 0.2
    create = fake.chat.completions.create

    async def slowly(**kwargs):
        await asyncio.sleep(0.15)
        return await create(**kwargs)

    fake.chat.completions.create = slowly

    response = await client.post("/api/chat", json={"prompt": "How many papers?"})
    assert response.status_code == 200
    assert response.content_type.startswith("text/event-stream")

    events = events_of((await response.get_data()).decode())
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start"
    assert "tool_call_start" in kinds
    assert kinds[-1] == "done"
    assert events[-1]["answer"] == "There are 19 papers."
    assert events[-1]["error"] is None
    tool_end = next(e for e in events if e["type"] == "tool_call_end")
    assert tool_end["ok"] is True

    session = await (await client.get("/api/session")).get_json()
    assert session["busy"] is False
    roles = [m["role"] for m in session["messages"]]
    assert roles == ["user", "assistant"]
    assert session["messages"][-1]["content"] == "There are 19 papers."


async def test_the_conversation_is_named_after_the_stream_has_ended(
    client, app, scripted
):
    scripted([[text_chunk("An answer.")]])
    response = await client.post("/api/chat", json={"prompt": "A question"})
    await response.get_data()
    ctx = app.extensions["cra"]
    if ctx.background:
        await asyncio.gather(*ctx.background)
    conversations = await (await client.get("/api/conversations")).get_json()
    assert conversations["conversations"][0]["title"] == "A short title"


async def test_without_an_api_key_the_route_says_so(client, app, monkeypatch):
    ctx = app.extensions["cra"]
    monkeypatch.setattr(ctx.settings, "llm_api_key", type(ctx.settings.llm_api_key)(""))
    response = await client.post("/api/chat", json={"prompt": "Anything"})
    assert response.status_code == 503


async def slow(gaps):
    for n, gap in enumerate(gaps):
        await asyncio.sleep(gap)
        yield {"type": "text_delta", "text": str(n)}


async def test_a_keepalive_fills_every_silence_and_the_events_still_arrive():
    events = [e async for e in paced(slow([0.0, 0.25, 0.0]), 0.1)]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "text_delta"
    assert kinds[-1] == "text_delta"
    assert 1 <= kinds.count("keepalive") <= 3, "one every 0.1 s over a 0.25 s wait"
    assert [e["text"] for e in events if e["type"] == "text_delta"] == ["0", "1", "2"]


async def test_no_keepalive_is_sent_while_events_flow():
    events = [e async for e in paced(slow([0.0, 0.0, 0.0]), 1.0)]
    assert [e["type"] for e in events] == ["text_delta"] * 3


async def test_closing_the_paced_stream_stops_the_source():
    closed = asyncio.Event()

    async def source():
        try:
            yield {"type": "text_delta", "text": "a"}
            await asyncio.sleep(10)
            yield {"type": "text_delta", "text": "never"}
        finally:
            closed.set()

    stream = paced(source(), 0.05)
    assert (await stream.__anext__())["text"] == "a"
    await stream.__anext__()  # a keepalive, while the source sleeps
    await stream.aclose()
    await asyncio.wait_for(closed.wait(), 1)


def test_a_keepalive_is_a_comment_not_an_event():
    assert _frame({"type": "keepalive"}) == ": keepalive\n\n"
    assert _frame({"type": "done", "answer": "x"}).startswith("event: done\ndata: ")
