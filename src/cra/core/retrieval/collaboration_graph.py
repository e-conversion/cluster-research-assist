"""Questions about the co-authorship graph that search cannot answer.

The two whole-graph metrics are computed once, when the object is built, rather
than per call as the prototype did. On 42 principal investigators that is two
milliseconds either way, but it is per call that the cost grows with the
cluster.
"""

from dataclasses import dataclass
from typing import Any

import networkx as nx


@dataclass(frozen=True)
class Person:
    name: str
    group: str
    institution: str


class CollaborationGraph:
    def __init__(self, graph: nx.Graph) -> None:
        self._graph = graph
        self._betweenness = nx.betweenness_centrality(graph)
        self._communities = [
            sorted(self._name(n) for n in community)
            for community in nx.community.greedy_modularity_communities(graph)
            if len(community) > 1
        ]
        self._communities.sort(key=lambda members: -len(members))
        self._singletons = sum(
            1
            for community in nx.community.greedy_modularity_communities(graph)
            if len(community) == 1
        )

    def _name(self, node: str) -> str:
        return str(self._graph.nodes[node]["name"])

    def person(self, node: str) -> Person:
        data = self._graph.nodes[node]
        return Person(
            name=data["name"],
            group=data.get("group", ""),
            institution=data.get("institution", ""),
        )

    def resolve(self, query: str) -> str | None:
        """A free-text name to a node. Where several match, the one who has
        published most is meant."""
        if query in self._graph:
            return query
        wanted = query.strip().lower()
        if not wanted:
            return None
        matches = [
            node
            for node, data in self._graph.nodes(data=True)
            if wanted in data["name"].lower() or wanted in data.get("group", "").lower()
        ]
        if not matches:
            return None
        return max(matches, key=lambda n: self._graph.nodes[n].get("paper_count", 0))

    def collaborators(self, node: str) -> list[dict[str, Any]]:
        found = [
            {
                **vars(self.person(other)),
                "shared_papers": self._graph.edges[node, other]["weight"],
            }
            for other in self._graph.neighbors(node)
        ]
        found.sort(key=lambda c: -c["shared_papers"])
        return found

    def joint_papers(self, first: str, second: str) -> tuple[int, list[str]]:
        if not self._graph.has_edge(first, second):
            return 0, []
        edge = self._graph.edges[first, second]
        return int(edge["weight"]), list(edge.get("shared_dois", ()))

    def centrality(self, limit: int = 10) -> list[dict[str, Any]]:
        """Who bridges otherwise weakly connected groups.

        Unweighted betweenness: a high score means many shortest paths run
        through this person, not that they publish a lot.
        """
        ranked = sorted(self._betweenness.items(), key=lambda kv: -kv[1])[:limit]
        return [
            {**vars(self.person(node)), "betweenness": round(score, 4)}
            for node, score in ranked
        ]

    def communities(self) -> dict[str, Any]:
        """Clusters that collaborate internally. People with no shared paper at
        all are counted rather than listed."""
        return {
            "community_count": len(self._communities),
            "unconnected_pis": self._singletons,
            "communities": [
                {"size": len(members), "members": members}
                for members in self._communities
            ],
        }
