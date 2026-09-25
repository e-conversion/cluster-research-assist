"""Attaching a personal account on an external MCP server. The connection is
live in this session and, with CRA_SOURCE_TOKEN_KEY set, kept for the account
(``stored_sources``)."""

import logging
from typing import Any

from quart import Blueprint, current_app, g, request

from cra.app.web import stored_sources
from cra.core.connectors.registration import RegistrationError, register

log = logging.getLogger(__name__)

bp = Blueprint("connect", __name__)


def _ctx():
    return current_app.extensions["cra"]


def _source(ctx, kind: str):
    source = ctx.remote.sources.get(kind)
    if source is None:
        raise LookupError(kind)
    return source


@bp.post("/api/session/connect/<kind>")
async def connect(kind: str) -> Any:
    ctx = _ctx()
    if kind not in ctx.remote.sources:
        return {"error": f"Unknown source: {kind}"}, 404
    body = await request.get_json(silent=True) or {}
    token = str(body.get("token", "") or "").strip()
    return await _connect(ctx, kind, token)


async def _connect(ctx, kind: str, token: str) -> Any:
    result = await ctx.remote.connect(g.session.id, kind, token)
    if result["active"]:
        await stored_sources.remember(ctx, g.principal.user_id, kind, token)
    return result, 200 if result["active"] else 400


@bp.delete("/api/session/connect/<kind>")
async def disconnect(kind: str) -> Any:
    ctx = _ctx()
    if kind not in ctx.remote.sources:
        return {"error": f"Unknown source: {kind}"}, 404
    await ctx.repo.delete_source_connections(g.principal.user_id, kind)
    return await ctx.remote.disconnect(g.session.id, kind)


@bp.post("/api/session/register/<kind>")
async def register_source(kind: str) -> Any:
    """Register an API key upstream and connect with the token it returns.

    The key is passed to the registration service and kept nowhere: it is not
    stored, not logged, and never sent back to the browser.
    """
    ctx = _ctx()
    if kind not in ctx.remote.sources:
        return {"error": f"Unknown source: {kind}"}, 404
    body = await request.get_json(silent=True) or {}
    try:
        token = await register(
            ctx.http,
            _source(ctx, kind),
            base_url=str(body.get("base_url", "") or ""),
            api_key=str(body.get("api_key", "") or ""),
            profile=str(body.get("profile", "") or ""),
        )
    except RegistrationError as exc:
        return {
            "kind": kind,
            "active": False,
            "tools": 0,
            "error": str(exc),
        }, exc.status
    return await _connect(ctx, kind, token)
