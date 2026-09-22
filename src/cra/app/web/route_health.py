from quart import Blueprint, current_app

from cra import __version__
from cra.app.web.access import public

bp = Blueprint("health", __name__)


@bp.get("/api/health")
@public
async def health() -> dict:
    ctx = current_app.extensions["cra"]
    library = ctx.library
    return {
        "ok": True,
        "version": __version__,
        "library": library.counts if library is not None else {},
        "tools": len(ctx.registry),
    }
