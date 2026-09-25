"""What the frontend asks on boot: the configuration the landing page needs,
which anyone may read, and the session, which needs a sign-in."""

from typing import Any

from quart import Blueprint, current_app, g

from cra import __version__
from cra.app.auth import tokens
from cra.app.web import examples, stored_sources
from cra.app.web.access import public
from cra.app.web.route_preferences import selection
from cra.assistant.llm import params as params_
from cra.assistant.llm.selection import available

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
            "institution": settings.oidc_enabled,
            "login_url": "auth/login",
            "logout_url": "auth/logout",
            "contact": settings.auth_admin_contact,
        },
        "notice": policy["notice"],
        "provider": settings.llm_provider,
        "openrouter": params_.is_openrouter(settings),
        "models": await available(
            settings, policy["llm_models"], ctx.catalogue, ctx.http
        ),
        "default_model": policy["llm_model"],
        "routes": [dict(r) for r in params_.ROUTES],
        "parameters": params_.payload(settings),
        "library": counts,
        "placeholder": _placeholder(counts),
        "examples": examples.some(ctx.brand.manifest.examples or examples.QUESTIONS),
        "sources": {kind: s.public() for kind, s in ctx.remote.sources.items()},
        "sources_kept": ctx.vault.enabled,
        "max_tool_rounds": policy["llm_max_tool_rounds"],
        "mcp": _mcp(settings),
        "version": {"version": __version__},
    }


def _mcp(settings) -> dict[str, Any] | None:
    """Where the outward endpoint is, relative to the page, so the browser
    resolves it against the address it actually used: behind the proxy this
    process does not know its own scheme."""
    if not settings.mcp_server_enabled:
        return None
    return {
        "path": settings.mcp_server_path.lstrip("/"),
        "token_required": settings.mcp_server_require_token,
        "token_days": {
            "default": tokens.DEFAULT_DAYS,
            "choices": list(tokens.EXPIRY_CHOICES),
        },
    }


def _placeholder(counts: dict[str, int]) -> str:
    if not counts.get("papers"):
        return "Ask about the cluster's research…"
    return (
        f"Ask about {counts['papers']} papers across "
        f"{counts.get('pis', 0)} groups in the cluster…"
    )


def _tool_counts(ctx, tier, session) -> dict[str, int]:
    local = len(ctx.registry.specs(tier))
    remote = ctx.remote.counts(session.id) if session else {}
    return {"local": local, **remote, "total": local + sum(remote.values())}


async def _conversation_view(ctx, session, principal) -> dict[str, Any]:
    """The conversation this session is in, as the interface renders it.

    Shown only when it belongs to the principal: the session may still name a
    conversation from whoever was signed in before.
    """
    conversation_id = session.data.get("conversation") if session else None
    empty: dict[str, Any] = {"conversation": None, "messages": [], "turns": 0}
    if not conversation_id:
        return empty
    conversation = await ctx.repo.get_conversation(str(conversation_id))
    if conversation is None or conversation.user_id != principal.user_id:
        return empty
    stored = await ctx.repo.messages(str(conversation_id))
    return {
        "conversation": conversation_id,
        "messages": [
            {"role": m.role, "content": m.content, "meta": m.meta} for m in stored
        ],
        "turns": sum(1 for m in stored if m.role == "user"),
    }


@bp.get("/api/session")
async def session() -> dict[str, Any]:
    ctx = _ctx()
    principal = g.principal
    await stored_sources.restore(ctx, g.session, principal.user_id)
    return {
        "session": g.session.id[:12] if g.session else None,
        "user": principal.display or None,
        "role": str(principal.role),
        "tier": str(principal.tier),
        "signed_in": principal.signed_in,
        "is_admin": principal.is_admin,
        "daily_limit": ctx.policy["user_chat_daily_limit"],
        **await selection(ctx, g.session),
        "connected": ctx.remote.status(g.session.id),
        "tools": _tool_counts(ctx, principal.tier, g.session),
        "busy": ctx.turns.of(g.session.id).busy,
        **await _conversation_view(ctx, g.session, principal),
    }
