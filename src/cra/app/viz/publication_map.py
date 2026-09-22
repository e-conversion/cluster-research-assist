"""The publication map, served from what the library already holds.

Nothing is computed here: the projection and every clustering were made when
the library was built, which is why this answers in milliseconds and the
serving image needs no numerical stack.
"""

from typing import Any

from cra.app.viz.palette import colour
from cra.core.library.library import Library


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
    points = []
    for index, doi in enumerate(stored.dois):
        paper = library.papers.get(doi)
        points.append(
            {
                "doi": doi,
                "title": paper.title if paper else doi,
                "year": paper.year if paper else "",
                "x": stored.x[index],
                # deck.gl's orthographic view has y pointing down
                "y": -stored.y[index],
                "cluster": named[index],
                "color": colours[named[index]],
            }
        )
    return {
        "available": True,
        "clusters": count,
        "available_counts": list(stored.available_counts),
        "points": points,
        "legend": [{"cluster": name, "color": rgb} for name, rgb in colours.items()],
    }
