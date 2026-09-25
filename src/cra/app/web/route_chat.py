"""Asking a question, and watching the answer arrive."""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from quart import Blueprint, Response, current_app, g, request

from cra.app.web import stored_sources
from cra.app.web.route_preferences import selection
from cra.assistant.chat import title as title_
from cra.assistant.chat.orchestrator import run_turn
from cra.assistant.llm import params as params_
from cra.assistant.llm.client import make_client

log = logging.getLogger(__name__)

bp = Blueprint("chat", __name__)

MAX_PROMPT_CHARS = 20_000
# A proxy that sees no bytes for a minute closes the connection, and a model
# endpoint can take longer than that to send its first token.
KEEPALIVE_S = 15.0


def _ctx():
    return current_app.extensions["cra"]


def _frame(event: dict[str, Any]) -> str:
    if event["type"] == "keepalive":
        return ": keepalive\n\n"
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


async def paced(
    events: AsyncIterator[dict[str, Any]], interval: float
) -> AsyncIterator[dict[str, Any]]:
    """The events as they come, and a keepalive whenever none came for a while.

    The next event is awaited in a task so that waiting for it can time out
    without cancelling it; the source keeps running across keepalives.
    """
    source = events.__aiter__()
    pending: asyncio.Task[dict[str, Any]] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(source.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield {"type": "keepalive"}
                continue
            task, pending = pending, None
            try:
                yield task.result()
            except StopAsyncIteration:
                return
    finally:
        if pending is not None:
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending


async def _conversation(ctx: Any, session: Any, principal: Any) -> str:
    """The conversation this session is in, started on first use.

    The session is trusted for the id only, never for who may continue it:
    the conversation has to belong to the principal, or a fresh one starts.
    """
    known = session.data.get("conversation")
    if known:
        found = await ctx.repo.get_conversation(str(known))
        if found is not None and found.user_id == principal.user_id:
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

    await stored_sources.restore(ctx, g.session, g.principal.user_id)
    chosen = await selection(ctx, g.session)
    conversation_id = await _conversation(ctx, g.session, g.principal)
    history = await _history(ctx, conversation_id)
    await ctx.repo.add_message(conversation_id, "user", question)
    first_turn = not history
    if first_turn:
        await ctx.repo.rename_conversation(conversation_id, title_.fallback(question))

    slot = ctx.turns.of(g.session.id)
    epoch, cancel = await slot.start()
    # Everything the turn needs is read here, while the request context is
    # still there: the generator below outlives it.
    turn = _Turn(
        ctx=ctx,
        session_id=g.session.id,
        conversation_id=conversation_id,
        chosen=chosen,
        history=history,
        question=question,
        tier=g.principal.tier,
        cancel=cancel,
        name_it=first_turn,
    )

    async def stream():
        yield _frame({"type": "start", "model": chosen["model"]})
        final: dict[str, Any] | None = None
        sent = 0
        reason = "complete"
        try:
            async for event in paced(turn.run(), KEEPALIVE_S):
                yield _frame(event)
                if event["type"] == "keepalive":
                    continue
                sent += 1
                if event["type"] in ("done", "error"):
                    final = event
        except BaseException as exc:
            reason = type(exc).__name__
            raise
        finally:
            # the reader may be gone; the answer still belongs in the history
            cancel.set()
            slot.finish(epoch)
            log.info(
                "turn finished",
                extra={
                    "fields": {
                        "reason": reason,
                        "events": sent,
                        "answered": final is not None,
                        "rounds": (final or {}).get("rounds"),
                        "error": (final or {}).get("error"),
                    }
                },
            )
            await asyncio.shield(turn.store(final))

    response = Response(
        stream(),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # nginx buffers by default, which would hold the whole answer back
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
    # Quart cancels a response after RESPONSE_TIMEOUT (a minute) unless told
    # otherwise, and an answer with a few tool rounds takes longer than that.
    response.timeout = None
    return response


@dataclass
class _Turn:
    """One answer, detached from the request that asked for it."""

    ctx: Any
    session_id: str
    conversation_id: str
    chosen: dict[str, Any]
    history: list[dict[str, Any]]
    question: str
    tier: Any
    cancel: asyncio.Event
    name_it: bool = False

    async def run(self) -> AsyncIterator[dict[str, Any]]:
        ctx = self.ctx
        tool_ctx = ctx.tool_context(self.tier)
        # a source the user connected adds its tools to this turn's list; one
        # that stopped answering adds a single entry saying so
        tools = [
            *ctx.registry.schemas(self.tier),
            *await ctx.remote.schemas(self.session_id),
        ]
        fields = params_.request_fields(ctx.settings, self.chosen["params"])
        extra_body, plain = params_.split(fields)
        async for event in run_turn(
            make_client(ctx.settings),
            model=self.chosen["model"],
            messages=[*self.history, {"role": "user", "content": self.question}],
            system_prompt=ctx.system_prompt,
            tools=tools,
            call_tool=lambda name, arguments: self.call(name, arguments, tool_ctx),
            base_url=ctx.settings.llm_base_url,
            max_rounds=int(self.chosen["params"]["max_tool_rounds"] or 10),
            cancel=self.cancel,
            extra_body=extra_body or None,
            fields=plain,
        ):
            yield event

    async def call(self, name: str, arguments: dict[str, Any], tool_ctx: Any) -> Any:
        ctx = self.ctx
        if ctx.remote.kind_of(name) is not None:
            return await ctx.remote.call(self.session_id, name, arguments)
        return await ctx.registry.call(name, arguments, tool_ctx)

    async def store(self, final: dict[str, Any] | None) -> None:
        if final is None:
            return
        await self.ctx.repo.add_message(
            self.conversation_id,
            "assistant",
            final.get("answer", ""),
            {
                "model": self.chosen["model"],
                "elapsed": final.get("elapsed", 0),
                "tools": final.get("tools", []),
                "tool_calls": final.get("tool_calls", []),
                "error": final.get("error"),
            },
        )
        # The name is a second model call. It runs after the stream has ended,
        # so the interface is free as soon as the answer is, and a slow or
        # failed naming costs nobody anything.
        answer = final.get("answer", "")
        if self.name_it and answer.strip():
            self.ctx.spawn(self.name(answer), "name the conversation")

    async def name(self, answer: str) -> None:
        suggested = await title_.suggest(
            self.ctx.settings, self.chosen["model"], self.question, answer
        )
        if suggested:
            await self.ctx.repo.rename_conversation(self.conversation_id, suggested)


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
