"""One turn: the model answers, calling tools until it is ready.

An async generator of plain dicts, so the transport can be server-sent events,
a test, or anything else, and the loop itself knows nothing about HTTP. Exactly
one ``done`` or ``error`` ends it, and both carry the rounds and tool calls that
already happened, so a failure halfway is still accounted for.

The tool budget is a budget for searching, not for answering: when it is spent,
or when two rounds in a row produced nothing new, the model is asked once more,
without tools, to answer from what it has. A person then gets an answer built
on the evidence already gathered rather than a note saying the limit was hit.
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
REPEATED = (
    "You already made this exact call earlier in this answer and have its result. "
    "Use it, search for something different, or answer with what you have."
)
FAILED_AGAIN = (
    "This exact call already failed earlier in this answer with the same error. "
    "Change the arguments or use a different tool."
)
ANSWER_NOW = (
    "You have used the tool calls available for this answer. Do not call any more "
    "tools. Answer the question now from the results you already have, cite what "
    "you found, and say plainly what you could not find out."
)
# rounds in a row in which every call failed or repeated an earlier one
FRUITLESS_ROUNDS = 2
LIMIT_REACHED_ERROR = "tool_call_limit_reached"
FRUITLESS_ERROR = "tool_calls_fruitless"

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

    def answer(self, final: str = "") -> str:
        """The answer is what the model says once it stops calling tools. What
        it said between tool calls is narration ("let me check...") and is
        used only when nothing else ever came, so a cut-off turn still shows
        what it had."""
        return final or "\n\n".join(t for t in self.texts if t)

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


@dataclass
class RoundResult:
    """What one model call produced, filled in while its events stream out."""

    text: str = ""
    calls: dict[int, dict[str, str]] = field(default_factory=dict)
    cancelled: bool = False


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
    # A model that repeats a call it already made would keep doing it until the
    # round limit. A repeat is answered rather than run: a successful call with
    # a note that its result is already there, a failed one with its error
    # again, so the model is never told it has a result it does not have.
    outcomes: dict[tuple[str, str], str | None] = {}
    # Some endpoints number their calls from zero in every round. The ids pair
    # each result with its call, in the conversation and in the interface, so
    # they have to be unique for the whole turn.
    used_ids: set[str] = set()
    fruitless = 0
    why_final: str | None = None

    for number in range(max_rounds):
        progress.rounds = number + 1
        if stopped():
            yield progress.ending(progress.answer(), "cancelled")
            return
        yield {"type": "round", "round": progress.rounds, "max": max_rounds}

        result = RoundResult()
        async for event in _one_round(
            client, model, conversation, base_url, tools, extra_body, fields,
            progress, stopped, result,
        ):  # fmt: skip
            yield event
        if result.cancelled:
            yield progress.ending(progress.answer(result.text), "cancelled")
            return
        if not result.calls:
            yield progress.ending(progress.answer(result.text), None)
            return
        progress.texts.append(result.text)

        wanted = [result.calls[i] for i in sorted(result.calls)]
        for position, call in enumerate(wanted):
            if not call["id"] or call["id"] in used_ids:
                call["id"] = f"call_{progress.rounds}_{position}"
            used_ids.add(call["id"])
        conversation.append(
            {
                "role": "assistant",
                "content": result.text or None,
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

        useful = 0
        for call in wanted:
            if stopped():
                yield progress.ending(progress.answer(), "cancelled")
                return
            arguments = _arguments(call["arguments"])
            yield {
                "type": "tool_call_start",
                "id": call["id"],
                "name": call["name"],
                "args": arguments,
                "round": progress.rounds,
            }
            at = time.perf_counter()
            signature = (call["name"], json.dumps(arguments, sort_keys=True))
            if signature in outcomes:
                earlier = outcomes[signature]
                result_text = json.dumps(
                    {"repeated": True, "note": REPEATED}
                    if earlier is None
                    else {"repeated": True, "error": earlier, "note": FAILED_AGAIN}
                )
                succeeded = False
            else:
                result_text = await call_tool(call["name"], arguments)
                if not isinstance(result_text, str):
                    result_text = json.dumps(
                        result_text, ensure_ascii=False, default=str
                    )
                succeeded = not result_text.lstrip().startswith('{"error"')
                outcomes[signature] = None if succeeded else _error_of(result_text)
                useful += succeeded
            took = (time.perf_counter() - at) * 1000
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
                "preview": result_text[:RESULT_PREVIEW_CHARS],
            }
            conversation.append(
                {"role": "tool", "tool_call_id": call["id"], "content": result_text}
            )

        fruitless = 0 if useful else fruitless + 1
        if fruitless >= FRUITLESS_ROUNDS:
            why_final = FRUITLESS_ERROR
            break
    else:
        why_final = LIMIT_REACHED_ERROR

    log.info(
        "answering without tools",
        extra={
            "fields": {"model": model, "reason": why_final, "rounds": progress.rounds}
        },
    )
    if stopped():
        yield progress.ending(progress.answer(), "cancelled")
        return
    yield {
        "type": "round",
        "round": progress.rounds + 1,
        "max": max_rounds,
        "final": True,
    }
    conversation.append({"role": "user", "content": ANSWER_NOW})
    result = RoundResult()
    async for event in _one_round(
        client, model, conversation, base_url, tools, extra_body,
        {**fields, "tool_choice": "none"}, progress, stopped, result,
    ):  # fmt: skip
        yield event
    if result.cancelled:
        yield progress.ending(progress.answer(result.text), "cancelled")
        return
    yield progress.ending(progress.answer(result.text) or LIMIT_REACHED, why_final)


async def _one_round(
    client: Any,
    model: str,
    conversation: list[dict[str, Any]],
    base_url: str,
    tools: list[dict[str, Any]],
    extra_body: dict[str, Any] | None,
    fields: dict[str, Any],
    progress: Progress,
    stopped: Callable[[], bool],
    out: RoundResult,
) -> AsyncIterator[dict[str, Any]]:
    """One model call, streamed. Text deltas go out as they arrive; the text
    and the tool calls it asked for are left in ``out``."""
    pieces: list[str] = []
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
                out.text = "".join(pieces)
                out.cancelled = True
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
                accumulate(out.calls, fragment)
        for channel, piece in splitter.flush():
            if channel == "text":
                pieces.append(piece)
            yield {"type": f"{channel}_delta", "text": piece}
    finally:
        if close := getattr(stream, "close", None):
            closing = close()
            if inspect.isawaitable(closing):
                await closing
    out.text = "".join(pieces)
    log.info(
        "model round",
        extra={
            "fields": {
                "model": model,
                "round": progress.rounds,
                "duration_s": round(time.perf_counter() - started, 3),
                "tool_calls": len(out.calls),
            }
        },
    )


def _arguments(raw: str) -> dict[str, Any]:
    try:
        arguments = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return arguments if isinstance(arguments, dict) else {}


def _error_of(result: str) -> str:
    try:
        return str(json.loads(result).get("error", result))[:300]
    except (json.JSONDecodeError, AttributeError):
        return result[:300]
