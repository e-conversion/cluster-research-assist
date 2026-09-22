"""What the frontend asks on boot: the public configuration, and who is
calling. Both answer for anonymous visitors, who may use the site."""

from typing import Any

from quart import Blueprint, current_app, g

from cra import __version__

bp = Blueprint("session", __name__)


def _ctx():
    return current_app.extensions["cra"]


@bp.get("/api/config")
async def config() -> dict[str, Any]:
    ctx = _ctx()
    settings, policy = ctx.settings, ctx.policy
    counts = ctx.corpus.counts if ctx.corpus else {}
    return {
        "title": settings.cluster_display_name,
        "cluster": {
            "name": settings.cluster_name,
            "display_name": settings.cluster_display_name,
            "website": settings.cluster_website,
        },
        "auth": {
            "provider": settings.auth_provider,
            "login_url": "auth/login",
            "logout_url": "auth/logout",
        },
        "notice": policy["notice"],
        "corpus": counts,
        "placeholder": _placeholder(counts),
        "examples": [],
        "models": policy["llm_models"],
        "default_model": policy["llm_model"],
        "sources": {},
        "max_tool_rounds": policy["llm_max_tool_rounds"],
        "anonymous_chat": policy["anonymous_chat_enabled"],
        "version": {"version": __version__},
    }


def _placeholder(counts: dict[str, int]) -> str:
    if not counts.get("papers"):
        return "Ask about the cluster's research…"
    return (
        f"Ask about {counts['papers']} papers across "
        f"{counts.get('pis', 0)} groups in the cluster…"
    )


@bp.get("/api/session")
async def session() -> dict[str, Any]:
    ctx = _ctx()
    principal = g.principal
    limit = (
        ctx.policy["anonymous_chat_daily_limit"]
        if not principal.signed_in
        else ctx.policy["user_chat_daily_limit"]
    )
    return {
        "session": g.session.id[:12] if g.session else None,
        "user": principal.display or None,
        "role": str(principal.role),
        "tier": str(principal.tier),
        "signed_in": principal.signed_in,
        "is_admin": principal.is_admin,
        "can_chat": principal.signed_in or ctx.policy["anonymous_chat_enabled"],
        "daily_limit": limit,
        "model": "",
        "connected": {},
        "tools": {"local": 0, "elab": 0, "dt": 0, "total": 0},
        "turns": 0,
        "busy": False,
        "messages": [],
    }
