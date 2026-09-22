"""Past conversations: what was asked before, and going back to it."""

from typing import Any

from quart import Blueprint, current_app, g, request

bp = Blueprint("conversation", __name__)

TITLE_CHARS = 200


def _ctx():
    return current_app.extensions["cra"]


def _view(conversation: Any, current: str | None) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "title": conversation.title or "Untitled",
        "updated_at": conversation.updated_at.isoformat(),
        "current": conversation.id == current,
    }


async def _own(ctx: Any, conversation_id: str) -> Any | None:
    """The conversation, if it belongs to whoever is asking."""
    conversation = await ctx.repo.get_conversation(conversation_id)
    if conversation is None or conversation.user_id != g.principal.user_id:
        return None
    return conversation


@bp.get("/api/conversations")
async def listing() -> dict[str, Any]:
    ctx = _ctx()
    current = g.session.data.get("conversation")
    stored = await ctx.repo.list_conversations(g.principal.user_id)
    return {"conversations": [_view(c, current) for c in stored if c.title]}


@bp.post("/api/conversations/<conversation_id>/open")
async def open_one(conversation_id: str) -> Any:
    """Make this the conversation the next question continues."""
    ctx = _ctx()
    if await _own(ctx, conversation_id) is None:
        return {"error": "No such conversation."}, 404
    ctx.turns.of(g.session.id).abandon()
    g.session.data = {**g.session.data, "conversation": conversation_id}
    await ctx.sessions.save(g.session)
    messages = await ctx.repo.messages(conversation_id)
    return {
        "conversation": conversation_id,
        "messages": [
            {"role": m.role, "content": m.content, "meta": m.meta} for m in messages
        ],
    }


@bp.put("/api/conversations/<conversation_id>")
async def rename(conversation_id: str) -> Any:
    ctx = _ctx()
    if await _own(ctx, conversation_id) is None:
        return {"error": "No such conversation."}, 404
    body = await request.get_json(silent=True) or {}
    title = str(body.get("title", "")).strip()
    if not title:
        return {"error": "Give it a name."}, 400
    await ctx.repo.rename_conversation(conversation_id, title[:TITLE_CHARS])
    return {"id": conversation_id, "title": title[:TITLE_CHARS]}


@bp.delete("/api/conversations/<conversation_id>")
async def delete(conversation_id: str) -> Any:
    ctx = _ctx()
    if await _own(ctx, conversation_id) is None:
        return {"error": "No such conversation."}, 404
    await ctx.repo.delete_conversation(conversation_id)
    if g.session.data.get("conversation") == conversation_id:
        ctx.turns.of(g.session.id).abandon()
        g.session.data = {
            k: v for k, v in g.session.data.items() if k != "conversation"
        }
        await ctx.sessions.save(g.session)
    return {"ok": True}
