"""The short citation shown wherever a paper is named."""

import pytest

from cra.core.library.citation import compose, short, surname, surnames
from cra.core.library.records import Paper


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Ada Lovelace", "Lovelace"),
        ("Lovelace, Ada", "Lovelace"),
        ("Lovelace,Ada", "Lovelace"),
        ("Johannes van der Waals", "van der Waals"),
        ("Martin Luther King Jr.", "King"),
        ("Madonna", "Madonna"),
        ("   ", ""),
    ],
)
def test_surname_reads_either_written_order(name, expected):
    assert surname(name) == expected


def test_one_author_is_named_without_et_al():
    assert compose(["Ada Lovelace"], "A note").startswith("Lovelace, ")


def test_several_authors_collapse_to_et_al():
    citation = compose(["Ada Lovelace", "Grace Hopper", "Emmy Noether"], "A note")
    assert citation.startswith("Lovelace et al., ")
    assert "Hopper" not in citation


def test_every_part_appears_when_every_part_is_known():
    assert (
        compose(["Ada Lovelace"], "On the engine", "Nature", "1843")
        == "Lovelace, On the engine, Nature, 1843"
    )


@pytest.mark.parametrize(
    ("journal", "year", "expected"),
    [
        ("", "1843", "Lovelace, On the engine, 1843"),
        ("Nature", "", "Lovelace, On the engine, Nature"),
        ("", "", "Lovelace, On the engine"),
    ],
)
def test_a_missing_part_leaves_no_empty_slot(journal, year, expected):
    assert compose(["Ada Lovelace"], "On the engine", journal, year) == expected


def test_a_long_title_is_cut_at_a_word_boundary():
    title = "Directional message passing for molecular graphs and other things"
    citation = compose([], title, title_chars=30)
    assert citation.endswith("…")
    assert "…" not in citation[:-1]
    # Cut on a space, so no word is left half-written.
    assert title.startswith(citation[:-1])
    assert len(citation) <= 31


def test_a_title_inside_the_budget_is_left_alone():
    assert compose([], "Short title") == "Short title"


def test_an_empty_record_falls_back_to_the_doi():
    assert compose([], "", "", "", fallback="10.1/x") == "10.1/x"


def test_surnames_are_bounded_for_search():
    authors = [f"Given Family{i}" for i in range(20)]
    assert surnames(authors, limit=3) == "Family0 Family1 Family2"


def test_short_reads_a_library_record():
    paper = Paper(
        doi="10.1/x",
        title="On the engine",
        authors=("Ada Lovelace", "Charles Babbage"),
        year="1843",
        journal="Nature",
    )
    assert short(paper) == "Lovelace et al., On the engine, Nature, 1843"


def test_short_falls_back_to_the_doi_for_an_empty_record():
    assert short(Paper(doi="10.1/x", title="", authors=(), year="")) == "10.1/x"
