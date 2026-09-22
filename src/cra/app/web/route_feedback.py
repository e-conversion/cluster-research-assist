"""Sending feedback about an answer."""

import logging
from typing import Any

from quart import Blueprint, current_app, g, request

bp = Blueprint("feedback", __name__)

log = logging.getLogger(__name__)

CATEGORIES = ("Bug report", "General feedback")
MAX_CHARS = 5_000
# enough to see what went wrong without copying a whole session into the table
MAX_MESSAGES = 40


@bp.get("/api/feedback")
async def categories() -> dict[str, Any]:
    return {"categories": list(CATEGORIES)}


@bp.post("/api/feedback")
async def submit() -> Any:
    ctx = current_app.extensions["cra"]
    body = await request.get_json(silent=True) or {}
    category = str(body.get("category", ""))
    text = str(body.get("text", "")).strip()
    if category not in CATEGORIES:
        return {"error": f"category must be one of {list(CATEGORIES)}"}, 400
    if not text:
        return {"error": "Add a note before submitting."}, 400
    if len(text) > MAX_CHARS:
        return {"error": f"at most {MAX_CHARS} characters"}, 400

    messages = [
        {"role": str(m.get("role", "")), "content": str(m.get("content", ""))}
        for m in (body.get("messages") or [])[-MAX_MESSAGES:]
        if isinstance(m, dict)
    ]
    await ctx.repo.add_feedback(
        user_id=g.principal.user_id,
        category=category,
        text=text,
        model=str(body.get("model", "")),
        messages=messages,
    )
    log.info(
        "feedback",
        extra={"fields": {"category": category, "messages": len(messages)}},
    )
    return {"ok": True}
