"""One turn: the model answers, calling tools until it is ready.

An async generator of plain dicts, so the transport can be server-sent events,
a test, or anything else, and the loop itself knows nothing about HTTP. Exactly
one ``done`` or ``error`` ends it, and both carry the rounds and tool calls that
already happened, so a failure halfway is still accounted for.
"""

import asyncio
import inspect
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from cra.assistant.llm.client import friendly_error, open_stream

log = logging.getLogger(__name__)

RESULT_PREVIEW_CHARS = 200
ARGUMENTS_IN_LOG = 80
LIMIT_REACHED = (
    "I reached the limit on tool calls for one answer. Try asking something narrower."
)

THINK_OPEN, THINK_CLOSE = "<think>", "</think>"


class ThinkSplitter:
    """Send inline ``<think>`` blocks to the reasoning channel.

    A model served without a reasoning parser puts its thinking in the answer
    itself. A tag can be split across two chunks, so a trailing piece that could
    still become one is held back until the next chunk settles it.
    """

    def __init__(self) -> None:
        self.thinking = False
        self._held = ""

    def feed(self, text: str) -> list[tuple[str, str]]:
        buffer = self._held + text
        self._held = ""
        out: list[tuple[str, str]] = []
        while buffer:
            tag = THINK_CLOSE if self.thinking else THINK_OPEN
            channel = "reasoning" if self.thinking else "text"
            at = buffer.find(tag)
            if at >= 0:
                if at:
                    out.append((channel, buffer[:at]))
                buffer = buffer[at + len(tag) :]
                self.thinking = not self.thinking
                continue
            hold = 0
            for n in range(min(len(tag) - 1, len(buffer)), 0, -1):
                if tag.startswith(buffer[-n:]):
                    hold = n
                    break
            if len(buffer) - hold:
                out.append((channel, buffer[: len(buffer) - hold]))
            self._held = buffer[len(buffer) - hold :] if hold else ""
            buffer = ""
        return out

    def flush(self) -> list[tuple[str, str]]:
        if not self._held:
            return []
        out = [("reasoning" if self.thinking else "text", self._held)]
        self._held = ""
        return out


def accumulate(calls: dict[int, dict[str, str]], fragment: Any) -> None:
    """Merge one streamed piece of a tool call into what is being assembled.

    Endpoints differ: some send a whole call at once, some spread the arguments
    over many pieces, some omit the index. Without an index, a piece carrying an
    id or a name starts a new call and anything else continues the last, because
    a name is never split.
    """
    index = getattr(fragment, "index", None)
    function = getattr(fragment, "function", None)
    if index is None:
        starts = bool(
            getattr(fragment, "id", None)
            or (function is not None and getattr(function, "name", None))
        )
        index = len(calls) if starts or not calls else max(calls)
    slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
    if getattr(fragment, "id", None):
        slot["id"] = fragment.id
    if function is not None:
        if getattr(function, "name", None):
            slot["name"] = function.name
        if getattr(function, "arguments", None):
            slot["arguments"] += function.arguments


@dataclass
class Progress:
    """What has happened so far, shared with the failure path."""

    started: float = field(default_factory=time.perf_counter)
    rounds: int = 0
    texts: list[str] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(
        default_factory=lambda: {"prompt": 0, "completion": 0, "total": 0}
    )

    def answer(self, partial: str = "") -> str:
        return "\n\n".join(t for t in [*self.texts, partial] if t)

    def ending(self, answer: str, error: str | None) -> dict[str, Any]:
        return {
            "type": "done",
            "answer": answer,
            "elapsed": time.perf_counter() - self.started,
            "rounds": self.rounds,
            "usage": dict(self.usage),
            "tool_calls": list(self.calls),
            "tools": list(self.tools),
            "error": error,
        }


def _count_usage(totals: dict[str, int], usage: Any) -> None:
    if usage is None:
        return
    for key, attribute in (
        ("prompt", "prompt_tokens"),
        ("completion", "completion_tokens"),
        ("total", "total_tokens"),
    ):
        totals[key] += int(getattr(usage, attribute, 0) or 0)


async def run_turn(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    system_prompt: str,
    tools: list[dict[str, Any]],
    call_tool: Callable[[str, dict[str, Any]], Any],
    base_url: str = "",
    max_rounds: int = 10,
    cancel: asyncio.Event | None = None,
    extra_body: dict[str, Any] | None = None,
    fields: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream one answer. Never raises: a failure becomes the final event."""
    progress = Progress()
    try:
        async for event in _rounds(
            client,
            model,
            messages,
            system_prompt,
            tools,
            call_tool,
            base_url,
            max_rounds,
            cancel,
            extra_body,
            fields or {},
            progress,
        ):
            yield event
    except Exception as exc:
        log.exception("the turn failed", extra={"fields": {"model": model}})
        message = friendly_error(exc)
        event = progress.ending(
            progress.answer() or f"Error: {message}", type(exc).__name__
        )
        event |= {"type": "error", "message": message, "error_type": type(exc).__name__}
        yield event


async def _rounds(
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    system_prompt: str,
    tools: list[dict[str, Any]],
    call_tool: Callable[[str, dict[str, Any]], Any],
    base_url: str,
    max_rounds: int,
    cancel: asyncio.Event | None,
    extra_body: dict[str, Any] | None,
    fields: dict[str, Any],
    progress: Progress,
) -> AsyncIterator[dict[str, Any]]:
    conversation = [{"role": "system", "content": system_prompt}, *messages]
    stopped = lambda: cancel is not None and cancel.is_set()  # noqa: E731

    for number in range(max_rounds):
        progress.rounds = number + 1
        if stopped():
            yield progress.ending(progress.answer(), "cancelled")
            return
        yield {"type": "round", "round": progress.rounds, "max": max_rounds}

        pieces: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        splitter = ThinkSplitter()
        started = time.perf_counter()
        stream = await open_stream(
            client,
            model=model,
            messages=conversation,
            base_url=base_url,
            tools=tools,
            extra_body=extra_body,
            **fields,
        )
        try:
            async for chunk in stream:
                if stopped():
                    yield progress.ending(progress.answer("".join(pieces)), "cancelled")
                    return
                _count_usage(progress.usage, getattr(chunk, "usage", None))
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                reasoning = getattr(delta, "reasoning_content", None) or getattr(
                    delta, "reasoning", None
                )
                if reasoning:
                    yield {"type": "reasoning_delta", "text": reasoning}
                if content := getattr(delta, "content", None):
                    for channel, piece in splitter.feed(content):
                        if channel == "text":
                            pieces.append(piece)
                        yield {"type": f"{channel}_delta", "text": piece}
                for fragment in getattr(delta, "tool_calls", None) or []:
                    accumulate(calls, fragment)
            for channel, piece in splitter.flush():
                if channel == "text":
                    pieces.append(piece)
                yield {"type": f"{channel}_delta", "text": piece}
        finally:
            if close := getattr(stream, "close", None):
                result = close()
                if inspect.isawaitable(result):
                    await result

        text = "".join(pieces)
        progress.texts.append(text)
        log.info(
            "model round",
            extra={
                "fields": {
                    "model": model,
                    "round": progress.rounds,
                    "duration_s": round(time.perf_counter() - started, 3),
                    "tool_calls": len(calls),
                }
            },
        )
        if not calls:
            yield progress.ending(progress.answer(), None)
            return

        wanted = [calls[i] for i in sorted(calls)]
        for position, call in enumerate(wanted):
            if not call["id"]:
                call["id"] = f"call_{progress.rounds}_{position}"
        conversation.append(
            {
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["arguments"]},
                    }
                    for c in wanted
                ],
            }
        )

        for call in wanted:
            if stopped():
                yield progress.ending(progress.answer(), "cancelled")
                return
            try:
                arguments = json.loads(call["arguments"] or "{}")
                if not isinstance(arguments, dict):
                    arguments = {}
            except json.JSONDecodeError:
                arguments = {}
            yield {
                "type": "tool_call_start",
                "id": call["id"],
                "name": call["name"],
                "args": arguments,
                "round": progress.rounds,
            }
            at = time.perf_counter()
            result = await call_tool(call["name"], arguments)
            if not isinstance(result, str):
                result = json.dumps(result, ensure_ascii=False, default=str)
            took = (time.perf_counter() - at) * 1000
            succeeded = not result.lstrip().startswith('{"error"')
            progress.tools.append(
                {"name": call["name"], "ms": round(took), "ok": succeeded}
            )
            progress.calls.append(
                f"`{call['name']}({call['arguments'][:ARGUMENTS_IN_LOG]})`"
            )
            yield {
                "type": "tool_call_end",
                "id": call["id"],
                "name": call["name"],
                "ok": succeeded,
                "ms": round(took),
                "preview": result[:RESULT_PREVIEW_CHARS],
            }
            conversation.append(
                {"role": "tool", "tool_call_id": call["id"], "content": result}
            )

    yield progress.ending(progress.answer(LIMIT_REACHED), "tool_call_limit_reached")
