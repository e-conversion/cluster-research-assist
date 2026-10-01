"""Placing a paper that is not in the library among the ones that are."""

import numpy as np
import pytest

from cra.core.library.records import Embeddings, PublicationMap
from cra.core.retrieval.dense import DenseIndex
from cra.core.retrieval.placement import DUPLICATE_AT, place, place_known


def unit(values: list[float]) -> list[float]:
    vector = np.array(values, dtype=np.float32)
    return list(vector / np.linalg.norm(vector))


def two_clouds() -> tuple[DenseIndex, PublicationMap]:
    """Two groups, close in meaning but far apart on the map.

    The groups sit 45 degrees apart in embedding space, so a query between
    them ranks members of both highly, while their projections are ten units
    apart. That is exactly the case the radius guard has to survive.
    """
    left = [unit([1.0, 0.03 * i, 0.0]) for i in range(8)]
    right = [unit([1.0, 1.0, 0.02 * i]) for i in range(8)]
    dois = [f"10.1/l{i}" for i in range(8)] + [f"10.1/r{i}" for i in range(8)]
    vectors = np.array(left + right, dtype=np.float32)
    xs = [0.0 + 0.1 * i for i in range(8)] + [10.0 + 0.1 * i for i in range(8)]
    ys = [0.0] * 16
    stored = PublicationMap(
        dois=tuple(dois),
        x=tuple(xs),
        y=tuple(ys),
        clusters={2: ((0,) * 8 + (1,) * 8, ("left", "right"))},
    )
    return DenseIndex(Embeddings(tuple(dois), vectors, "fake")), stored


def test_a_query_between_two_clouds_stays_in_the_nearer_one():
    dense, stored = two_clouds()
    # Deliberately between the groups, so the ranking mixes both.
    placed = place(np.array(unit([1.0, 0.45, 0.0]), dtype=np.float32), dense, stored)
    assert placed is not None
    neighbour_dois = {n.doi for n in placed.neighbours}
    assert any(d.startswith("10.1/l") for d in neighbour_dois)
    assert any(d.startswith("10.1/r") for d in neighbour_dois)
    # The gap runs from x=0.8 to x=10; a centroid would land in it.
    assert not 2.0 < placed.x < 9.0


def test_an_exact_match_is_reported_as_a_duplicate():
    dense, stored = two_clouds()
    vectors = dense._embeddings.vectors
    placed = place(vectors[3], dense, stored)
    assert placed is not None
    assert placed.confidence >= DUPLICATE_AT
    assert placed.duplicate_of == "10.1/l3"
    # Offset so the marker does not hide under the paper it matches.
    assert placed.x != stored.x[3]
    assert abs(placed.x - stored.x[3]) < 1.0


def test_placement_is_none_when_no_ranked_paper_is_on_the_map():
    dense, _ = two_clouds()
    empty = PublicationMap(dois=(), x=(), y=(), clusters={})
    assert (
        place(np.array(unit([1.0, 0.0, 0.0]), dtype=np.float32), dense, empty) is None
    )


def test_only_the_papers_that_are_mapped_become_neighbours():
    """A library may embed more papers than the map covers."""
    dense, stored = two_clouds()
    partial = PublicationMap(
        dois=stored.dois[:4], x=stored.x[:4], y=stored.y[:4], clusters={}
    )
    placed = place(np.array(unit([1.0, 0.05, 0.0]), dtype=np.float32), dense, partial)
    assert placed is not None
    assert {n.doi for n in placed.neighbours} <= set(partial.dois)


def test_the_same_query_places_identically_twice():
    dense, stored = two_clouds()
    query = np.array(unit([1.0, 0.3, 0.1]), dtype=np.float32)
    first, second = place(query, dense, stored), place(query, dense, stored)
    assert (first.x, first.y) == (second.x, second.y)


def test_a_library_paper_keeps_its_stored_position():
    dense, stored = two_clouds()
    placed = place_known("10.1/r2", dense, stored)
    assert placed is not None
    assert (placed.x, placed.y) == (stored.x[10], stored.y[10])
    assert placed.confidence == 1.0
    assert "10.1/r2" not in {n.doi for n in placed.neighbours}


@pytest.mark.parametrize("doi", ["10.1/missing", "not-a-doi"])
def test_an_unknown_doi_has_no_stored_position(doi):
    dense, stored = two_clouds()
    assert place_known(doi, dense, stored) is None
