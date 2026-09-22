"""What the frontend asks on boot: the configuration the landing page needs,
which anyone may read, and the session, which needs a sign-in."""

from typing import Any

from quart import Blueprint, current_app, g

from cra import __version__
from cra.app.web.access import public
from cra.app.web.route_preferences import selection
from cra.assistant.llm import params as params_
from cra.assistant.llm.selection import offered

bp = Blueprint("session", __name__)


def _ctx():
    return current_app.extensions["cra"]


@bp.get("/api/config")
@public
async def config() -> dict[str, Any]:
    ctx = _ctx()
    settings, policy = ctx.settings, ctx.policy
    counts = ctx.library.counts if ctx.library else {}
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
        "provider": settings.llm_provider,
        "openrouter": params_.is_openrouter(settings),
        "models": offered(settings, policy["llm_models"]),
        "default_model": policy["llm_model"],
        "routes": [dict(r) for r in params_.ROUTES],
        "parameters": params_.payload(settings),
        "library": counts,
        "placeholder": _placeholder(counts),
        "examples": [],
        "sources": {},
        "max_tool_rounds": policy["llm_max_tool_rounds"],
        "version": {"version": __version__},
    }


def _placeholder(counts: dict[str, int]) -> str:
    if not counts.get("papers"):
        return "Ask about the cluster's research…"
    return (
        f"Ask about {counts['papers']} papers across "
        f"{counts.get('pis', 0)} groups in the cluster…"
    )


def _tool_counts(ctx, tier) -> dict[str, int]:
    local = len(ctx.registry.specs(tier))
    return {"local": local, "elab": 0, "dt": 0, "total": local}


@bp.get("/api/session")
async def session() -> dict[str, Any]:
    ctx = _ctx()
    principal = g.principal
    return {
        "session": g.session.id[:12] if g.session else None,
        "user": principal.display or None,
        "role": str(principal.role),
        "tier": str(principal.tier),
        "signed_in": principal.signed_in,
        "is_admin": principal.is_admin,
        "daily_limit": ctx.policy["user_chat_daily_limit"],
        **await selection(ctx, g.session),
        "connected": {},
        "tools": _tool_counts(ctx, principal.tier),
        "turns": 0,
        "busy": False,
        "messages": [],
    }
