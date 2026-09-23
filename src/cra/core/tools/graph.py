"""Who works with whom: questions about the co-authorship network that search
cannot answer."""

from typing import Annotated, Any, Literal

from pydantic import Field

from cra.config.settings import Settings
from cra.core.retrieval.indexes import Indexes
from cra.core.tools.registry import Registry, ToolContext, ToolError, tool
from cra.core.tools.tiers import Tier
from cra.core.tools.views import paper_view

Person = Annotated[str, Field(description="A surname, full name or group name.")]


def _graph(ctx: ToolContext):
    if ctx.indexes.graph is None:
        raise ToolError("This library has no collaboration graph.")
    return ctx.indexes.graph


def _resolve(ctx: ToolContext, query: str) -> str:
    node = _graph(ctx).resolve(query)
    if node is None:
        raise ToolError(f"No principal investigator matching {query!r}.")
    return node


@tool(tier=Tier.PUBLIC)
def get_collaborators(ctx: ToolContext, pi_query: Person) -> dict[str, Any]:
    """Everyone who has co-authored a paper with this person, most shared
    papers first."""
    graph = _graph(ctx)
    node = _resolve(ctx, pi_query)
    collaborators = graph.collaborators(node)
    return {
        **vars(graph.person(node)),
        "collaborator_count": len(collaborators),
        "collaborators": collaborators,
    }


@tool(tier=Tier.PUBLIC)
def joint_papers(ctx: ToolContext, pi_a: Person, pi_b: Person) -> dict[str, Any]:
    """The papers two people wrote together: title, year and DOI for each one
    the library holds, and the DOIs of any it does not."""
    graph = _graph(ctx)
    first, second = _resolve(ctx, pi_a), _resolve(ctx, pi_b)
    count, dois = graph.joint_papers(first, second)
    papers = ctx.indexes.library.papers
    return {
        "pi_a": vars(graph.person(first)),
        "pi_b": vars(graph.person(second)),
        "count": count,
        "papers": [
            paper_view(papers[doi], abstract=0) for doi in dois if doi in papers
        ],
        "not_in_library": [doi for doi in dois if doi not in papers],
    }


Ranking = Annotated[
    Literal["betweenness", "collaborators", "shared_papers"],
    Field(
        description="betweenness: who bridges otherwise separate groups; "
        "collaborators: who has co-authored with the most different people; "
        "shared_papers: who has the most co-authored papers in total."
    ),
]


@tool(tier=Tier.PUBLIC)
def collaboration_centrality(
    ctx: ToolContext,
    limit: Annotated[
        int, Field(description="How many people to return.", ge=1, le=42)
    ] = 10,
    by: Ranking = "betweenness",
) -> dict[str, Any]:
    """The people at the centre of the co-authorship network, ranked as asked.
    Every entry carries all three numbers: betweenness (a high score means
    many collaborations run through this person, not that they publish a lot),
    the number of distinct collaborators, and the total of shared papers."""
    return {"ranked_by": by, "results": _graph(ctx).centrality(limit, by)}


@tool(tier=Tier.PUBLIC)
def collaboration_communities(ctx: ToolContext) -> dict[str, Any]:
    """Clusters of people who mostly publish with each other. People with no
    shared paper at all are counted, not listed."""
    return _graph(ctx).communities()


def setup(registry: Registry, settings: Settings, indexes: Indexes) -> None:
    if indexes.graph is None:
        return
    for function in (
        get_collaborators,
        joint_papers,
        collaboration_centrality,
        collaboration_communities,
    ):
        registry.register(function)
