"""One turn of the conversation, driven by a scripted endpoint."""

import asyncio

import httpx
import openai
import pytest
from fakes import FakeOpenAI, reasoning_chunk, text_chunk, tool_chunk, usage_chunk

from cra.assistant.chat.orchestrator import (
    LIMIT_REACHED,
    ThinkSplitter,
    framed,
    run_turn,
)


async def collect(client, **overrides):
    settings = {
        "model": "m",
        "messages": [{"role": "user", "content": "q"}],
        "system_prompt": "SYS",
        "tools": [],
        "call_tool": None,
        **overrides,
    }
    return [event async for event in run_turn(client, **settings)]


def kinds(events):
    return [e["type"] for e in events]


async def test_a_plain_answer_streams_and_ends_once():
    client = FakeOpenAI([[text_chunk("Hel"), text_chunk("lo")]])
    events = await collect(client)
    assert kinds(events) == ["round", "text_delta", "text_delta", "done"]
    assert events[-1]["answer"] == "Hello"
    assert events[-1]["error"] is None
    assert events[-1]["rounds"] == 1
    assert client.streams[0].closed


async def test_the_system_prompt_leads_the_conversation():
    client = FakeOpenAI([[text_chunk("ok")]])
    await collect(client)
    sent = client.calls[0]["messages"]
    assert sent[0] == {"role": "system", "content": "SYS"}
    assert sent[1] == {"role": "user", "content": "q"}


async def test_a_tool_is_called_and_its_result_goes_back():
    calls = []

    async def call_tool(name, arguments):
        calls.append((name, arguments))
        return {"papers": 2}

    client = FakeOpenAI(
        [
            [tool_chunk(0, id="c1", name="search_papers", arguments='{"query": "x"}')],
            [text_chunk("Two papers.")],
        ]
    )
    events = await collect(client, call_tool=call_tool)
    assert kinds(events) == [
        "round",
        "tool_call_start",
        "tool_call_end",
        "round",
        "text_delta",
        "done",
    ]
    assert calls == [("search_papers", {"query": "x"})]
    assert events[2]["ok"] is True
    assert events[-1]["tools"] == [
        {"name": "search_papers", "ms": pytest.approx(0, abs=1000), "ok": True}
    ]

    # the second round sees the call and its result
    second = client.calls[1]["messages"]
    assert second[-2]["tool_calls"][0]["function"]["name"] == "search_papers"
    # the result reaches the model framed as data, never as bare text it
    # might take instructions from
    assert second[-1] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": framed('{"papers": 2}'),
    }
    assert "not instructions" in second[-1]["content"]


async def test_a_tool_that_answers_with_an_error_is_marked_as_such():
    async def call_tool(name, arguments):
        return {"error": "nope"}

    client = FakeOpenAI(
        [[tool_chunk(0, id="c1", name="t", arguments="{}")], [text_chunk("sorry")]]
    )
    events = await collect(client, call_tool=call_tool)
    assert next(e for e in events if e["type"] == "tool_call_end")["ok"] is False


async def test_arguments_that_are_not_json_become_no_arguments():
    seen = []

    async def call_tool(name, arguments):
        seen.append(arguments)
        return {}

    client = FakeOpenAI(
        [[tool_chunk(0, id="c", name="t", arguments="{not json")], [text_chunk("done")]]
    )
    await collect(client, call_tool=call_tool)
    assert seen == [{}]


async def test_fragments_without_an_index_are_assembled():
    seen = []

    async def call_tool(name, arguments):
        seen.append((name, arguments))
        return {}

    client = FakeOpenAI(
        [
            [
                tool_chunk(id="a", name="first", arguments='{"x"'),
                tool_chunk(arguments=": 1}"),
                tool_chunk(id="b", name="second", arguments="{}"),
            ],
            [text_chunk("ok")],
        ]
    )
    await collect(client, call_tool=call_tool)
    assert seen == [("first", {"x": 1}), ("second", {})]


async def test_reasoning_never_reaches_the_answer():
    client = FakeOpenAI([[reasoning_chunk("thinking"), text_chunk("the answer")]])
    events = await collect(client)
    assert kinds(events) == ["round", "reasoning_delta", "text_delta", "done"]
    assert events[-1]["answer"] == "the answer"


async def test_inline_thinking_is_separated_from_the_answer():
    client = FakeOpenAI([[text_chunk("<think>hmm</think>"), text_chunk("the answer")]])
    events = await collect(client)
    assert [e["text"] for e in events if e["type"] == "reasoning_delta"] == ["hmm"]
    assert events[-1]["answer"] == "the answer"


@pytest.mark.parametrize(
    "pieces",
    [
        ["<thi", "nk>a</think>b"],
        ["<think>a</thi", "nk>b"],
        ["<", "think>a", "</think>", "b"],
    ],
    ids=["split open tag", "split close tag", "split both"],
)
async def test_a_thinking_tag_split_across_chunks_still_works(pieces):
    client = FakeOpenAI([[text_chunk(p) for p in pieces]])
    events = await collect(client)
    assert events[-1]["answer"] == "b"
    assert "".join(e["text"] for e in events if e["type"] == "reasoning_delta") == "a"


def test_the_splitter_holds_back_only_what_could_be_a_tag():
    splitter = ThinkSplitter()
    assert splitter.feed("hello <thi") == [("text", "hello ")]
    assert splitter.feed("nk>secret") == [("reasoning", "secret")]
    assert splitter.flush() == []


async def test_token_counts_are_summed_when_the_endpoint_sends_them():
    client = FakeOpenAI([[text_chunk("hi"), usage_chunk(10, 5, 15)]])
    events = await collect(client)
    assert events[-1]["usage"] == {"prompt": 10, "completion": 5, "total": 15}


async def test_an_endpoint_that_refuses_token_counts_is_asked_only_once():
    client = FakeOpenAI(
        [[text_chunk("a")], [text_chunk("b")]],
        fail_stream_options=openai.APIStatusError(
            "no",
            response=httpx.Response(
                500, request=httpx.Request("POST", "https://gateway.test")
            ),
            body=None,
        ),
    )
    await collect(client, base_url="https://gateway.test/v1")
    await collect(client, base_url="https://gateway.test/v1")
    assert all("stream_options" not in call for call in client.calls)
    assert len(client.calls) == 2


async def test_the_tool_round_limit_ends_the_turn_with_what_it_has():
    async def call_tool(name, arguments):
        return {}

    client = FakeOpenAI(
        [[tool_chunk(0, id=f"c{n}", name="t", arguments="{}")] for n in range(3)]
    )
    events = await collect(client, call_tool=call_tool, max_rounds=3)
    assert events[-1]["error"] == "tool_call_limit_reached"
    assert LIMIT_REACHED in events[-1]["answer"]
    assert events[-1]["rounds"] == 3


async def test_a_failing_endpoint_becomes_an_error_event_not_an_exception():
    client = FakeOpenAI([RuntimeError("upstream is down")])
    events = await collect(client)
    assert kinds(events) == ["round", "error"]
    assert events[-1]["error_type"] == "RuntimeError"
    assert "upstream is down" in events[-1]["message"]


async def test_a_failure_keeps_the_work_already_done():
    async def call_tool(name, arguments):
        return {"ok": 1}

    client = FakeOpenAI(
        [[tool_chunk(0, id="c", name="t", arguments="{}")], RuntimeError("gone")]
    )
    events = await collect(client, call_tool=call_tool)
    assert events[-1]["type"] == "error"
    assert events[-1]["tools"] == [
        {"name": "t", "ms": pytest.approx(0, abs=1000), "ok": True}
    ]
    assert events[-1]["rounds"] == 2


async def test_cancelling_stops_the_turn_and_keeps_the_partial_answer():
    cancel = asyncio.Event()

    async def call_tool(name, arguments):
        cancel.set()
        return {}

    client = FakeOpenAI(
        [
            [
                text_chunk("part of an answer"),
                tool_chunk(0, id="c", name="t", arguments="{}"),
            ],
            [text_chunk("never reached")],
        ]
    )
    events = await collect(client, call_tool=call_tool, cancel=cancel)
    assert events[-1]["error"] == "cancelled"
    assert events[-1]["answer"] == "part of an answer"
    assert len(client.calls) == 1, "a cancelled turn does not ask again"


async def test_a_repeated_call_is_answered_rather_than_run_again():
    """A model that repeats itself would otherwise burn every round."""
    ran = []

    async def call_tool(name, arguments):
        ran.append((name, arguments))
        return {"results": []}

    same = tool_chunk(0, id="c", name="search_pis", arguments='{"query": "x"}')
    client = FakeOpenAI(
        [
            [tool_chunk(0, id="c1", name="search_pis", arguments='{"query": "x"}')],
            [same],
            [text_chunk("nothing found")],
        ]
    )
    events = await collect(client, call_tool=call_tool)
    assert ran == [("search_pis", {"query": "x"})], "the tool ran once"
    previews = [e["preview"] for e in events if e["type"] == "tool_call_end"]
    assert '"repeated": true' in previews[1]
    assert events[-1]["answer"] == "nothing found"


async def test_the_same_tool_with_different_arguments_still_runs():
    ran = []

    async def call_tool(name, arguments):
        ran.append(arguments["query"])
        return {}

    client = FakeOpenAI(
        [
            [tool_chunk(0, id="a", name="search_pis", arguments='{"query": "one"}')],
            [tool_chunk(0, id="b", name="search_pis", arguments='{"query": "two"}')],
            [text_chunk("done")],
        ]
    )
    await collect(client, call_tool=call_tool)
    assert ran == ["one", "two"]
