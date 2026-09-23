"""The tools, against the small library the builder writes."""

import json

import httpx
import pytest
import respx
from conftest import make_settings
from fakes import FakeEncoder
from library_builder import write_library

from cra.core.library.library import Library
from cra.core.retrieval.indexes import Indexes
from cra.core.tools.registry import ToolContext, load
from cra.core.tools.tiers import Tier

ALL_MODULES = ["papers", "pis", "proposal", "graph", "nomad", "status"]


@pytest.fixture
def indexes(tmp_path):
    return Indexes.build(Library.load(write_library(tmp_path / "lib")), FakeEncoder())


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def registry(settings, indexes):
    return load(settings, indexes, ALL_MODULES)


@pytest.fixture
def ctx(indexes, settings):
    return ToolContext(indexes=indexes, settings=settings, http=httpx.AsyncClient())


async def call(registry, ctx, tool_name, **arguments):
    return await registry.call(tool_name, arguments, ctx)


def test_every_tool_is_registered_with_a_tier(registry):
    by_tier = {spec.name: spec.tier for spec in registry}
    assert by_tier == {
        "collaboration_centrality": Tier.PUBLIC,
        "collaboration_communities": Tier.PUBLIC,
        "get_collaborators": Tier.PUBLIC,
        "get_paper_by_doi": Tier.PUBLIC,
        "get_paper_fulltext": Tier.INTERNAL,
        "get_pi": Tier.PUBLIC,
        "find_experts": Tier.PUBLIC,
        "get_proposal_fulltext": Tier.INTERNAL,
        "get_similar_papers": Tier.PUBLIC,
        "joint_papers": Tier.PUBLIC,
        "library_status": Tier.PUBLIC,
        "list_papers": Tier.PUBLIC,
        "list_pis": Tier.PUBLIC,
        "most_collaborative_papers": Tier.PUBLIC,
        "search_fulltext": Tier.PUBLIC,
        "search_nomad": Tier.PUBLIC,
        "search_papers": Tier.PUBLIC,
        "search_pis": Tier.PUBLIC,
        "semantic_search_papers": Tier.PUBLIC,
    }


def test_the_two_things_that_stay_inside_are_internal(registry):
    public = {spec.name for spec in registry.specs(Tier.PUBLIC)}
    assert "get_paper_fulltext" not in public
    assert "get_proposal_fulltext" not in public
    assert "search_fulltext" in public, (
        "searching inside the text is public, reading it is not"
    )


def test_a_library_without_extras_registers_fewer_tools(tmp_path, settings):
    bare = Indexes.build(
        Library.load(
            write_library(
                tmp_path / "bare",
                fulltexts=False,
                pis=False,
                graph=False,
                proposal=False,
            )
        )
    )
    names = {spec.name for spec in load(settings, bare, ALL_MODULES)}
    assert names == {
        "search_papers",
        "get_paper_by_doi",
        "list_papers",
        "get_similar_papers",
        "library_status",
        "search_nomad",
    }


async def test_search_papers_finds_a_paper_and_says_which_field_matched(registry, ctx):
    found = await call(registry, ctx, "search_papers", query="perovskite")
    assert found["matched_on"] == "title"
    assert found["results"][0]["doi"] == "10.1000/alpha"
    assert found["results"][0]["authors"] == ["Ada Lovelace", "Grace Hopper"]


async def test_semantic_search_needs_a_matching_encoder(
    registry, ctx, indexes, settings
):
    assert (await call(registry, ctx, "semantic_search_papers", query="copper"))[
        "count"
    ] == 3
    mismatched = Indexes.build(indexes.library, FakeEncoder(name="another-model"))
    assert "semantic_search_papers" not in {
        s.name for s in load(settings, mismatched, ["papers"])
    }


async def test_get_paper_by_doi_reports_whether_a_full_text_is_there(registry, ctx):
    assert (await call(registry, ctx, "get_paper_by_doi", doi="10.1000/ALPHA"))[
        "has_fulltext"
    ]
    assert not (await call(registry, ctx, "get_paper_by_doi", doi="10.1000/gamma"))[
        "has_fulltext"
    ]
    assert (
        "not in the library"
        in (await call(registry, ctx, "get_paper_by_doi", doi="x"))["error"]
    )


async def test_list_papers_filters_or_lists_everything(registry, ctx):
    assert (await call(registry, ctx, "list_papers", author="hopper"))["count"] == 2
    assert (await call(registry, ctx, "list_papers", year="2022"))["count"] == 1
    assert (await call(registry, ctx, "list_papers", journal="nature"))["count"] == 1
    everything = await call(registry, ctx, "list_papers", limit=2)
    assert (everything["count"], everything["returned"]) == (3, 2)
    years = [p["year"] for p in everything["results"]]
    assert years == sorted(years, reverse=True)
    listed = await call(registry, ctx, "list_papers", author="hopper", limit=1)
    assert (listed["count"], listed["returned"]) == (2, 1)
    assert "abstract" not in listed["results"][0], "a listing does not carry abstracts"


async def test_search_fulltext_returns_passages_within_the_budget(
    registry, ctx, settings
):
    found = await call(registry, ctx, "search_fulltext", query="measured")
    assert found["count"] == 2
    snippets = found["results"][0]["snippets"]
    assert snippets
    assert all(len(s) <= settings.fulltext_snippet_chars for s in snippets)
    assert len(snippets) <= settings.fulltext_max_snippets
    assert (await call(registry, ctx, "search_fulltext", query="aardvark"))[
        "count"
    ] == 0


async def test_the_full_text_comes_whole_or_in_passages(registry, ctx):
    whole = await call(registry, ctx, "get_paper_fulltext", doi="10.1000/alpha")
    assert whole["fulltext"].startswith("# Perovskite")
    passages = await call(
        registry, ctx, "get_paper_fulltext", doi="10.1000/alpha", query="methods"
    )
    assert "fulltext" not in passages
    assert len(passages["passages"]) == 1
    assert (
        "no full text"
        in (await call(registry, ctx, "get_paper_fulltext", doi="10.1000/gamma"))[
            "error"
        ].lower()
    )


async def test_pis_are_found_by_topic_and_by_name(registry, ctx):
    assert (await call(registry, ctx, "search_pis", query="electrocatalysis"))[
        "results"
    ][0]["name"] == "Prof. Dr. Grace Hopper"
    lovelace = await call(registry, ctx, "get_pi", name="lovelace")
    assert lovelace["institution"] == "TUM"
    assert [p["doi"] for p in lovelace["publications"]] == [
        "10.1000/alpha",
        "10.1000/beta",
    ]
    assert (
        "No principal investigator"
        in (await call(registry, ctx, "get_pi", name="zz"))["error"]
    )


async def test_every_pi_comes_at_once_with_what_the_library_holds_of_them(
    registry, ctx
):
    """'Which groups name X' is a question about all the groups together, and
    counting them from searches misses some."""
    everyone = await call(registry, ctx, "list_pis")
    assert everyone["count"] == len(ctx.indexes.library.pis) == len(everyone["results"])
    lovelace = next(p for p in everyone["results"] if "Lovelace" in p["name"])
    assert lovelace["papers_in_library"] == 2
    assert lovelace["research_focus"]
    assert "publications" not in lovelace, "the detail is get_pi's job"


async def test_papers_are_ranked_by_how_many_pis_they_join(registry, ctx):
    """'Which paper has the most co-authors from the cluster' has no search
    that answers it; this ranks the attributed papers directly."""
    ranked = await call(registry, ctx, "most_collaborative_papers", limit=2)
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


async def test_the_proposal_answers_with_passages(registry, ctx):
    overview = await call(registry, ctx, "get_proposal_fulltext")
    assert overview["paragraph_count"] == 5
    found = await call(registry, ctx, "get_proposal_fulltext", query="work package 3")
    assert "electrocatalysis" in found["passages"][0]
    assert (
        "does not appear"
        in (await call(registry, ctx, "get_proposal_fulltext", query="zebra"))["error"]
    )


async def test_the_graph_answers_who_works_with_whom(registry, ctx):
    collaborators = await call(registry, ctx, "get_collaborators", pi_query="hopper")
    assert collaborators["collaborator_count"] == 2
    joint = await call(registry, ctx, "joint_papers", pi_a="lovelace", pi_b="hopper")
    assert joint["count"] == 2
    assert [p["doi"] for p in joint["papers"]] == ["10.1000/alpha", "10.1000/beta"]
    assert joint["papers"][0]["title"], "a title saves a lookup per paper"
    assert joint["not_in_library"] == []
    central = await call(registry, ctx, "collaboration_centrality", limit=1)
    assert central["ranked_by"] == "betweenness"
    assert central["results"][0]["name"] == "Prof. Dr. Grace Hopper"
    assert central["results"][0]["collaborators"] == 2
    assert central["results"][0]["shared_papers"] >= 2
    by_partners = await call(
        registry, ctx, "collaboration_centrality", limit=3, by="collaborators"
    )
    counts = [row["collaborators"] for row in by_partners["results"]]
    assert counts == sorted(counts, reverse=True)
    assert (
        "not called correctly"
        in (await call(registry, ctx, "collaboration_centrality", by="fame"))["error"]
    )
    assert (await call(registry, ctx, "collaboration_communities"))[
        "community_count"
    ] == 1
    assert (
        "No principal"
        in (await call(registry, ctx, "get_collaborators", pi_query="zz"))["error"]
    )


async def test_library_status_reports_what_is_there(registry, ctx):
    status = await call(registry, ctx, "library_status")
    assert status["counts"]["papers"] == 3
    assert status["semantic_search"] is True
    assert status["tier"] == "internal"


@respx.mock
async def test_search_nomad_asks_the_repository_and_summarises(registry, ctx, settings):
    route = respx.post(f"{settings.nomad_base_url}/entries/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "pagination": {"total": 412},
                "data": [
                    {
                        "entry_id": "abc",
                        "upload_name": "TiO2 runs",
                        "entry_type": "Simulation",
                        "authors": [{"name": "Ada Lovelace"}],
                        "results": {
                            "material": {
                                "chemical_formula_reduced": "O2Ti",
                                "elements": ["O", "Ti"],
                            }
                        },
                    }
                ],
            },
        )
    )
    found = await call(
        registry, ctx, "search_nomad", elements="Ti,O", author="Prof. Dr. Ada Lovelace"
    )
    assert found["total_matches"] == 412
    assert found["entries"][0]["url"].endswith("abc")
    sent = json.loads(route.calls.last.request.read())
    assert sent["query"] == {
        "results.material.elements": {"all": ["Ti", "O"]},
        # NOMAD stores a depositor's name without a title
        "authors.name": "Ada Lovelace",
    }
    assert sent["pagination"]["order_by"] == "upload_create_time"


@respx.mock
async def test_nomad_failures_are_reported_not_raised(registry, ctx, settings):
    respx.post(f"{settings.nomad_base_url}/entries/query").mock(
        return_value=httpx.Response(422)
    )
    assert (
        "rejected"
        in (await call(registry, ctx, "search_nomad", formula="nonsense"))["error"]
    )
    assert "at least one" in (await call(registry, ctx, "search_nomad"))["error"]


@respx.mock
async def test_nomad_being_unreachable_is_reported(registry, ctx, settings):
    respx.post(f"{settings.nomad_base_url}/entries/query").mock(
        side_effect=httpx.ConnectError("no route")
    )
    assert (
        "not reachable"
        in (await call(registry, ctx, "search_nomad", text="water"))["error"]
    )


async def test_a_proposal_passage_cannot_fill_the_context(registry, ctx, tmp_path):
    """A real proposal has paragraphs that are tables; one of those would
    otherwise be most of an answer's context."""
    from cra.core.tools.proposal import PASSAGE_CHARS

    library = ctx.indexes.library
    long_paragraph = "work package 3 " + ("filler " * 5_000)
    (library.path / "proposal.md").write_text(f"{long_paragraph}\n\nshort one\n")
    reloaded = Library.load(library.path, verify=False)
    fresh = ToolContext(
        indexes=Indexes.build(reloaded), settings=ctx.settings, http=ctx.http
    )
    found = await call(registry, fresh, "get_proposal_fulltext", query="work package 3")
    assert max(len(p) for p in found["passages"]) == PASSAGE_CHARS


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
    found = await call(registry, ctx, "find_experts", topic="electrocatalysis copper")
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
    found = await call(
        registry, ctx, "find_experts", topic="perovskite electrocatalysis"
    )
    # Lovelace and Hopper wrote both matching papers; Noether only one
    assert [p["matching_papers"] for p in found["results"]] == [2, 2, 1]
    assert found["results"][-1]["name"] == "Dr. Emmy Noether"


async def test_find_experts_says_so_when_nobody_matches(words_only):
    registry, ctx = words_only
    assert (
        "Nothing in the library"
        in (await call(registry, ctx, "find_experts", topic="zebra husbandry"))["error"]
    )


async def test_a_distant_neighbour_is_not_an_expert(registry, ctx, monkeypatch):
    """A dense search returns its nearest neighbours whatever is asked, so
    without a floor every question would find an expert."""
    from cra.core.tools import pis as pis_module

    monkeypatch.setattr(pis_module, "MIN_SIMILARITY", 1.1)
    assert (
        "Nothing in the library"
        in (await call(registry, ctx, "find_experts", topic="zebra husbandry"))["error"]
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
