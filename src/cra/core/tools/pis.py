"""Finding people: the cluster's principal investigators and their groups."""

from typing import Annotated, Any

from pydantic import Field

from cra.config.settings import Settings
from cra.core.library.records import PI
from cra.core.library.text import overlap, query_tokens, words
from cra.core.retrieval.indexes import Indexes
from cra.core.tools.registry import Registry, ToolContext, ToolError, tool
from cra.core.tools.tiers import Tier
from cra.core.tools.views import ABSTRACT_IN_LIST, paper_view, pi_view

PUBLICATIONS_SHOWN = 10


def _searchable(pi: PI) -> str:
    return " ".join(
        (
            pi.name,
            pi.group,
            pi.department,
            pi.institution,
            *pi.research_focus,
            *pi.application_fields,
        )
    )


@tool(tier=Tier.PUBLIC)
def search_pis(
    ctx: ToolContext,
    query: Annotated[
        str, Field(description="A name, a topic or a field of application.")
    ],
    limit: Annotated[
        int, Field(description="How many people to return.", ge=1, le=42)
    ] = 5,
) -> dict[str, Any]:
    """Find principal investigators by name, research topic or application
    field. Returns a summary each; get_pi has the detail."""
    tokens = query_tokens(query)
    if not tokens:
        raise ToolError("Give a word of three letters or more.")
    scored = [(overlap(_searchable(pi), tokens), pi) for pi in ctx.indexes.library.pis]
    found = sorted((s for s in scored if s[0]), key=lambda s: -s[0])[:limit]
    return {"count": len(found), "results": [pi_view(pi) for _, pi in found]}


def _resolve(pis: tuple[PI, ...], name: str) -> PI | list[PI]:
    """Exact surname, then substring, then word overlap. Several equally good
    matches come back as a list so the caller can ask again."""
    wanted = name.strip().lower()
    if not wanted:
        return []
    exact = [pi for pi in pis if pi.last_name.lower() == wanted]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return exact
    partial = [pi for pi in pis if wanted in pi.name.lower()]
    if len(partial) == 1:
        return partial[0]
    if partial:
        return partial
    tokens = query_tokens(name)
    scored = [(len(words(pi.name) & set(tokens)), pi) for pi in pis] if tokens else []
    best = max((s[0] for s in scored), default=0)
    return [pi for score, pi in scored if score == best] if best else []


@tool(tier=Tier.PUBLIC)
def get_pi(
    ctx: ToolContext,
    name: Annotated[str, Field(description="A surname or a full name.")],
) -> dict[str, Any]:
    """Everything about one principal investigator, including their papers in
    the library. Ask by surname; an ambiguous name returns the candidates."""
    library = ctx.indexes.library
    found = _resolve(library.pis, name)
    if isinstance(found, list):
        if not found:
            raise ToolError(f"No principal investigator matching {name!r}.")
        return {
            "error": f"{name!r} matches several people; ask for one of them.",
            "matches": [pi.name for pi in found],
        }
    view = pi_view(found, full=True)
    papers = [
        paper_view(paper, abstract=ABSTRACT_IN_LIST)
        for doi in found.publication_dois[:PUBLICATIONS_SHOWN]
        if (paper := library.papers.get(doi)) is not None
    ]
    view["publications"] = papers
    view["publications_in_library"] = sum(
        1 for doi in found.publication_dois if doi in library.papers
    )
    return view


def setup(registry: Registry, settings: Settings, indexes: Indexes) -> None:
    if not indexes.library.pis:
        return
    registry.register(search_pis)
    registry.register(get_pi)
