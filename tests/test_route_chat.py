"""The chat stream's pacing: keepalives while the model is silent."""

import asyncio

from cra.app.web.route_chat import _frame, paced


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
