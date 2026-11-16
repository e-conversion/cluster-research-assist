"""The co-authorship network, shaped for the force-directed page."""

from collections.abc import Iterable
from typing import Any

import networkx as nx

from cra.core.library.citation import short
from cra.core.library.library import Library
from cra.core.library.records import PI


def _profile(pi: PI | None) -> str:
    """The page a name on the card links to: the profile, else the group's
    website. Only a web link qualifies; anything else would be dead text."""
    if pi is None:
        return ""
    for url in (pi.profile_url, pi.website):
        if url.startswith(("https://", "http://")):
            return url
    return ""


def payload(graph: nx.Graph, pis: Iterable[PI] = ()) -> dict[str, Any]:
    degrees = dict(graph.degree())
    # the graph's nodes are keyed by the PI's id; the links live on the PI
    by_smid = {pi.smid: pi for pi in pis}
    nodes = []
    for node, data in graph.nodes(data=True):
        name = str(data.get("name", node))
        nodes.append(
            {
                "id": node,
                "name": name,
                # the surname is what fits on a node
                "label": name.split()[-1] if name.split() else name,
                "group": data.get("group", ""),
                "inst": data.get("institution", ""),
                "papers": data.get("paper_count", 0),
                "deg": degrees.get(node, 0),
                "url": _profile(by_smid.get(str(node))),
            }
        )
    links = [
        {"source": a, "target": b, "weight": data.get("weight", 1)}
        for a, b, data in graph.edges(data=True)
    ]
    return {"nodes": nodes, "links": links}


def papers(library: Library, node: str) -> list[dict[str, Any]] | None:
    """One PI's papers, newest first; None for a node with no PI behind it.

    Asked for when a card opens rather than sent with the graph, so the page
    loads with counts only. A DOI the PI lists that the library does not hold
    stays in, named by its DOI, so the list adds up to the count on the card.
    """
    pi = next((p for p in library.pis if p.smid == node), None)
    if pi is None:
        return None
    rows = []
    for doi in pi.publication_dois:
        paper = library.papers.get(doi)
        rows.append(
            {
                "doi": doi,
                "cite": short(paper) if paper else doi,
                "year": paper.year if paper else "",
            }
        )
    # years are four-digit strings, so they sort as text; an unknown one goes last
    rows.sort(key=lambda row: row["year"], reverse=True)
    return rows
