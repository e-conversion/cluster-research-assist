"""Asking a question, and watching the answer arrive."""

import asyncio
import contextlib
import json
import logging
from typing import Any

from quart import Blueprint, Response, current_app, g, request

from cra.app.web.route_preferences import selection
from cra.assistant.chat.orchestrator import run_turn
from cra.assistant.llm import params as params_
from cra.assistant.llm.client import make_client

log = logging.getLogger(__name__)

bp = Blueprint("chat", __name__)

MAX_PROMPT_CHARS = 20_000
# how often a silent stream sends something, so a proxy does not close it
PING_S = 15


def _ctx():
    return current_app.extensions["cra"]


def _frame(event: dict[str, Any]) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _conversation(ctx: Any, session: Any, principal: Any) -> str:
    """The conversation this session is in, started on first use."""
    known = session.data.get("conversation")
    if known and await ctx.repo.get_conversation(known):
        return str(known)
    created = await ctx.repo.create_conversation(principal.user_id)
    session.data = {**session.data, "conversation": created.id}
    await ctx.sessions.save(session)
    return created.id


async def _history(ctx: Any, conversation_id: str) -> list[dict[str, Any]]:
    return [
        {"role": m.role, "content": m.content}
        for m in await ctx.repo.messages(conversation_id)
    ]


@bp.post("/api/chat")
async def chat() -> Any:
    ctx = _ctx()
    body = await request.get_json(silent=True) or {}
    question = str(body.get("prompt", "")).strip()
    if not question:
        return {"error": "Ask something first."}, 400
    if len(question) > MAX_PROMPT_CHARS:
        return {
            "error": f"A question may be at most {MAX_PROMPT_CHARS} characters."
        }, 400
    if not ctx.settings.llm_api_key.get_secret_value():
        return {
            "error": "This deployment has no API key for the model endpoint yet."
        }, 503

    limit = ctx.policy["user_chat_daily_limit"]
    allowance = ctx.limiter.check(f"chat:{g.principal.user_id}", limit)
    if not allowance.allowed:
        return {
            "error": f"You have reached today's limit of {limit} questions.",
            "retry_after": allowance.retry_after,
        }, 429

    chosen = await selection(ctx, g.session)
    conversation_id = await _conversation(ctx, g.session, g.principal)
    history = await _history(ctx, conversation_id)
    await ctx.repo.add_message(conversation_id, "user", question)
    if len(history) == 0:
        await ctx.repo.rename_conversation(conversation_id, question[:120])

    slot = ctx.turns.of(g.session.id)
    epoch, cancel = await slot.start()
    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(
        _run(ctx, conversation_id, chosen, history, question, cancel, queue)
    )

    async def stream():
        yield _frame({"type": "start", "model": chosen["model"]})
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), PING_S)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if event is None:
                    return
                yield _frame(event)
        finally:
            # the reader is gone, or the answer is complete; either way the
            # turn must not keep spending
            cancel.set()
            slot.finish(epoch)
            await asyncio.shield(_settle(task))

    return Response(
        stream(),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # nginx buffers by default, which would hold the whole answer back
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _settle(task: asyncio.Task) -> None:
    # the task reports its own failures as events; here we only wait for it
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _run(
    ctx: Any,
    conversation_id: str,
    chosen: dict[str, Any],
    history: list[dict[str, Any]],
    question: str,
    cancel: asyncio.Event,
    queue: asyncio.Queue,
) -> None:
    """Own one turn from the first token to the stored answer."""
    tier = g.principal.tier
    tool_ctx = ctx.tool_context(tier)
    schemas = ctx.registry.schemas(tier)
    fields = params_.request_fields(ctx.settings, chosen["params"])
    extra_body, plain = params_.split(fields)
    final: dict[str, Any] | None = None
    try:
        async for event in run_turn(
            make_client(ctx.settings),
            model=chosen["model"],
            messages=[*history, {"role": "user", "content": question}],
            system_prompt=ctx.system_prompt,
            tools=schemas,
            call_tool=lambda name, arguments: ctx.registry.call(
                name, arguments, tool_ctx
            ),
            base_url=ctx.settings.llm_base_url,
            max_rounds=int(chosen["params"]["max_tool_rounds"] or 10),
            cancel=cancel,
            extra_body=extra_body or None,
            fields=plain,
        ):
            await queue.put(event)
            if event["type"] in ("done", "error"):
                final = event
    except Exception as exc:
        log.exception("the turn could not start")
        final = {
            "answer": f"Error: {exc}",
            "elapsed": 0,
            "rounds": 0,
            "tools": [],
            "error": type(exc).__name__,
        }
        await queue.put(
            {"type": "error", "message": str(exc), "error_type": type(exc).__name__}
        )
    finally:
        if final is not None:
            await ctx.repo.add_message(
                conversation_id,
                "assistant",
                final.get("answer", ""),
                {
                    "model": chosen["model"],
                    "elapsed": final.get("elapsed", 0),
                    "tools": final.get("tools", []),
                    "tool_calls": final.get("tool_calls", []),
                    "error": final.get("error"),
                },
            )
        await queue.put(None)


@bp.post("/api/chat/stop")
async def stop() -> dict[str, Any]:
    slot = _ctx().turns.of(g.session.id)
    return {"ok": True, "stopped": slot.stop()}


@bp.post("/api/chat/reset")
async def reset() -> dict[str, Any]:
    """Start a new conversation. The old one stays in the history."""
    ctx = _ctx()
    stopped = ctx.turns.of(g.session.id).abandon()
    g.session.data = {k: v for k, v in g.session.data.items() if k != "conversation"}
    await ctx.sessions.save(g.session)
    return {"ok": True, "stopped": stopped}
