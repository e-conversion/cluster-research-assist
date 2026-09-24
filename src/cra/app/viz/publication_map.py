"""The publication map, served from what the library already holds.

Nothing is computed here: the projection and every clustering were made when
the library was built, which is why this answers in milliseconds and the
serving image needs no numerical stack.
"""

from typing import Any

from cra.app.viz.palette import colour
from cra.core.library.citation import short, surnames
from cra.core.library.library import Library
from cra.core.library.records import Paper


def point_view(
    doi: str, x: float, y: float, paper: Paper | None = None, **extra: Any
) -> dict[str, Any]:
    """One paper as the browser sees it.

    The single place the projection's y is flipped: deck.gl's orthographic
    view has y pointing down, while the library stores it pointing up. Every
    coordinate the frontend receives passes through here, so a placed paper
    and the cloud it sits in can never end up mirrored relative to each other.
    """
    view: dict[str, Any] = {
        "doi": doi,
        "title": paper.title if paper else doi,
        "year": paper.year if paper else "",
        # `cite` carries author, title, journal and year in one bounded
        # string, and `au` the surnames the client search matches on. Sending
        # the raw author list instead would be unbounded: a paper with several
        # hundred authors is ordinary in this field.
        "cite": short(paper) if paper else doi,
        "au": surnames(paper.authors) if paper else "",
        "x": x,
        "y": -y,
    }
    view.update(extra)
    return view


def payload(library: Library, clusters: int) -> dict[str, Any]:
    stored = library.map
    if stored is None:
        return {
            "available": False,
            "hint": "This library has no publication map. Build one with `cra library build`.",
        }
    count = stored.nearest_count(clusters)
    assignments, labels = stored.clusters[count]
    named = [labels[a] if a < len(labels) else f"cluster {a + 1}" for a in assignments]
    colours = {
        name: colour(i) for i, name in enumerate(dict.fromkeys(sorted(set(named))))
    }
    points = [
        point_view(
            doi,
            stored.x[index],
            stored.y[index],
            library.papers.get(doi),
            cluster=named[index],
            color=colours[named[index]],
        )
        for index, doi in enumerate(stored.dois)
    ]
    return {
        "available": True,
        "clusters": count,
        "available_counts": list(stored.available_counts),
        "points": points,
        "legend": [{"cluster": name, "color": rgb} for name, rgb in colours.items()],
    }
