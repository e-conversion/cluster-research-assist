"""The paper tools, against the small library the builder writes."""

import httpx
import respx
from conftest import call_tool, make_settings
from fakes import FakeEncoder
from library_builder import write_library

from cra.core.library.library import Library
from cra.core.retrieval.indexes import Indexes
from cra.core.tools.registry import load


async def test_search_papers_finds_a_paper_and_says_which_field_matched(registry, ctx):
    found = await call_tool(registry, ctx, "search_papers", query="perovskite")
    assert found["matched_on"] == "title"
    assert found["results"][0]["doi"] == "10.1000/alpha"
    assert found["results"][0]["authors"] == ["Ada Lovelace", "Grace Hopper"]


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
