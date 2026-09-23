"""What the model is told when a connected source stops answering.

A source that fails must not take the chat down with it. The tools it offered
are replaced by a single entry saying so, which is both an honest answer to
"what can you do" and something the model can report to the user.
"""

import re
from typing import Any

# the prefix already ends in "_": the name reads elab___unavailable__
UNAVAILABLE = "__unavailable__"
# the proxies take the token in the URL, and httpx puts the URL in its error
# messages: anything derived from an exception goes through this first
_TOKEN = re.compile(r"(token=)[^&\s'\"]*", re.IGNORECASE)


def redacted(text: str) -> str:
    return _TOKEN.sub(r"\1***", text)


def unavailable_name(prefix: str) -> str:
    return f"{prefix}{UNAVAILABLE}"


def unavailable_schema(prefix: str, label: str, error: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": unavailable_name(prefix),
            "description": (
                f"{label} is connected but not answering right now: {error} "
                "Calling this tool only repeats that. Tell the user, and answer "
                "with the sources that do work."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    }


def friendly_error(exc: BaseException) -> str:
    """A short, actionable line from a transport or protocol failure.

    anyio wraps transport failures in an ExceptionGroup, so the real cause is
    usually one level down; the group's own text says nothing useful.
    """
    for nested in getattr(exc, "exceptions", ()) or ():
        if (found := friendly_error(nested)) != _plain(nested):
            return found
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if "401" in low or "unauthorized" in low:
        return "the token was refused (401) — register a new one."
    if "403" in low or "forbidden" in low:
        return "the token is not allowed to do that (403)."
    if "404" in low:
        return "the endpoint was not found (404)."
    if "timed out" in low or "timeout" in low or "connect" in low:
        return "the server did not answer in time."
    return _plain(exc)


def _plain(exc: BaseException) -> str:
    return redacted(f"{type(exc).__name__}: {exc}")[:200]
