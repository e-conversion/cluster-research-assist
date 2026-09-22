from quart import Blueprint, current_app

from cra import __version__

bp = Blueprint("health", __name__)


@bp.get("/api/health")
async def health() -> dict:
    corpus = current_app.extensions["cra"].corpus
    return {
        "ok": True,
        "version": __version__,
        "corpus": corpus.counts if corpus is not None else {},
    }
