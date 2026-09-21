import pytest

from cra.core.corpus.text import fold, overlap, query_tokens, words


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Müller-Buschbaum", "muller-buschbaum"), ("Cortés", "cortes"), ("", "")],
)
def test_fold_strips_accents(raw, expected):
    assert fold(raw) == expected


def test_query_tokens_drop_short_words_and_fillers():
    assert query_tokens("The energy of a Koblmüller group and the CO2") == [
        "energy",
        "koblmuller",
        "group",
        "co2",
    ]


def test_overlap_matches_whole_words_only():
    assert words("Müller-Buschbaum group") == {"muller", "buschbaum", "group"}
    assert overlap("Koblmüller group", ["muller", "group"]) == 1
