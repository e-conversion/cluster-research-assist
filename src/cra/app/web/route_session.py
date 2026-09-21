"""What the frontend asks on boot: the public configuration and, once signed
in, the caller's session."""

from quart import Blueprint, current_app, g

from cra import __version__

bp = Blueprint("session", __name__)


@bp.get("/api/config")
async def config() -> dict:
    settings = current_app.extensions["cra"].settings
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
        "placeholder": "Ask about the cluster's research…",
        "examples": [],
        "providers": {},
        "default_provider": "",
        "sources": {},
        "max_tool_rounds": settings.llm_max_tool_rounds,
        "version": {"version": __version__},
    }


@bp.get("/api/session")
async def session() -> dict:
    return {
        "session": g.session.id[:12],
        "user": g.principal.display,
        "provider": "",
        "model": "",
        "auto_model": False,
        "connected": {},
        "tools": {"local": 0, "elab": 0, "dt": 0, "total": 0},
        "turns": 0,
        "busy": False,
        "messages": [],
    }
