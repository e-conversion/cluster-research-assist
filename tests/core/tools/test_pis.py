"""The tools about principal investigators."""

import pytest
from conftest import call_tool
from library_builder import write_library

from cra.core.library.library import Library
from cra.core.retrieval.indexes import Indexes
from cra.core.tools.registry import ToolContext, load


async def test_pis_are_found_by_topic_and_by_name(registry, ctx):
    assert (await call_tool(registry, ctx, "search_pis", query="electrocatalysis"))[
        "results"
    ][0]["name"] == "Prof. Dr. Grace Hopper"
    lovelace = await call_tool(registry, ctx, "get_pi", name="lovelace")
    assert lovelace["institution"] == "TUM"
    assert [p["doi"] for p in lovelace["publications"]] == [
        "10.1000/alpha",
        "10.1000/beta",
    ]
    assert (
        "No principal investigator"
        in (await call_tool(registry, ctx, "get_pi", name="zz"))["error"]
    )


async def test_every_pi_comes_at_once_with_what_the_library_holds_of_them(
    registry, ctx
):
    """'Which groups name X' is a question about all the groups together, and
    counting them from searches misses some."""
    everyone = await call_tool(registry, ctx, "list_pis")
    assert everyone["count"] == len(ctx.indexes.library.pis) == len(everyone["results"])
    lovelace = next(p for p in everyone["results"] if "Lovelace" in p["name"])
    assert lovelace["papers_in_library"] == 2
    assert lovelace["research_focus"]
    assert "publications" not in lovelace, "the detail is get_pi's job"


async def test_papers_are_ranked_by_how_many_pis_they_join(registry, ctx):
    """'Which paper has the most co-authors from the cluster' has no search
    that answers it; this ranks the attributed papers directly."""
    ranked = await call_tool(registry, ctx, "most_collaborative_papers", limit=2)
    first = ranked["results"][0]
    assert first["doi"] == "10.1000/beta"
    assert first["pi_count"] == len(first["pis"]) == 3
    assert ranked["results"][1]["pi_count"] <= 3
    assert "abstract" not in first
    assert (
        sum(y["papers"] for y in ranked["by_year"].values())
        == ranked["papers_with_a_pi"]
    )
    assert sum(y["shared_by_several"] for y in ranked["by_year"].values()) == 2


@pytest.fixture
def words_only(tmp_path, settings):
    """Word matching alone, so the ranking here is exact rather than whatever a
    stand-in encoder happens to place nearby."""
    indexes = Indexes.build(Library.load(write_library(tmp_path / "words")))
    return load(settings, indexes, ["pis"]), ToolContext(
        indexes=indexes, settings=settings
    )


async def test_find_experts_answers_by_what_people_published(words_only):
    """A profile rarely names a method; the papers do."""
    registry, ctx = words_only
    found = await call_tool(
        registry, ctx, "find_experts", topic="electrocatalysis copper"
    )
    assert found["papers_considered"] == 1
    # everyone who co-authored the matching paper, and nobody else
    assert {p["name"] for p in found["results"]} == {
        "Prof. Dr. Grace Hopper",
        "Dr. Ada Lovelace",
        "Dr. Emmy Noether",
    }
    assert found["results"][0]["evidence"][0]["doi"] == "10.1000/beta"
    assert found["results"][0]["matching_papers"] == 1


async def test_find_experts_ranks_by_how_much_of_their_work_matches(words_only):
    registry, ctx = words_only
    found = await call_tool(
        registry, ctx, "find_experts", topic="perovskite electrocatalysis"
    )
    # Lovelace and Hopper wrote both matching papers; Noether only one
    assert [p["matching_papers"] for p in found["results"]] == [2, 2, 1]
    assert found["results"][-1]["name"] == "Dr. Emmy Noether"


async def test_find_experts_says_so_when_nobody_matches(words_only):
    registry, ctx = words_only
    assert (
        "Nothing in the library"
        in (await call_tool(registry, ctx, "find_experts", topic="zebra husbandry"))[
            "error"
        ]
    )


async def test_a_distant_neighbour_is_not_an_expert(registry, ctx, monkeypatch):
    """A dense search returns its nearest neighbours whatever is asked, so
    without a floor every question would find an expert."""
    from cra.core.tools import pis as pis_module

    monkeypatch.setattr(pis_module, "MIN_SIMILARITY", 1.1)
    assert (
        "Nothing in the library"
        in (await call_tool(registry, ctx, "find_experts", topic="zebra husbandry"))[
            "error"
        ]
    )


def test_find_experts_needs_papers_attributed_to_people(tmp_path, settings):
    """Profiles without publications cannot support the claim it makes."""
    import json

    directory = write_library(tmp_path / "unattributed")
    pis = json.loads((directory / "pis.json").read_text())
    for pi in pis:
        pi["publication_dois"] = []
    (directory / "pis.json").write_text(json.dumps(pis))
    bare = Indexes.build(Library.load(directory, verify=False))
    names = {spec.name for spec in load(settings, bare, ["pis"])}
    assert names == {"search_pis", "get_pi", "list_pis"}
