"""Fetching one DOI's metadata, and staying welcome while doing it."""

import httpx
import pytest
import respx
from fakes import crossref_response

from cra.core.connectors import crossref, doi_lookup
from cra.core.connectors.crossref import MetadataError
from cra.core.connectors.doi_lookup import LookupGuard

CROSSREF = "https://api.crossref.org"


OPENALEX = "https://api.openalex.org"


DOI = "10.1038/s41586-021-03819-2"


WORK_URL = f"{CROSSREF}/works/10.1038%2Fs41586-021-03819-2"


ALEX_URL = f"{OPENALEX}/works/doi:{DOI}"


@pytest.fixture
def guard():
    return LookupGuard(cooldown_s=60.0)


@respx.mock
async def test_openalex_supplies_an_abstract_crossref_lacks(http, guard):
    respx.get(WORK_URL).mock(return_value=crossref_response(abstract=None))
    respx.get(ALEX_URL).mock(
        return_value=httpx.Response(
            200, json={"abstract_inverted_index": {"Filled": [0], "in": [1]}}
        )
    )
    work = await doi_lookup.fetch(
        http, DOI, guard=guard, crossref_url=CROSSREF, openalex_url=OPENALEX
    )
    assert work.abstract == "Filled in"
    # Crossref stated these, so the second source must not overwrite them.
    assert work.journal == "Nature"
    assert work.title.startswith("Highly accurate")


@respx.mock
async def test_openalex_is_not_asked_when_crossref_had_an_abstract(http, guard):
    respx.get(WORK_URL).mock(return_value=crossref_response())
    alex = respx.get(ALEX_URL)
    await doi_lookup.fetch(
        http, DOI, guard=guard, crossref_url=CROSSREF, openalex_url=OPENALEX
    )
    assert not alex.called


@respx.mock
async def test_openalex_stands_in_when_crossref_has_no_record(http, guard):
    respx.get(WORK_URL).mock(return_value=httpx.Response(404))
    respx.get(ALEX_URL).mock(
        return_value=httpx.Response(200, json={"display_name": "Only here"})
    )
    work = await doi_lookup.fetch(
        http, DOI, guard=guard, crossref_url=CROSSREF, openalex_url=OPENALEX
    )
    assert work.title == "Only here"


@respx.mock
async def test_an_answer_is_reused_instead_of_asked_again(http, guard):
    route = respx.get(WORK_URL).mock(return_value=crossref_response())
    for _ in range(3):
        await doi_lookup.fetch(http, DOI, guard=guard, crossref_url=CROSSREF)
    assert route.call_count == 1


@respx.mock
async def test_a_missing_doi_is_remembered_so_retyping_costs_nothing(http, guard):
    route = respx.get(WORK_URL).mock(return_value=httpx.Response(404))
    for _ in range(3):
        with pytest.raises(MetadataError):
            await doi_lookup.fetch(http, DOI, guard=guard, crossref_url=CROSSREF)
    assert route.call_count == 1


@respx.mock
async def test_a_throttling_source_is_set_aside_rather_than_retried(http, guard):
    route = respx.get(WORK_URL).mock(return_value=httpx.Response(429))
    with pytest.raises(MetadataError):
        await doi_lookup.fetch(http, DOI, guard=guard, crossref_url=CROSSREF)
    with pytest.raises(MetadataError) as raised:
        await doi_lookup.fetch(http, "10.1/other", guard=guard, crossref_url=CROSSREF)
    assert route.call_count == 1
    assert raised.value.reason == "rate_limited"


@respx.mock
async def test_nothing_is_sent_while_every_source_is_cooling(http, guard):
    guard.cool_down("crossref")
    guard.cool_down("openalex")
    crossref_route, alex_route = respx.get(WORK_URL), respx.get(ALEX_URL)
    with pytest.raises(MetadataError) as raised:
        await doi_lookup.fetch(
            http, DOI, guard=guard, crossref_url=CROSSREF, openalex_url=OPENALEX
        )
    assert raised.value.reason == "rate_limited"
    assert not crossref_route.called
    assert not alex_route.called


async def test_a_cooldown_lifts_once_its_time_has_passed():
    now = [0.0]
    guard = LookupGuard(clock=lambda: now[0], cooldown_s=60.0)
    guard.cool_down("crossref")
    assert not guard.available("crossref")
    now[0] = 61.0
    assert guard.available("crossref")


async def test_the_cache_does_not_grow_without_bound():
    now = [0.0]
    guard = LookupGuard(clock=lambda: now[0])
    for i in range(doi_lookup.CACHE_ENTRIES + 50):
        guard.remember(f"10.1/{i}", crossref.Work(doi=f"10.1/{i}"))
    assert len(guard._cache) <= doi_lookup.CACHE_ENTRIES
