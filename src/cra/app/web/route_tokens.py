"""Tokens for the outward MCP endpoint: each person their own, admins everyone's.

A token's value is in the response that mints it and nowhere else; every
listing carries what a token is, never what it is.
"""

from typing import Any

from quart import Blueprint, current_app, g, request

from cra.app.auth import tokens
from cra.app.web import auditlog
from cra.app.web.access import requires_admin

bp = Blueprint("tokens", __name__)


def _ctx():
    return current_app.extensions["cra"]


def _bad(message: str, status: int = 400) -> tuple[dict[str, str], int]:
    return {"error": message}, status


@bp.before_request
async def endpoint_enabled() -> Any:
    if not _ctx().settings.mcp_server_enabled:
        return _bad("this deployment serves no MCP endpoint", 404)
    return None


@bp.get("/api/tokens")
async def own_tokens() -> dict[str, Any]:
    rows = await _ctx().repo.list_tokens(g.principal.user_id)
    return {"tokens": [tokens.describe(row) for row in rows]}


@bp.post("/api/tokens")
async def mint() -> Any:
    body = await request.get_json(silent=True) or {}
    days = body.get("days", tokens.DEFAULT_DAYS)
    if type(days) is not int or days not in tokens.EXPIRY_CHOICES:
        choices = ", ".join(str(d) for d in tokens.EXPIRY_CHOICES)
        return _bad(f"days must be one of {choices}")
    try:
        row, value = await tokens.mint(
            _ctx().repo, g.principal.user_id, str(body.get("label", "")), days
        )
    except ValueError as exc:
        return _bad(str(exc))
    auditlog.record("mint_token", token=row.id, days=days)
    return {**tokens.describe(row), "token": value}, 201


@bp.delete("/api/tokens/<token_id>")
async def revoke_own(token_id: str) -> Any:
    # someone else's id answers like an unknown one: nothing is learnt by asking
    if not await _ctx().repo.revoke_token(token_id, user_id=g.principal.user_id):
        return _bad("no such token", 404)
    auditlog.record("revoke_token", token=token_id)
    return {"ok": True}


@bp.get("/api/admin/tokens")
@requires_admin
async def all_tokens() -> dict[str, Any]:
    rows = await _ctx().repo.list_all_tokens()
    return {
        "tokens": [
            {**tokens.describe(row), "owner": owner, "owner_id": row.user_id}
            for row, owner in rows
        ]
    }


@bp.delete("/api/admin/tokens/<token_id>")
@requires_admin
async def revoke_any(token_id: str) -> Any:
    token = await _ctx().repo.get_token(token_id)
    if token is None or not await _ctx().repo.revoke_token(token_id):
        return _bad("no such token", 404)
    auditlog.record("revoke_token", token=token_id, owner=token.user_id)
    return {"ok": True}
