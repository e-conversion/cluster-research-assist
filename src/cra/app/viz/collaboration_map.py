"""The co-authorship network, shaped for the force-directed page."""

from typing import Any

import networkx as nx


def payload(graph: nx.Graph) -> dict[str, Any]:
    degrees = dict(graph.degree())
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
            }
        )
    links = [
        {"source": a, "target": b, "weight": data.get("weight", 1)}
        for a, b, data in graph.edges(data=True)
    ]
    return {"nodes": nodes, "links": links}
