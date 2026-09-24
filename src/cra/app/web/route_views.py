"""The two pictures of the library: where the papers sit, and who works with whom.

The map also answers the question from the other direction: given a DOI the
cluster may not have, where would that paper fall among the ones it does?
"""

from typing import Any

from quart import Blueprint, current_app, g, request

from cra.app.viz import collaboration_map, publication_map
from cra.core.retrieval.locate import LocateError, locate

bp = Blueprint("views", __name__)

MIN_CLUSTERS, MAX_CLUSTERS, DEFAULT_CLUSTERS = 2, 20, 8
NEIGHBOURS_SHOWN = 8

# What a caller is told, and what the failure means for them. A limit we
# impose and a limit imposed on us read alike but call for different patience,
# so they are kept apart.
STATUS = {
    "invalid_doi": 400,
    "not_found": 404,
    "no_text": 422,
    "unplaceable": 422,
    "not_in_library": 404,
    "unavailable": 502,
    "rate_limited": 503,
    "semantic_unavailable": 503,
    "map_unavailable": 503,
}


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


@bp.post("/api/publication-map/lookup")
async def locate_publication() -> Any:
    ctx = current_app.extensions["cra"]
    if ctx.indexes is None:
        return {"error": "No library is loaded."}, 503

    body = await request.get_json(silent=True) or {}
    doi = str(body.get("doi") or "")

    # The allowance is charged only once we know we will go outside: a
    # malformed DOI or a paper the library already holds costs nothing.
    limit = ctx.policy["user_lookup_daily_limit"]
    charged = False

    async def attempt(allow_network: bool) -> Any:
        return await locate(
            doi,
            indexes=ctx.indexes,
            http=ctx.http,
            guard=ctx.lookups,
            settings=ctx.settings,
            allow_network=allow_network,
        )

    try:
        try:
            found = await attempt(allow_network=False)
        except LocateError as local:
            if local.reason != "not_in_library":
                raise
            allowance = ctx.limiter.check(f"doi-lookup:{g.principal.user_id}", limit)
            if not allowance.allowed:
                return {
                    "error": f"You have reached today's limit of {limit} DOI lookups.",
                    "retry_after": allowance.retry_after,
                }, 429
            charged = True
            found = await attempt(allow_network=True)
    except LocateError as error:
        body = {"error": str(error), "reason": error.reason}
        if error.retry_after:
            body["retry_after"] = error.retry_after
        return body, STATUS.get(error.reason, 502)

    placement, work = found.placement, found.work
    papers = ctx.library.papers
    neighbours = [
        publication_map.point_view(
            n.doi, n.x, n.y, papers.get(n.doi), score=round(n.score, 4)
        )
        for n in placement.neighbours[:NEIGHBOURS_SHOWN]
    ]
    point = publication_map.point_view(
        found.doi,
        placement.x,
        placement.y,
        found.paper,
        placed=True,
        cite=found.citation,
    )
    if not found.in_library:
        point["title"] = work.title or found.doi
        point["year"] = work.year
    return {
        "in_library": found.in_library,
        "source": work.source,
        "point": point,
        "authors": list(work.authors),
        "has_abstract": found.has_abstract,
        "confidence": round(placement.confidence, 4),
        "used": placement.used,
        "duplicate_of": placement.duplicate_of,
        "neighbours": neighbours,
        "charged": charged,
    }


@bp.get("/api/collaboration-graph")
async def collaboration_graph() -> Any:
    ctx = current_app.extensions["cra"]
    if ctx.library is None or ctx.library.graph is None:
        return {
            "error": "This library has no collaboration graph.",
            "hint": "It is built from the principal investigators' publications.",
        }, 404
    return collaboration_map.payload(ctx.library.graph)
