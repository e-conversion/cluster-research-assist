"""The proposal tool."""

from conftest import call_tool

from cra.core.library.library import Library
from cra.core.retrieval.indexes import Indexes
from cra.core.tools.registry import ToolContext


async def test_the_proposal_answers_with_passages(registry, ctx):
    overview = await call_tool(registry, ctx, "get_proposal_fulltext")
    assert overview["paragraph_count"] == 5
    found = await call_tool(
        registry, ctx, "get_proposal_fulltext", query="work package 3"
    )
    assert "electrocatalysis" in found["passages"][0]
    assert (
        "does not appear"
        in (await call_tool(registry, ctx, "get_proposal_fulltext", query="zebra"))[
            "error"
        ]
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
    found = await call_tool(
        registry, fresh, "get_proposal_fulltext", query="work package 3"
    )
    assert max(len(p) for p in found["passages"]) == PASSAGE_CHARS
