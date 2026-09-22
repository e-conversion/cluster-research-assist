from quart import Blueprint, current_app

from cra import __version__

bp = Blueprint("health", __name__)


@bp.get("/api/health")
async def health() -> dict:
    library = current_app.extensions["cra"].library
    return {
        "ok": True,
        "version": __version__,
        "library": library.counts if library is not None else {},
    }
