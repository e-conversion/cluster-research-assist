"""The chat-completions client, against a mocked endpoint and, marked ``llm``,
a real one (CRA_LLM_API_KEY, CRA_LLM_BASE_URL, CRA_LLM_MODEL)."""

import json
import os

import httpx
import pytest
import respx
from conftest import make_settings

from cra.assistant.chat.orchestrator import run_turn
from cra.assistant.llm import client as client_
from cra.assistant.llm.client import (
    ChatClient,
    LLMError,
    LLMTimeout,
    LLMUnreachable,
    failure_kind,
    friendly_error,
    open_stream,
)

BASE = "https://gateway.test/v1"
URL = f"{BASE}/chat/completions"
# read at import time: the autouse fixture wipes CRA_ variables per test
REAL = {
    k: os.environ.get(f"CRA_LLM_{k.upper()}", "")
    for k in ("api_key", "base_url", "model")
}


def sse(*events: str) -> httpx.Response:
    return httpx.Response(
        200,
        content="".join(f"{e}\n\n" for e in events).encode(),
        headers={"content-type": "text/event-stream"},
    )


def data(chunk: dict) -> str:
    return f"data: {json.dumps(chunk)}"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    monkeypatch.setattr(client_, "BACKOFF_S", 0.0)


@pytest.fixture
async def chat(tmp_path):
    settings = make_settings(
        tmp_path, llm_api_key="secret-key", llm_base_url=BASE, llm_retries=2
    )
    async with httpx.AsyncClient() as http:
        yield ChatClient(settings, http)


async def chunks(chat, request=None):
    stream = await chat.stream(request or {"model": "m", "messages": []})
    try:
        return [chunk async for chunk in stream]
    finally:
        await stream.close()


@respx.mock
async def test_a_stream_yields_its_events_until_done(chat):
    route = respx.post(URL).mock(
        return_value=sse(
            ": OPENROUTER PROCESSING",
            data({"choices": [{"delta": {"content": "Hel"}}]}),
            "event: message\n" + data({"choices": [{"delta": {"content": "lo"}}]}),
            "data: [DONE]",
            data({"choices": [{"delta": {"content": "never"}}]}),
        )
    )
    received = await chunks(chat)
    assert [c["choices"][0]["delta"]["content"] for c in received] == ["Hel", "lo"]
    sent = route.calls[0].request
    assert json.loads(sent.content)["stream"] is True
    assert sent.headers["authorization"] == "Bearer secret-key"


@respx.mock
async def test_a_stream_left_early_closes_cleanly(chat):
    """What a cancelled turn does: stop reading, then close."""
    respx.post(URL).mock(
        return_value=sse(*[data({"choices": [{"delta": {"content": "x"}}]})] * 5)
    )
    stream = await chat.stream({"model": "m"})
    async for _ in stream:
        break
    await stream.close()


@respx.mock
async def test_an_error_in_the_middle_of_a_stream_is_raised(chat):
    respx.post(URL).mock(
        return_value=sse(
            data({"choices": [{"delta": {"content": "par"}}]}),
            data({"error": {"code": 502, "message": "provider went away"}}),
        )
    )
    with pytest.raises(LLMError, match="provider went away") as caught:
        await chunks(chat)
    assert caught.value.status == 502


@respx.mock
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_a_failure_a_retry_fixes_is_retried(chat, status):
    route = respx.post(URL).mock(
        side_effect=[httpx.Response(status), sse(data({"choices": []}))]
    )
    assert await chunks(chat) == [{"choices": []}]
    assert route.call_count == 2


@respx.mock
async def test_a_refusal_is_not_retried_and_says_why(chat):
    route = respx.post(URL).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "stream_options is not supported"}}
        )
    )
    with pytest.raises(LLMError, match="HTTP 400: stream_options") as caught:
        await chat.stream({"model": "m"})
    assert caught.value.status == 400
    assert route.call_count == 1


@respx.mock
async def test_retries_end_with_the_last_status(chat):
    route = respx.post(URL).mock(return_value=httpx.Response(503))
    with pytest.raises(LLMError) as caught:
        await chat.stream({"model": "m"})
    assert caught.value.status == 503
    assert route.call_count == 3, "the first try and two retries"


@respx.mock
async def test_a_retry_after_beyond_the_retry_window_is_not_waited_out(chat):
    route = respx.post(URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "59"})
    )
    with pytest.raises(LLMError) as caught:
        await chat.stream({"model": "m"})
    assert caught.value.status == 429
    assert route.call_count == 1


@pytest.fixture
async def impatient(tmp_path):
    """A client whose retry window has already closed."""
    settings = make_settings(
        tmp_path,
        llm_api_key="k",
        llm_base_url=BASE,
        llm_retries=3,
        llm_retry_window_s=0,
    )
    async with httpx.AsyncClient() as http:
        yield ChatClient(settings, http)


@respx.mock
async def test_a_hung_request_is_retried_once_past_the_window(impatient):
    route = respx.post(URL).mock(
        side_effect=[httpx.ReadTimeout("hung"), sse(data({"choices": []}))]
    )
    assert await chunks(impatient) == [{"choices": []}]
    assert route.call_count == 2


@respx.mock
async def test_a_request_that_hangs_twice_is_given_up(impatient):
    route = respx.post(URL).mock(side_effect=httpx.ReadTimeout("hung"))
    with pytest.raises(LLMTimeout):
        await impatient.stream({"model": "m"})
    assert route.call_count == 2


@respx.mock
@pytest.mark.parametrize(
    ("failure", "kind"),
    [
        (httpx.ReadTimeout("slow"), LLMTimeout),
        (httpx.ConnectError("no"), LLMUnreachable),
    ],
)
async def test_a_transport_failure_is_named(chat, failure, kind):
    respx.post(URL).mock(side_effect=failure)
    with pytest.raises(kind):
        await chat.complete({"model": "m"})


@respx.mock
async def test_a_completion_comes_back_whole(chat):
    route = respx.post(URL).mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "A title"}}]}
        )
    )
    body = await chat.complete({"model": "m", "max_tokens": 5}, timeout_s=3.0)
    assert body["choices"][0]["message"]["content"] == "A title"
    assert "stream" not in json.loads(route.calls[0].request.content)


@pytest.mark.parametrize(
    ("exc", "says"),
    [
        (LLMError("HTTP 401: no", status=401), "rejected our API key"),
        (LLMError("HTTP 429: slow down", status=429), "busy right now"),
        (LLMError("HTTP 402: Insufficient credits", status=402), "model budget"),
        (LLMError("HTTP 502: bad", status=502), "HTTP 502 even after retries"),
        (LLMTimeout("x"), "stopped responding"),
        (LLMUnreachable("x"), "could not be reached"),
        (LLMError("HTTP 400: unknown model", status=400), "HTTP 400: unknown model"),
    ],
)
def test_a_failure_is_told_in_plain_words(exc, says):
    assert says in friendly_error(exc)


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (LLMError("HTTP 402: Insufficient credits", status=402), "llm_out_of_credit"),
        (LLMError("HTTP 429: slow down", status=429), "llm_rate_limited"),
        (LLMError("HTTP 503: no provider", status=503), "llm_unavailable"),
        (LLMError("HTTP 400: unknown model", status=400), "llm_refused"),
        (LLMTimeout("x"), "llm_timeout"),
        (KeyError("x"), "KeyError"),
    ],
)
def test_a_failure_has_a_stable_kind(exc, kind):
    assert failure_kind(exc) == kind


@respx.mock
@pytest.mark.parametrize("status", [402, 429])
async def test_a_failure_unrelated_to_token_counts_keeps_them_on(
    chat, monkeypatch, status
):
    """Only a refusal of the field switches the counts off for good: running
    out of credit once must not cost every later turn its token counts."""
    monkeypatch.setattr(client_, "_no_usage_in_stream", set())
    route = respx.post(URL).mock(return_value=httpx.Response(status))
    with pytest.raises(LLMError):
        await open_stream(chat, {"model": "m"}, BASE)
    assert BASE not in client_._no_usage_in_stream
    assert "stream_options" in json.loads(route.calls[-1].request.content)


needs_endpoint = pytest.mark.skipif(
    not all(REAL.values()),
    reason="CRA_LLM_API_KEY, CRA_LLM_BASE_URL and CRA_LLM_MODEL not all set",
)


@pytest.fixture
async def real(tmp_path):
    settings = make_settings(
        tmp_path,
        llm_api_key=REAL["api_key"],
        llm_base_url=REAL["base_url"],
        llm_model=REAL["model"],
    )
    async with httpx.AsyncClient() as http:
        yield ChatClient(settings, http)


@pytest.mark.llm
@needs_endpoint
async def test_a_real_endpoint_answers_whole(real):
    body = await real.complete(
        {
            "model": REAL["model"],
            "max_tokens": 512,
            "messages": [{"role": "user", "content": "Reply with the word: pong"}],
        }
    )
    assert "pong" in body["choices"][0]["message"]["content"].lower()


@pytest.mark.llm
@needs_endpoint
async def test_a_real_endpoint_streams_a_tool_call_and_an_answer(real):
    called = []

    async def call_tool(name, arguments):
        called.append((name, arguments))
        return {"sum": arguments["a"] + arguments["b"]}

    add = {
        "type": "function",
        "function": {
            "name": "add",
            "description": "Add two integers.",
            "parameters": {
                "type": "object",
                "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                "required": ["a", "b"],
            },
        },
    }
    events = [
        event
        async for event in run_turn(
            real,
            model=REAL["model"],
            messages=[{"role": "user", "content": "Use the add tool: 1234 + 4321?"}],
            system_prompt="You answer by calling tools.",
            tools=[add],
            call_tool=call_tool,
            base_url=REAL["base_url"],
            max_rounds=3,
            fields={"max_tokens": 1024},
        )
    ]
    assert called == [("add", {"a": 1234, "b": 4321})]
    assert events[-1]["type"] == "done", events[-1]
    assert "5555" in events[-1]["answer"].replace(",", "")


@pytest.mark.llm
@needs_endpoint
async def test_a_real_endpoint_refusing_the_key_is_named(tmp_path):
    settings = make_settings(
        tmp_path, llm_api_key="not-a-key", llm_base_url=REAL["base_url"]
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(LLMError) as caught:
            await ChatClient(settings, http).complete(
                {"model": REAL["model"], "messages": [{"role": "user", "content": "x"}]}
            )
    assert caught.value.status == 401
    assert "not-a-key" not in str(caught.value)
