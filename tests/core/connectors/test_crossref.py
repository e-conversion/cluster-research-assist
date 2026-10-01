"""Crossref: parsing a work record and fetching one."""

import httpx
import pytest
import respx
from fakes import crossref_message, crossref_response

from cra.core.connectors import crossref
from cra.core.connectors.crossref import MetadataError, parse_work, strip_jats

CROSSREF = "https://api.crossref.org"


DOI = "10.1038/s41586-021-03819-2"


WORK_URL = f"{CROSSREF}/works/10.1038%2Fs41586-021-03819-2"


def test_a_full_record_parses_into_every_field():
    work = parse_work(crossref_message())
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
    parse_work(crossref_message(**overrides))


def test_the_embeddable_text_joins_title_and_abstract():
    """Byte-identical to how the library's own vectors were built."""
    assert parse_work(crossref_message()).embeddable == (
        "Highly accurate protein structure prediction. We present a method"
    )


def test_a_record_with_only_a_title_still_embeds():
    assert parse_work(crossref_message(abstract=None)).embeddable.endswith("prediction")


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


@respx.mock
async def test_a_known_doi_comes_back(http):
    respx.get(WORK_URL).mock(return_value=crossref_response())
    work = await crossref.fetch_work(http, CROSSREF, DOI)
    assert work.journal == "Nature"


@respx.mock
async def test_an_unknown_doi_is_reported_as_not_found(http):
    respx.get(WORK_URL).mock(return_value=httpx.Response(404))
    with pytest.raises(MetadataError) as raised:
        await crossref.fetch_work(http, CROSSREF, DOI)
    assert raised.value.reason == "not_found"
    assert DOI in str(raised.value)


@respx.mock
async def test_a_throttle_is_reported_with_its_retry_delay(http):
    respx.get(WORK_URL).mock(
        return_value=httpx.Response(429, headers={"retry-after": "30"})
    )
    with pytest.raises(MetadataError) as raised:
        await crossref.fetch_work(http, CROSSREF, DOI)
    assert raised.value.reason == "rate_limited"
    assert raised.value.retry_after == 30


@respx.mock
@pytest.mark.parametrize(
    "failure", [httpx.Response(500), httpx.ConnectError("no route")]
)
async def test_an_unreachable_source_is_reported_as_unavailable(http, failure):
    route = respx.get(WORK_URL)
    if isinstance(failure, httpx.Response):
        route.mock(return_value=failure)
    else:
        route.mock(side_effect=failure)
    with pytest.raises(MetadataError) as raised:
        await crossref.fetch_work(http, CROSSREF, DOI)
    assert raised.value.reason == "unavailable"


@respx.mock
async def test_a_body_that_is_not_json_is_unavailable(http):
    respx.get(WORK_URL).mock(return_value=httpx.Response(200, text="not json"))
    with pytest.raises(MetadataError) as raised:
        await crossref.fetch_work(http, CROSSREF, DOI)
    assert raised.value.reason == "unavailable"


@respx.mock
async def test_the_contact_address_is_sent_only_when_configured(http):
    route = respx.get(WORK_URL).mock(return_value=crossref_response())
    await crossref.fetch_work(http, CROSSREF, DOI, mailto="cluster@example.org")
    assert "mailto=cluster%40example.org" in str(route.calls[0].request.url)
    await crossref.fetch_work(http, CROSSREF, DOI)
    assert "mailto" not in str(route.calls[1].request.url)
