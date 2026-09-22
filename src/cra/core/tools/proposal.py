"""The cluster's own proposal, which says what it set out to do."""

from typing import Annotated, Any

from pydantic import Field

from cra.config.settings import Settings
from cra.core.library.text import overlap, query_tokens
from cra.core.retrieval.indexes import Indexes
from cra.core.tools.registry import Registry, ToolContext, ToolError, tool
from cra.core.tools.tiers import Tier

PARAGRAPHS = 5
OVERVIEW_PARAGRAPHS = 5
# a proposal has paragraphs that are really tables; one of those can be most of
# an answer's context on its own
PASSAGE_CHARS = 1_500


@tool(tier=Tier.INTERNAL)
def get_proposal_fulltext(
    ctx: ToolContext,
    query: Annotated[
        str,
        Field(description="Words to find in the proposal, e.g. 'work package 3'."),
    ] = "",
) -> dict[str, Any]:
    """Search the cluster's funding proposal and return the passages that
    match. Without a query it returns the opening, as an overview. The
    proposal is long, so it is returned in paragraphs rather than whole."""
    proposal = ctx.indexes.library.proposal
    if proposal is None:
        raise ToolError("This library has no proposal.")
    # min_len=1: "work package 3" and "section 2" turn on the number
    tokens = query_tokens(query, min_len=1)
    if not tokens:
        return {
            "char_count": len(proposal.text),
            "paragraph_count": len(proposal.paragraphs),
            "opening": [
                p[:PASSAGE_CHARS] for p in proposal.paragraphs[:OVERVIEW_PARAGRAPHS]
            ],
        }
    scored = [(overlap(p, tokens), p) for p in proposal.paragraphs]
    found = sorted((s for s in scored if s[0]), key=lambda s: -s[0])[:PARAGRAPHS]
    if not found:
        raise ToolError(f"{query!r} does not appear in the proposal.")
    return {
        "count": len(found),
        "passages": [p[:PASSAGE_CHARS] for _, p in found],
    }


def setup(registry: Registry, settings: Settings, indexes: Indexes) -> None:
    if indexes.library.proposal is None:
        return
    registry.register(get_proposal_fulltext)
