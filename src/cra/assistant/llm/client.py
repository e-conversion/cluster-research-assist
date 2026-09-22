"""The connection to the model endpoint.

Two things learned from running against an academic gateway are built in here
rather than discovered again: some endpoints answer a request for token counts
in the stream with a server error rather than refusing the field, and some hang
without ever closing the connection.
"""

import logging
import threading
from typing import Any

import httpx
import openai

from cra.config.settings import Settings

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 15.0

# Endpoints that answered a usage request with an error. Module level because
# it is a fact about a URL, not about a user, and it carries no user data.
_no_usage_in_stream: set[str] = set()
_lock = threading.Lock()


def make_client(settings: Settings) -> openai.AsyncOpenAI:
    """A client that gives up rather than waiting forever.

    Some gateways answer a share of requests with an immediate error, which a
    retry fixes, and hang on others, which only a timeout ends. The two
    together bound how long someone waits: retries times the timeout.
    """
    return openai.AsyncOpenAI(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_base_url,
        max_retries=settings.llm_retries,
        timeout=httpx.Timeout(settings.llm_timeout_s, connect=CONNECT_TIMEOUT_S),
    )


async def open_stream(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    base_url: str,
    tools: list[dict[str, Any]] | None = None,
    extra_body: dict[str, Any] | None = None,
    **fields: Any,
) -> Any:
    """Start a streamed completion, asking for token counts where they work."""
    request: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        **fields,
    }
    if tools:
        request["tools"] = tools
    if extra_body:
        request["extra_body"] = extra_body

    with _lock:
        worth_trying = base_url not in _no_usage_in_stream
    if worth_trying:
        try:
            return await client.chat.completions.create(
                stream_options={"include_usage": True}, **request
            )
        except openai.APIStatusError as exc:
            # One gateway answers this with a 500 and another with a 400 naming
            # the field. Either way it is unsupported, so retry without it and
            # remember; a real outage then fails on the retry instead.
            with _lock:
                _no_usage_in_stream.add(base_url)
            log.warning(
                "token counts in the stream are unsupported here",
                extra={"fields": {"base_url": base_url, "status": exc.status_code}},
            )
    return await client.chat.completions.create(**request)


def friendly_error(exc: Exception) -> str:
    """What to show someone who asked a question and got a failure."""
    status = getattr(exc, "status_code", None)
    if isinstance(exc, openai.AuthenticationError):
        return (
            "The model endpoint rejected our API key. An administrator has to renew it."
        )
    if isinstance(exc, openai.RateLimitError):
        return "The model endpoint is rate-limiting us. Try again in a moment."
    if isinstance(exc, openai.APIStatusError) and status and status >= 500:
        return (
            f"The model endpoint failed with HTTP {status} even after retries. "
            "It is probably overloaded; try again or pick another model."
        )
    if isinstance(exc, openai.APITimeoutError):
        return "The model endpoint stopped responding. Try again or pick another model."
    if isinstance(exc, openai.APIConnectionError):
        return "The model endpoint could not be reached."
    return (str(exc).strip() or type(exc).__name__)[:300]
