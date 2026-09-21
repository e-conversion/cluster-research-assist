from quart import Blueprint

from cra import __version__

bp = Blueprint("health", __name__)


@bp.get("/api/health")
async def health() -> dict:
    return {"ok": True, "version": __version__}
