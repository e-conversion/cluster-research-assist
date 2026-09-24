"""Fetching one DOI's metadata, and staying welcome while doing it."""

import httpx
import pytest
import respx

from cra.core.connectors import crossref, doi_lookup, openalex
from cra.core.connectors.crossref import MetadataError, parse_work, strip_jats
from cra.core.connectors.doi_lookup import LookupGuard
from cra.core.connectors.openalex import rebuild_abstract

CROSSREF = "https://api.crossref.org"
OPENALEX = "https://api.openalex.org"
DOI = "10.1038/s41586-021-03819-2"
WORK_URL = f"{CROSSREF}/works/10.1038%2Fs41586-021-03819-2"
ALEX_URL = f"{OPENALEX}/works/doi:{DOI}"


def message(**overrides):
    base = {
        "DOI": "10.1038/S41586-021-03819-2",
        "title": ["Highly accurate protein structure prediction"],
        "author": [{"given": "John", "family": "Jumper"}, {"family": "Evans"}],
        "container-title": ["Nature"],
        "issued": {"date-parts": [[2021, 7, 15]]},
        "abstract": "<jats:p>We present a method.</jats:p>",
    }
    return {**base, **overrides}


def crossref_response(**overrides):
    return httpx.Response(200, json={"message": message(**overrides)})


@pytest.fixture
def guard():
    return LookupGuard(cooldown_s=60.0)


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as made:
        yield made


# ---------- parsing ----------


def test_a_full_record_parses_into_every_field():
    work = parse_work(message())
    assert work.doi == "10.1038/s41586-021-03819-2"  # lowercased, as stored
    assert work.authors == ("John Jumper", "Evans")
    assert work.year == "2021"
    assert work.journal == "Nature"
    assert work.abstract == "We present a method."
    assert work.citation.startswith("Jumper et al., ")


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": []},
        {"issued": {"date-parts": [[None]]}},
        {"issued": {}},
        {"container-title": []},
        {"author": [{"name": "The LHCb Collaboration"}]},
        {"author": "not a list"},
        {"abstract": None},
    ],
    ids=[
        "no title",
        "null year",
        "no date parts",
        "no journal",
        "consortium author",
        "malformed authors",
        "no abstract",
    ],
)
def test_degenerate_records_parse_without_raising(overrides):
    parse_work(message(**overrides))


def test_the_embeddable_text_joins_title_and_abstract():
    """Byte-identical to how the library's own vectors were built."""
    assert parse_work(message()).embeddable == (
        "Highly accurate protein structure prediction. We present a method"
    )


def test_a_record_with_only_a_title_still_embeds():
    assert parse_work(message(abstract=None)).embeddable.endswith("prediction")


# ---------- JATS ----------


def test_jats_tags_are_removed():
    assert strip_jats("<jats:p>Plain words.</jats:p>") == "Plain words."


def test_the_abstract_heading_is_dropped():
    raw = "<jats:title>Abstract</jats:title><jats:p>The body.</jats:p>"
    assert strip_jats(raw) == "The body."


def test_entities_are_unescaped():
    assert strip_jats("<jats:p>Ti &amp; O</jats:p>") == "Ti & O"


def test_escaped_markup_in_the_text_survives_as_text():
    """Unescaping before stripping would eat the sentence around it."""
    assert strip_jats("<jats:p>we write &lt;p&gt; for a paragraph</jats:p>") == (
        "we write <p> for a paragraph"
    )


def test_whitespace_is_collapsed():
    assert strip_jats("<jats:p>two\n\n  words</jats:p>") == "two words"


def test_an_absent_abstract_is_empty():
    assert strip_jats("") == ""


# ---------- the inverted index ----------


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


# ---------- HTTP behaviour ----------


@respx.mock
async def test_a_known_doi_comes_back(client, guard):
    respx.get(WORK_URL).mock(return_value=crossref_response())
    work = await crossref.fetch_work(client, CROSSREF, DOI)
    assert work.journal == "Nature"


@respx.mock
async def test_an_unknown_doi_is_reported_as_not_found(client):
    respx.get(WORK_URL).mock(return_value=httpx.Response(404))
    with pytest.raises(MetadataError) as raised:
        await crossref.fetch_work(client, CROSSREF, DOI)
    assert raised.value.reason == "not_found"
    assert DOI in str(raised.value)


@respx.mock
async def test_a_throttle_is_reported_with_its_retry_delay(client):
    respx.get(WORK_URL).mock(
        return_value=httpx.Response(429, headers={"retry-after": "30"})
    )
    with pytest.raises(MetadataError) as raised:
        await crossref.fetch_work(client, CROSSREF, DOI)
    assert raised.value.reason == "rate_limited"
    assert raised.value.retry_after == 30


@respx.mock
@pytest.mark.parametrize(
    "failure", [httpx.Response(500), httpx.ConnectError("no route")]
)
async def test_an_unreachable_source_is_reported_as_unavailable(client, failure):
    route = respx.get(WORK_URL)
    if isinstance(failure, httpx.Response):
        route.mock(return_value=failure)
    else:
        route.mock(side_effect=failure)
    with pytest.raises(MetadataError) as raised:
        await crossref.fetch_work(client, CROSSREF, DOI)
    assert raised.value.reason == "unavailable"


@respx.mock
async def test_a_body_that_is_not_json_is_unavailable(client):
    respx.get(WORK_URL).mock(return_value=httpx.Response(200, text="not json"))
    with pytest.raises(MetadataError) as raised:
        await crossref.fetch_work(client, CROSSREF, DOI)
    assert raised.value.reason == "unavailable"


@respx.mock
async def test_the_contact_address_is_sent_only_when_configured(client):
    route = respx.get(WORK_URL).mock(return_value=crossref_response())
    await crossref.fetch_work(client, CROSSREF, DOI, mailto="cluster@example.org")
    assert "mailto=cluster%40example.org" in str(route.calls[0].request.url)
    await crossref.fetch_work(client, CROSSREF, DOI)
    assert "mailto" not in str(route.calls[1].request.url)


@respx.mock
async def test_openalex_answers_with_its_own_shape(client):
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
    work = await openalex.fetch_work(client, OPENALEX, DOI)
    assert work.doi == DOI
    assert work.journal == "J. Phys. Chem."
    assert work.abstract == "Ultracold reactions"


# ---------- the guard ----------


@respx.mock
async def test_openalex_supplies_an_abstract_crossref_lacks(client, guard):
    respx.get(WORK_URL).mock(return_value=crossref_response(abstract=None))
    respx.get(ALEX_URL).mock(
        return_value=httpx.Response(
            200, json={"abstract_inverted_index": {"Filled": [0], "in": [1]}}
        )
    )
    work = await doi_lookup.fetch(
        client, DOI, guard=guard, crossref_url=CROSSREF, openalex_url=OPENALEX
    )
    assert work.abstract == "Filled in"
    # Crossref stated these, so the second source must not overwrite them.
    assert work.journal == "Nature"
    assert work.title.startswith("Highly accurate")


@respx.mock
async def test_openalex_is_not_asked_when_crossref_had_an_abstract(client, guard):
    respx.get(WORK_URL).mock(return_value=crossref_response())
    alex = respx.get(ALEX_URL)
    await doi_lookup.fetch(
        client, DOI, guard=guard, crossref_url=CROSSREF, openalex_url=OPENALEX
    )
    assert not alex.called


@respx.mock
async def test_openalex_stands_in_when_crossref_has_no_record(client, guard):
    respx.get(WORK_URL).mock(return_value=httpx.Response(404))
    respx.get(ALEX_URL).mock(
        return_value=httpx.Response(200, json={"display_name": "Only here"})
    )
    work = await doi_lookup.fetch(
        client, DOI, guard=guard, crossref_url=CROSSREF, openalex_url=OPENALEX
    )
    assert work.title == "Only here"


@respx.mock
async def test_an_answer_is_reused_instead_of_asked_again(client, guard):
    route = respx.get(WORK_URL).mock(return_value=crossref_response())
    for _ in range(3):
        await doi_lookup.fetch(client, DOI, guard=guard, crossref_url=CROSSREF)
    assert route.call_count == 1


@respx.mock
async def test_a_missing_doi_is_remembered_so_retyping_costs_nothing(client, guard):
    route = respx.get(WORK_URL).mock(return_value=httpx.Response(404))
    for _ in range(3):
        with pytest.raises(MetadataError):
            await doi_lookup.fetch(client, DOI, guard=guard, crossref_url=CROSSREF)
    assert route.call_count == 1


@respx.mock
async def test_a_throttling_source_is_set_aside_rather_than_retried(client, guard):
    route = respx.get(WORK_URL).mock(return_value=httpx.Response(429))
    with pytest.raises(MetadataError):
        await doi_lookup.fetch(client, DOI, guard=guard, crossref_url=CROSSREF)
    with pytest.raises(MetadataError) as raised:
        await doi_lookup.fetch(client, "10.1/other", guard=guard, crossref_url=CROSSREF)
    assert route.call_count == 1
    assert raised.value.reason == "rate_limited"


@respx.mock
async def test_nothing_is_sent_while_every_source_is_cooling(client, guard):
    guard.cool_down("crossref")
    guard.cool_down("openalex")
    crossref_route, alex_route = respx.get(WORK_URL), respx.get(ALEX_URL)
    with pytest.raises(MetadataError) as raised:
        await doi_lookup.fetch(
            client, DOI, guard=guard, crossref_url=CROSSREF, openalex_url=OPENALEX
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
