"""The two pictures of the library: where the papers sit, and who works with whom."""

from typing import Any

from quart import Blueprint, current_app, request

from cra.app.viz import collaboration_map, publication_map

bp = Blueprint("views", __name__)

MIN_CLUSTERS, MAX_CLUSTERS, DEFAULT_CLUSTERS = 2, 20, 8


@bp.get("/api/publication-map")
async def map_of_publications() -> Any:
    ctx = current_app.extensions["cra"]
    if ctx.library is None:
        return {"available": False, "hint": "No library is loaded."}
    try:
        wanted = int(request.args.get("clusters", DEFAULT_CLUSTERS))
    except ValueError:
        wanted = DEFAULT_CLUSTERS
    return publication_map.payload(
        ctx.library, max(MIN_CLUSTERS, min(wanted, MAX_CLUSTERS))
    )


@bp.get("/api/collaboration-graph")
async def collaboration_graph() -> Any:
    ctx = current_app.extensions["cra"]
    if ctx.library is None or ctx.library.graph is None:
        return {
            "error": "This library has no collaboration graph.",
            "hint": "It is built from the principal investigators' publications.",
        }, 404
    return collaboration_map.payload(ctx.library.graph)
