import numpy as np
import pytest
from library_builder import write_library

from cra.core.library.library import Library
from cra.core.retrieval.dense import DenseIndex


@pytest.fixture
def index(tmp_path):
    return DenseIndex(Library.load(write_library(tmp_path / "lib")).embeddings)


def test_a_papers_own_vector_ranks_it_first(index, tmp_path):
    embeddings = Library.load(write_library(tmp_path / "lib")).embeddings
    hits = index.search(embeddings.vectors[1])
    assert hits[0].doi == embeddings.dois[1]
    assert hits[0].score == pytest.approx(1.0, abs=1e-6)
    assert hits[0].matched_on == "semantic"


def test_similar_excludes_the_paper_itself(index):
    hits = index.similar("10.1000/alpha")
    assert "10.1000/alpha" not in [h.doi for h in hits]
    assert len(hits) == 2


def test_similar_normalises_the_doi_and_reports_an_unknown_one(index):
    assert index.similar("10.1000/ALPHA}") is not None
    assert index.similar("10.9999/nope") is None


@pytest.mark.parametrize("limit", [1, 3, 50])
def test_the_limit_is_clamped_to_the_library(index, limit):
    assert len(index.search(np.ones(8, dtype=np.float32), limit=limit)) == min(limit, 3)


def test_a_query_of_the_wrong_width_is_refused(index):
    with pytest.raises(ValueError, match="dimensions"):
        index.search(np.ones(384, dtype=np.float32))
