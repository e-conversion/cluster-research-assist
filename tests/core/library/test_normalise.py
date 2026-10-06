import networkx as nx

from cra.core.library import normalise


def test_unique_dois_drop_the_brace_twin_and_keep_order():
    raw = ["10.1000/BETA", "10.1000/alpha}", "10.1000/beta}", "10.1000/alpha"]
    assert normalise.unique_dois(raw) == ("10.1000/beta", "10.1000/alpha")


def test_enrichment_authors_win_over_the_scraped_ones():
    scraped = ("Gr Hopper",)
    assert normalise.authors(scraped, {"authors": ["Grace Hopper"]}) == (
        "Grace Hopper",
    )


def test_scraped_authors_stay_without_enrichment():
    scraped = ("Ada Lovelace",)
    assert normalise.authors(scraped, {}) == scraped
    assert normalise.authors(scraped, {"authors": []}) == scraped


def test_graph_edges_count_each_shared_paper_once():
    graph = nx.Graph()
    graph.add_edge(
        "1", "2", weight=4, shared_dois=["10.1000/a", "10.1000/a}", "10.1000/B", "b"]
    )
    graph.add_edge("2", "3", weight=1)
    normalise.graph_edges(graph)
    assert graph.edges["1", "2"]["shared_dois"] == ["10.1000/a", "10.1000/b", "b"]
    assert graph.edges["1", "2"]["weight"] == 3
    assert graph.edges["2", "3"] == {"weight": 1}
