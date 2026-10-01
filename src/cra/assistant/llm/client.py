"""The connection to the model endpoint.

One OpenAI-compatible call, ``POST /chat/completions``, streamed as server-sent
events or answered whole. That is all the assistant asks of an endpoint, so it
is spoken over the app's httpx client rather than through a vendor SDK.

Three things learned from running against academic gateways are built in here
rather than discovered again: some answer a share of requests with an immediate
error that a retry fixes, some hang without ever closing the connection, and
some answer a request for token counts in the stream with a server error rather
than refusing the field.
"""

import asyncio
import json
import logging
import random
import threading
from collections.abc import AsyncIterator
from typing import Any

import httpx

from cra.config.settings import Settings

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 15.0
# request timeout, conflict, throttle; every server error is retried as well
RETRY_STATUSES = frozenset({408, 409, 429})
BACKOFF_S, BACKOFF_MAX_S = 0.5, 8.0
# a longer Retry-After is not worth keeping someone waiting for
RETRY_AFTER_MAX_S = 60.0

# Endpoints that answered a usage request with an error. Module level because
# it is a fact about a URL, not about a user, and it carries no user data.
_no_usage_in_stream: set[str] = set()
_lock = threading.Lock()


class LLMError(Exception):
    """The endpoint refused or failed; ``status`` is the HTTP status if any."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LLMTimeout(LLMError):
    pass


class LLMUnreachable(LLMError):
    pass


def _message(body: Any, fallback: str) -> str:
    """The ``error.message`` of an OpenAI-style error body, if it has one."""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    if isinstance(error, str) and error:
        return error
    return fallback


def _status_error(response: httpx.Response) -> LLMError:
    try:
        body = response.json()
    except ValueError:
        body = None
    detail = _message(body, response.text.strip()[:300] or response.reason_phrase)
    return LLMError(f"HTTP {response.status_code}: {detail}", response.status_code)


def _retry_after(response: httpx.Response) -> float | None:
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return seconds if 0 < seconds <= RETRY_AFTER_MAX_S else None


class ChatStream:
    """The chunks of one streamed answer, decoded from server-sent events."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        data: list[str] = []
        try:
            async for line in self._response.aiter_lines():
                if line.startswith("data:"):
                    data.append(line[5:].removeprefix(" "))
                    continue
                # comments (keep-alives), event:, id: and retry: carry nothing
                if line or not data:
                    continue
                payload, data = "\n".join(data), []
                if payload.startswith("[DONE]"):
                    return
                yield self._chunk(payload)
            # a last event without its closing blank line still counts
            if data and not (payload := "\n".join(data)).startswith("[DONE]"):
                yield self._chunk(payload)
        except httpx.TimeoutException as exc:
            raise LLMTimeout("the endpoint stopped sending") from exc
        except httpx.TransportError as exc:
            raise LLMUnreachable(f"the stream broke off: {exc}") from exc

    @staticmethod
    def _chunk(payload: str) -> dict[str, Any]:
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"unreadable event from the endpoint: {payload[:200]}"
            ) from exc
        if not isinstance(chunk, dict):
            raise LLMError(f"unexpected event from the endpoint: {payload[:200]}")
        # a gateway that fails midway says so in an event of its own
        if chunk.get("error"):
            raise LLMError(_message(chunk, "the endpoint failed while answering"))
        return chunk

    async def close(self) -> None:
        await self._response.aclose()


class ChatClient:
    """Gives up rather than waiting forever, and retries what a retry fixes.

    The two together bound how long someone waits: retries times the timeout.
    """

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._http = http
        self._url = settings.llm_base_url.rstrip("/") + "/chat/completions"
        self._headers = {
            "Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}"
        }
        self._retries = settings.llm_retries
        self._timeout = httpx.Timeout(settings.llm_timeout_s, connect=CONNECT_TIMEOUT_S)

    async def stream(self, request: dict[str, Any]) -> ChatStream:
        response = await self._send({**request, "stream": True}, self._timeout)
        return ChatStream(response)

    async def complete(
        self, request: dict[str, Any], timeout_s: float | None = None
    ) -> dict[str, Any]:
        limits = (
            self._timeout
            if timeout_s is None
            else httpx.Timeout(timeout_s, connect=CONNECT_TIMEOUT_S)
        )
        response = await self._send(request, limits)
        try:
            await response.aread()
        finally:
            await response.aclose()
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMError(
                "the endpoint answered with something other than JSON"
            ) from exc
        if not isinstance(body, dict) or body.get("error"):
            raise LLMError(_message(body, "the endpoint answered without a completion"))
        return body

    async def _send(
        self, body: dict[str, Any], limits: httpx.Timeout
    ) -> httpx.Response:
        """The response once its status is a success; the body is still unread."""
        for attempt in range(self._retries + 1):
            last = attempt == self._retries
            request = self._http.build_request(
                "POST", self._url, json=body, headers=self._headers, timeout=limits
            )
            try:
                response = await self._http.send(request, stream=True)
            except httpx.TimeoutException as exc:
                if last:
                    raise LLMTimeout("the endpoint did not answer in time") from exc
                await asyncio.sleep(self._backoff(attempt))
                continue
            except httpx.TransportError as exc:
                if last:
                    raise LLMUnreachable(
                        f"the endpoint could not be reached: {exc}"
                    ) from exc
                await asyncio.sleep(self._backoff(attempt))
                continue
            if response.is_success:
                return response
            await response.aread()
            await response.aclose()
            status = response.status_code
            if last or not (status in RETRY_STATUSES or status >= 500):
                raise _status_error(response)
            log.info(
                "retrying the model endpoint",
                extra={"fields": {"status": status, "attempt": attempt + 1}},
            )
            await asyncio.sleep(_retry_after(response) or self._backoff(attempt))
        raise AssertionError("unreachable: the last attempt returns or raises")

    @staticmethod
    def _backoff(attempt: int) -> float:
        # exponential, with jitter so that callers failing together spread out
        return min(BACKOFF_S * 2**attempt, BACKOFF_MAX_S) * (1 - 0.25 * random.random())


async def open_stream(client: Any, request: dict[str, Any], base_url: str) -> Any:
    """Start a streamed completion, asking for token counts where they work."""
    with _lock:
        worth_trying = base_url not in _no_usage_in_stream
    if worth_trying:
        try:
            return await client.stream(
                {**request, "stream_options": {"include_usage": True}}
            )
        except LLMError as exc:
            if exc.status is None:
                raise
            # One gateway answers this with a 500 and another with a 400 naming
            # the field. Either way it is unsupported, so retry without it and
            # remember; a real outage then fails on the retry instead.
            with _lock:
                _no_usage_in_stream.add(base_url)
            log.warning(
                "token counts in the stream are unsupported here",
                extra={"fields": {"base_url": base_url, "status": exc.status}},
            )
    return await client.stream(request)


def friendly_error(exc: Exception) -> str:
    """What to show someone who asked a question and got a failure."""
    if isinstance(exc, LLMTimeout):
        return "The model endpoint stopped responding. Try again or pick another model."
    if isinstance(exc, LLMUnreachable):
        return "The model endpoint could not be reached."
    status = exc.status if isinstance(exc, LLMError) else None
    if status == 401:
        return (
            "The model endpoint rejected our API key. An administrator has to renew it."
        )
    if status == 429:
        return "The model endpoint is rate-limiting us. Try again in a moment."
    if status and status >= 500:
        return (
            f"The model endpoint failed with HTTP {status} even after retries. "
            "It is probably overloaded; try again or pick another model."
        )
    return (str(exc).strip() or type(exc).__name__)[:300]
