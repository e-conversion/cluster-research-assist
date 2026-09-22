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
EVIDENCE_SHOWN = 3
# wide enough that a person with a few matching papers is not missed, narrow
# enough that everyone who ever touched the topic is not "an expert"
EXPERT_PAPERS = 40
# Nearest is not the same as relevant: a dense search returns its neighbours
# whatever the question, so a floor is what makes "nobody" a possible answer.
MIN_SIMILARITY = 0.35
# a word match is evidence; a neighbour in the embedding space is a hint
LEXICAL_WEIGHT = 2.0


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
    if any(pi.publication_dois for pi in indexes.library.pis):
        registry.register(find_experts)


@tool(tier=Tier.PUBLIC)
async def find_experts(
    ctx: ToolContext,
    topic: Annotated[str, Field(description="What you need expertise in.")],
    limit: Annotated[
        int, Field(description="How many people to return.", ge=1, le=20)
    ] = 5,
) -> dict[str, Any]:
    """Who to talk to about a topic, judged by what they have published rather
    than by how they describe themselves. Use this for "who could help me
    with X" and "who should I collaborate with": a profile rarely names a
    method, but the papers do."""
    library = ctx.indexes.library
    if not library.pis:
        raise ToolError("This library has no principal investigators.")

    papers = _matching_papers(ctx, topic)
    if not papers:
        raise ToolError(f"Nothing in the library matches {topic!r}.")

    found = []
    for pi in library.pis:
        theirs = [
            (doi, score) for doi, score in papers.items() if doi in pi.publication_dois
        ]
        if not theirs:
            continue
        theirs.sort(key=lambda pair: -pair[1])
        found.append(
            {
                **pi_view(pi),
                "matching_papers": len(theirs),
                "evidence": [
                    {"doi": doi, "title": library.papers[doi].title}
                    for doi, _ in theirs[:EVIDENCE_SHOWN]
                ],
                "_score": sum(score for _, score in theirs),
            }
        )
    found.sort(key=lambda person: -person["_score"])
    for person in found:
        del person["_score"]
    return {
        "count": len(found),
        "papers_considered": len(papers),
        "results": found[:limit],
    }


def _matching_papers(ctx: ToolContext, topic: str) -> dict[str, float]:
    """Papers about the topic, by words and by meaning, scored together."""
    indexes = ctx.indexes
    scores: dict[str, float] = {}
    for hit in indexes.lexical.search(topic, EXPERT_PAPERS):
        scores[hit.doi] = scores.get(hit.doi, 0.0) + LEXICAL_WEIGHT
    if indexes.semantic_ready and indexes.encoder and indexes.dense:
        vector = indexes.encoder.encode(topic)
        for hit in indexes.dense.search(vector, EXPERT_PAPERS):
            if hit.score >= MIN_SIMILARITY:
                scores[hit.doi] = scores.get(hit.doi, 0.0) + hit.score
    return scores
