"""OpenAlex: rebuilding an abstract and fetching a work."""

import httpx
import pytest
import respx

from cra.core.connectors import openalex
from cra.core.connectors.openalex import rebuild_abstract

OPENALEX = "https://api.openalex.org"


DOI = "10.1038/s41586-021-03819-2"


ALEX_URL = f"{OPENALEX}/works/doi:{DOI}"


def test_an_inverted_index_is_rebuilt_in_order():
    assert rebuild_abstract(
        {"Ultracold": [0], "chemical": [1, 3], "reactions": [2]}
    ) == ("Ultracold chemical reactions chemical")


@pytest.mark.parametrize(
    "inverted",
    [None, {}, "not a dict", {"word": "not a list"}, {"word": [10**9]}],
    ids=["null", "empty", "wrong type", "bad positions", "absurd position"],
)
def test_a_hostile_or_broken_index_yields_nothing(inverted):
    assert rebuild_abstract(inverted) == ""


@respx.mock
async def test_openalex_answers_with_its_own_shape(http):
    respx.get(ALEX_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "doi": f"https://doi.org/{DOI}",
                "display_name": "A title",
                "publication_year": 2023,
                "authorships": [{"author": {"display_name": "Masato Morita"}}],
                "primary_location": {"source": {"display_name": "J. Phys. Chem."}},
                "abstract_inverted_index": {"Ultracold": [0], "reactions": [1]},
            },
        )
    )
    work = await openalex.fetch_work(http, OPENALEX, DOI)
    assert work.doi == DOI
    assert work.journal == "J. Phys. Chem."
    assert work.abstract == "Ultracold reactions"
