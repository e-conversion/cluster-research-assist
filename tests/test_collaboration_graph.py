import pytest
from library_builder import write_library

from cra.core.library.library import Library
from cra.core.retrieval.collaboration_graph import CollaborationGraph


@pytest.fixture
def graph(tmp_path):
    return CollaborationGraph(Library.load(write_library(tmp_path / "lib")).graph)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("hopper", "Prof. Dr. Grace Hopper"),
        ("Noether Group", "Dr. Emmy Noether"),
        ("2", "Prof. Dr. Grace Hopper"),
    ],
    ids=["surname", "group", "node id"],
)
def test_a_person_is_found_by_name_group_or_id(graph, query, expected):
    assert graph.person(graph.resolve(query)).name == expected


@pytest.mark.parametrize("query", ["", "  ", "nobody"])
def test_an_unknown_name_resolves_to_nothing(graph, query):
    assert graph.resolve(query) is None


def test_collaborators_come_back_most_shared_first(graph):
    found = graph.collaborators(graph.resolve("hopper"))
    assert [(c["name"], c["shared_papers"]) for c in found] == [
        ("Dr. Ada Lovelace", 2),
        ("Dr. Emmy Noether", 1),
    ]


def test_joint_papers_of_two_people(graph):
    count, dois = graph.joint_papers(graph.resolve("lovelace"), graph.resolve("hopper"))
    assert (count, dois) == (2, ["10.1000/alpha", "10.1000/beta"])


def test_two_people_who_never_published_together(graph):
    assert graph.joint_papers(graph.resolve("lovelace"), graph.resolve("noether")) == (
        0,
        [],
    )


def test_centrality_ranks_the_bridge_first(graph):
    ranked = graph.centrality()
    # Hopper is the only path between Lovelace and Noether
    assert ranked[0]["name"] == "Prof. Dr. Grace Hopper"
    assert ranked[0]["betweenness"] > 0
    assert [r["betweenness"] for r in ranked[1:]] == [0.0, 0.0]
    assert len(graph.centrality(limit=1)) == 1


def test_communities_list_groups_and_count_the_unconnected(graph):
    found = graph.communities()
    assert found["community_count"] == 1
    assert found["unconnected_pis"] == 0
    assert found["communities"][0]["size"] == 3
