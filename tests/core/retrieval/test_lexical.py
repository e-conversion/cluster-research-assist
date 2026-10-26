import pytest
from library_builder import write_library

from cra.core.library.library import Library
from cra.core.retrieval.lexical import BM25, LexicalIndex, stem, tokenize


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


def test_a_word_in_most_documents_still_counts_a_little():
    """Its idf is negative; the floor keeps it positive but below a rare word."""
    scores = BM25([["the", "cell"], ["the", "anode"], ["the", "film"], ["film"]])
    common, rare = scores.scores(["the"]), scores.scores(["anode"])
    assert 0 < common[1] < rare[1]
    assert common[3] == 0


def test_the_same_count_weighs_more_in_a_shorter_document():
    corpus = [
        ["anode"],
        ["anode", "film", "cell", "oxide"],
        ["film"],
        ["cell"],
        ["oxide"],
    ]
    scores = BM25(corpus).scores(["anode"])
    assert scores[0] > scores[1] > scores[2] == 0


def test_an_empty_corpus_scores_nothing():
    assert len(BM25([]).scores(["anode"])) == 0


@pytest.mark.parametrize(
    ("written", "typed", "shared"),
    [
        ("Li–O₂ batteries", "Li-O2 battery", "o2"),  # dash, subscript, plural
        ("LiO2 cathodes", "Li-O2", "lio2"),  # the compound written together
        ("α-Fe₂O₃ photoanodes", "alpha-Fe2O3", "fe2o3"),  # Greek typed as its name
        ("Müller's catalysts", "muller catalyst", "catalyst"),
    ],
)
def test_the_ways_a_term_is_written_meet(written, typed, shared):
    assert shared in set(tokenize(typed)) & set(tokenize(written))
    # in a corpus of two, a term in one document has an idf of exactly zero
    others = ["an unrelated film", "copper surfaces", "graph networks"]
    corpus = [tokenize(written), *map(tokenize, others)]
    scores = BM25(corpus).scores(tokenize(typed))
    assert scores[0] > max(scores[1:]) == 0


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("catalysts", "catalyst"),
        ("batteries", "battery"),
        ("electrodes", "electrode"),
        ("glass", "glass"),
        ("gas", "gas"),
        ("co2", "co2"),
    ],
)
def test_only_plural_endings_are_stemmed(word, expected):
    assert stem(word) == expected


def test_a_paper_holding_the_phrase_outranks_one_holding_the_words():
    phrase = tokenize("solar cells for hydrogen")
    words = tokenize("cells in the solar hydrogen")
    other = tokenize("an unrelated film")
    scores = BM25([words, phrase, other]).scores(tokenize("solar cells"))
    assert scores[1] > scores[0] > scores[2]
