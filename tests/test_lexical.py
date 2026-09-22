import pytest
from library_builder import write_library

from cra.core.library.library import Library
from cra.core.retrieval.lexical import LexicalIndex


@pytest.fixture
def index(tmp_path):
    return LexicalIndex.build(Library.load(write_library(tmp_path / "lib")).papers)


def dois(hits):
    return [h.doi for h in hits]


def test_a_title_word_finds_its_paper(index):
    hits = index.search("perovskite")
    assert dois(hits)[0] == "10.1000/alpha"
    assert hits[0].matched_on == "title"


def test_a_word_only_in_an_abstract_still_finds_the_paper(index):
    hits = index.search("methanol")
    assert dois(hits) == ["10.1000/beta"]
    assert hits[0].matched_on == "abstract"


def test_the_stronger_field_decides_and_is_reported(index):
    # "electrocatalysis" is in beta's title and its abstract
    assert {h.matched_on for h in index.search("electrocatalysis")} == {"title"}
    assert {h.matched_on for h in index.search("illumination")} == {"abstract"}


def test_papers_without_an_abstract_do_not_break_the_index(tmp_path):
    index = LexicalIndex.build(
        Library.load(write_library(tmp_path / "lib", abstracts=False)).papers
    )
    assert len(index) == 3
    assert dois(index.search("battery")) == ["10.1000/gamma"]


@pytest.mark.parametrize("query", ["", "   ", "aardvark"])
def test_a_query_that_matches_nothing_returns_nothing(index, query):
    assert index.search(query) == []


def test_the_limit_is_respected(index):
    assert len(index.search("perovskite copper battery", limit=2)) <= 2
