"""The paper tools, against the small library the builder writes."""

import httpx
import pytest
import respx
from conftest import call_tool, make_settings
from fakes import FakeEncoder
from library_builder import write_library

from cra.core.library.library import Library
from cra.core.retrieval.indexes import Indexes
from cra.core.tools.registry import ToolContext, load
from cra.core.tools.tiers import Tier


async def test_search_papers_finds_a_paper_and_says_which_field_matched(registry, ctx):
    found = await call_tool(registry, ctx, "search_papers", query="perovskite")
    assert found["matched_on"] == "title"
    assert found["results"][0]["doi"] == "10.1000/alpha"
    assert found["results"][0]["authors"] == ["Ada Lovelace", "Grace Hopper"]


async def test_a_doi_in_a_keyword_search_returns_that_paper(registry, ctx):
    found = await call_tool(
        registry, ctx, "search_papers", query="the paper https://doi.org/10.1000/BETA."
    )
    assert found["matched_on"] == "doi"
    assert [p["doi"] for p in found["results"]] == ["10.1000/beta"]
    missing = await call_tool(registry, ctx, "search_papers", query="10.9999/elsewhere")
    assert "locate_paper_by_doi" in missing["error"]


async def test_semantic_search_needs_a_matching_encoder(
    registry, ctx, indexes, settings
):
    assert (await call_tool(registry, ctx, "semantic_search_papers", query="copper"))[
        "count"
    ] == 3
    mismatched = Indexes.build(indexes.library, FakeEncoder(name="another-model"))
    assert "semantic_search_papers" not in {
        s.name for s in load(settings, mismatched, ["papers"])
    }


async def test_get_paper_by_doi_reports_whether_a_full_text_is_there(registry, ctx):
    assert (await call_tool(registry, ctx, "get_paper_by_doi", doi="10.1000/ALPHA"))[
        "has_fulltext"
    ]
    assert not (
        await call_tool(registry, ctx, "get_paper_by_doi", doi="10.1000/gamma")
    )["has_fulltext"]
    assert (
        "not in the library"
        in (await call_tool(registry, ctx, "get_paper_by_doi", doi="x"))["error"]
    )


async def test_list_papers_filters_or_lists_everything(registry, ctx):
    assert (await call_tool(registry, ctx, "list_papers", author="hopper"))[
        "count"
    ] == 2
    assert (await call_tool(registry, ctx, "list_papers", year="2022"))["count"] == 1
    assert (await call_tool(registry, ctx, "list_papers", journal="nature"))[
        "count"
    ] == 1
    everything = await call_tool(registry, ctx, "list_papers", limit=2)
    assert (everything["count"], everything["returned"]) == (3, 2)
    years = [p["year"] for p in everything["results"]]
    assert years == sorted(years, reverse=True)
    listed = await call_tool(registry, ctx, "list_papers", author="hopper", limit=1)
    assert (listed["count"], listed["returned"]) == (2, 1)
    assert "abstract" not in listed["results"][0], "a listing does not carry abstracts"


async def test_many_topics_are_sized_in_one_call(registry, ctx):
    sized = await call_tool(
        registry, ctx, "count_papers", topics=["perovskite", "copper", "aardvark", "a"]
    )
    assert sized["papers"] == 3
    assert sized["counts"]["perovskite"]["title_or_abstract"] >= 1
    assert sized["counts"]["aardvark"] == {"title_or_abstract": 0, "full_text": 0}
    assert "error" in sized["counts"]["a"]
    assert (
        "not called correctly"
        in (await call_tool(registry, ctx, "count_papers", topics=[]))["error"]
    )


async def test_search_fulltext_returns_passages_within_the_budget(
    registry, ctx, settings
):
    found = await call_tool(registry, ctx, "search_fulltext", query="measured")
    assert found["count"] == 2
    snippets = found["results"][0]["snippets"]
    assert snippets
    assert all(len(s) <= settings.fulltext_snippet_chars for s in snippets)
    assert len(snippets) <= settings.fulltext_max_snippets
    assert (await call_tool(registry, ctx, "search_fulltext", query="aardvark"))[
        "count"
    ] == 0


async def test_the_full_text_comes_whole_or_in_passages(registry, ctx):
    whole = await call_tool(registry, ctx, "get_paper_fulltext", doi="10.1000/alpha")
    assert whole["fulltext"].startswith("# Perovskite")
    passages = await call_tool(
        registry, ctx, "get_paper_fulltext", doi="10.1000/alpha", query="methods"
    )
    assert "fulltext" not in passages
    assert len(passages["passages"]) == 1
    assert (
        "no full text"
        in (await call_tool(registry, ctx, "get_paper_fulltext", doi="10.1000/gamma"))[
            "error"
        ].lower()
    )


OUTSIDE_DOI = "10.1038/s41586-021-03819-2"


OUTSIDE_URL = "https://api.crossref.org/works/10.1038%2Fs41586-021-03819-2"


def test_the_locate_tool_is_absent_without_a_metadata_source(indexes, tmp_path):
    registry = load(make_settings(tmp_path, crossref_base_url=""), indexes)
    assert "locate_paper_by_doi" not in {spec.name for spec in registry}


def test_the_locate_tool_is_absent_without_an_encoder(settings, tmp_path):
    bare = Indexes.build(Library.load(write_library(tmp_path / "bare")))
    assert "locate_paper_by_doi" not in {spec.name for spec in load(settings, bare)}


@respx.mock
async def test_locating_an_outside_paper_returns_the_closest_work(registry, ctx):
    respx.get(OUTSIDE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "DOI": OUTSIDE_DOI,
                    "title": ["A paper from elsewhere"],
                    "author": [{"given": "Ada", "family": "Lovelace"}],
                    "issued": {"date-parts": [[2021]]},
                    "abstract": "<jats:p>Perovskite films.</jats:p>",
                }
            },
        )
    )
    result = await call_tool(registry, ctx, "locate_paper_by_doi", doi=OUTSIDE_DOI)
    assert result["in_library"] is False
    assert result["citation"].startswith("Lovelace, ")
    assert result["closest"]
    # The model must not read the position as a recomputed projection.
    assert "estimated" in result["placement"]


@respx.mock
async def test_locating_a_library_paper_asks_no_one(registry, ctx):
    route = respx.get(OUTSIDE_URL)
    result = await call_tool(registry, ctx, "locate_paper_by_doi", doi="10.1000/beta")
    assert result["in_library"] is True
    assert not route.called


async def test_similar_papers_points_at_the_tool_for_outside_dois(registry, ctx):
    """So the model reroutes itself instead of reporting a dead end."""
    result = await call_tool(registry, ctx, "get_similar_papers", doi=OUTSIDE_DOI)
    assert "locate_paper_by_doi" in result["error"]


SUMMARY_TOOLS = {"get_paper_summary", "find_papers_by_method"}


@pytest.fixture
def summarised(tmp_path):
    settings = make_settings(tmp_path)
    indexes = Indexes.build(
        Library.load(write_library(tmp_path / "lib", summaries=True)), FakeEncoder()
    )
    ctx = ToolContext(indexes=indexes, settings=settings, http=httpx.AsyncClient())
    return load(settings, indexes), ctx


def test_the_summary_tools_need_summaries_and_stay_in_the_chat(registry, summarised):
    assert SUMMARY_TOOLS.isdisjoint(spec.name for spec in registry)
    with_summaries, _ = summarised
    assert {s.name for s in with_summaries.specs(Tier.INTERNAL)} >= SUMMARY_TOOLS
    public = {s.name for s in with_summaries.specs(Tier.PUBLIC)}
    assert SUMMARY_TOOLS.isdisjoint(public), (
        "a summary is made from the full text, which the public tier never sees"
    )


async def test_a_paper_summary_says_who_wrote_it_and_from_what(summarised):
    registry, ctx = summarised
    got = await call_tool(registry, ctx, "get_paper_summary", doi="10.1000/ALPHA")
    assert got["approach"] == "computational"
    assert got["characterization_methods"] == ["radial distribution function"]
    assert (got["written_by"], got["from"]) == ("test-model", "fulltext")
    assert "title" in got
    assert "doi" in got
    no_text = await call_tool(registry, ctx, "get_paper_summary", doi="10.1000/gamma")
    assert "no method summary" in no_text["error"]
    unknown = await call_tool(registry, ctx, "get_paper_summary", doi="10.9/none")
    assert "not in the library" in unknown["error"]


async def test_papers_are_found_by_what_was_done_and_how(summarised):
    registry, ctx = summarised

    async def found(**filters):
        result = await call_tool(registry, ctx, "find_papers_by_method", **filters)
        return [r["doi"] for r in result["results"]]

    assert await found(approach="experimental") == ["10.1000/beta"]
    assert await found(approach="computational") == ["10.1000/alpha"]
    assert await found(method="XRD") == ["10.1000/beta"]
    assert await found(method="molecular dynamics") == ["10.1000/alpha"]
    assert await found(material="HALIDE") == ["10.1000/alpha"]
    assert await found(approach="experimental", method="molecular dynamics") == []
    listed = await call_tool(registry, ctx, "find_papers_by_method", approach="mixed")
    assert (listed["count"], listed["returned"]) == (0, 0)


async def test_a_list_of_summaries_carries_the_short_form_only(summarised):
    registry, ctx = summarised
    result = await call_tool(
        registry, ctx, "find_papers_by_method", approach="experimental"
    )
    (paper,) = result["results"]
    assert set(paper) == {
        "doi",
        "title",
        "year",
        "summary",
        "approach",
        "material_system",
    }
    assert result["written_by"] == ["test-model"]


async def test_finding_by_method_needs_a_filter_and_a_known_kind_of_work(summarised):
    registry, ctx = summarised
    empty = await call_tool(registry, ctx, "find_papers_by_method")
    assert "Give an approach, a method or a material" in empty["error"]
    wrong = await call_tool(registry, ctx, "find_papers_by_method", approach="alchemy")
    assert "approach" in wrong["error"]


async def test_papers_are_listed_by_their_area_on_the_map(registry, ctx):
    async def listed(**filters):
        result = await call_tool(registry, ctx, "list_papers", **filters)
        return {r["doi"]: r.get("group") for r in result["results"]}

    # a paper belongs to the group it has at every level of the map
    assert await listed(category="copper") == {
        "10.1000/beta": "copper",
        "10.1000/gamma": "battery",
    }
    assert set(await listed(category="BATTERY")) == {"10.1000/gamma"}
    assert await listed(category="nothing like it") == {}
    # the area is shown at the finest level the map has
    assert (await listed())["10.1000/alpha"] == "perovskite"


async def test_the_area_of_one_paper_is_in_its_record(registry, ctx):
    got = await call_tool(registry, ctx, "get_paper_by_doi", doi="10.1000/gamma")
    assert got["group"] == "battery"


async def test_without_a_map_papers_have_no_area(tmp_path):
    settings = make_settings(tmp_path)
    indexes = Indexes.build(
        Library.load(write_library(tmp_path / "lib", publication_map=False)),
        FakeEncoder(),
    )
    ctx = ToolContext(indexes=indexes, settings=settings, http=httpx.AsyncClient())
    registry = load(settings, indexes)
    refused = await call_tool(registry, ctx, "list_papers", category="copper")
    assert "no publication map" in refused["error"]
    everything = await call_tool(registry, ctx, "list_papers")
    assert all("group" not in r for r in everything["results"])
