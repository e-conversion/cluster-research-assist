"""Repairs for quirks of the upstream scrapers, kept apart from parsing.

Each function takes data as it arrives in a bundle and returns it clean. The
loader still applies them on every start; once the bundle is built by cra,
the build applies them once and the loader reads clean files.
"""

from collections.abc import Iterable
from typing import Any

import networkx as nx

from cra.core.library.records import normalise_doi


def unique_dois(raw: Iterable[str]) -> tuple[str, ...]:
    """Normalised and deduplicated, first occurrence first. The scraper writes
    some DOIs twice, once with a stray brace."""
    return tuple(dict.fromkeys(normalise_doi(doi) for doi in raw))


def authors(scraped: tuple[str, ...], enrichment: dict[str, Any]) -> tuple[str, ...]:
    """OpenAlex author names are clean UTF-8 where the scraped CSV truncates
    them, so they win whenever there are any."""
    return tuple(enrichment.get("authors") or ()) or scraped


def graph_edges(graph: nx.Graph) -> nx.Graph:
    """Shared DOIs deduplicated and weights recounted, in place. The builder
    that scraped the shared DOIs left a stray brace on some, so one paper
    appears twice on an edge and the weight counts it twice."""
    for _, _, data in graph.edges(data=True):
        if "shared_dois" in data:
            unique = unique_dois(data["shared_dois"])
            data["shared_dois"] = list(unique)
            data["weight"] = len(unique)
    return graph
