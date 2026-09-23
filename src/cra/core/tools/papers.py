"""Finding papers: by keyword, by meaning, by metadata, and by their text."""

from typing import Annotated, Any

from pydantic import Field

from cra.config.settings import Settings
from cra.core.library.records import normalise_doi
from cra.core.library.text import query_tokens
from cra.core.retrieval.indexes import Indexes
from cra.core.tools.registry import Registry, ToolContext, ToolError, tool
from cra.core.tools.tiers import Tier
from cra.core.tools.views import ABSTRACT_IN_LIST, paper_view

Doi = Annotated[
    str, Field(description="DOI of the paper, e.g. 10.1038/s41586-021-03819-2")
]
Limit = Annotated[int, Field(description="How many papers to return.", ge=1, le=50)]


@tool(tier=Tier.PUBLIC)
def search_papers(
    ctx: ToolContext,
    query: Annotated[str, Field(description="Words to search for.")],
    limit: Limit = 5,
) -> dict[str, Any]:
    """Keyword search over titles and abstracts. Best for exact terms,
    acronyms, formulas and author names. Reports which field matched."""
    hits = ctx.indexes.lexical.search(query, limit)
    papers = ctx.indexes.library.papers
    return {
        "count": len(hits),
        "matched_on": hits[0].matched_on if hits else None,
        "results": [paper_view(papers[h.doi]) for h in hits],
    }


@tool(tier=Tier.PUBLIC)
async def semantic_search_papers(
    ctx: ToolContext,
    query: Annotated[
        str, Field(description="A question or a description of the topic.")
    ],
    limit: Limit = 5,
) -> dict[str, Any]:
    """Meaning-based search. Best when the wording of the question differs from
    the wording of the papers. Run it alongside search_papers when unsure."""
    indexes = ctx.indexes
    encoder, dense = indexes.encoder, indexes.dense
    if encoder is None or dense is None or not indexes.semantic_ready:
        raise ToolError(
            "Semantic search is not available in this deployment; use search_papers."
        )
    vector = encoder.encode(query)
    hits = dense.search(vector, limit)
    papers = indexes.library.papers
    return {
        "count": len(hits),
        "results": [
            {**paper_view(papers[h.doi]), "score": round(h.score, 4)}
            for h in hits
            if h.doi in papers
        ],
    }


@tool(tier=Tier.PUBLIC)
def get_similar_papers(ctx: ToolContext, doi: Doi, limit: Limit = 5) -> dict[str, Any]:
    """Papers closest in content to one you already have. Takes a DOI, not a
    query: use it for "what else is like this paper?"."""
    if ctx.indexes.dense is None:
        raise ToolError("This library has no embeddings.")
    hits = ctx.indexes.dense.similar(doi, limit)
    if hits is None:
        raise ToolError(f"{doi} is not in the library.")
    papers = ctx.indexes.library.papers
    return {
        "doi": normalise_doi(doi),
        "results": [
            {
                **paper_view(papers[h.doi], abstract=ABSTRACT_IN_LIST),
                "score": round(h.score, 4),
            }
            for h in hits
            if h.doi in papers
        ],
    }


@tool(tier=Tier.PUBLIC)
def get_paper_by_doi(ctx: ToolContext, doi: Doi) -> dict[str, Any]:
    """Everything known about one paper: title, authors, year, journal,
    citations, abstract and any deposited datasets."""
    paper = ctx.indexes.library.paper(doi)
    if paper is None:
        raise ToolError(f"{doi} is not in the library.")
    view = paper_view(paper)
    view["has_fulltext"] = ctx.indexes.library.fulltext(doi) is not None
    return view


@tool(tier=Tier.PUBLIC)
def list_papers(
    ctx: ToolContext,
    author: Annotated[str, Field(description="Part of an author's name.")] = "",
    year: Annotated[str, Field(description="Exact publication year.")] = "",
    journal: Annotated[str, Field(description="Part of a journal name.")] = "",
    limit: Annotated[
        int, Field(description="How many papers to return.", ge=1, le=200)
    ] = 50,
) -> dict[str, Any]:
    """Exhaustive listing by metadata, newest first. Use it for "every paper
    by X" or "what was published in 2022", where a ranked search would stop
    at the most relevant few."""
    if not any((author, year, journal)):
        raise ToolError("Give at least one of author, year or journal.")
    from cra.core.library.text import fold

    wanted_author, wanted_journal = fold(author), fold(journal)
    matches = [
        paper
        for paper in ctx.indexes.library.papers.values()
        if (not year or paper.year == year)
        and (not wanted_author or any(wanted_author in fold(a) for a in paper.authors))
        and (not wanted_journal or wanted_journal in fold(paper.journal))
    ]
    matches.sort(key=lambda p: p.year, reverse=True)
    return {
        "count": len(matches),
        "returned": min(len(matches), limit),
        "results": [paper_view(p, abstract=0) for p in matches[:limit]],
    }


def _snippets(text: str, tokens: list[str], size: int, most: int) -> list[str]:
    """Windows of ``text`` around the query words, at most ``most`` of them."""
    lowered = text.lower()
    found: list[str] = []
    seen_at: list[int] = []
    for token in tokens:
        start = 0
        while len(found) < most:
            at = lowered.find(token, start)
            if at < 0:
                break
            start = at + len(token)
            if any(abs(at - previous) < size for previous in seen_at):
                continue
            seen_at.append(at)
            begin = max(0, at - size // 3)
            found.append(text[begin : begin + size].strip().replace("\n", " "))
        if len(found) >= most:
            break
    return found


# INTERNAL although it returns only passages: an anonymous caller can walk a
# paper by asking for the words at the end of each passage, so outside the
# sign-in the budget is a speed limit, not a boundary
@tool(tier=Tier.INTERNAL)
def search_fulltext(
    ctx: ToolContext,
    query: Annotated[str, Field(description="Words that must all appear in the text.")],
    limit: Limit = 5,
) -> dict[str, Any]:
    """Search inside the full texts and return short passages around the
    matches. Use it for methods, materials or numbers that an abstract omits.
    The passages are deliberately short; the full text is a separate tool."""
    tokens = query_tokens(query, min_len=2)
    if not tokens:
        raise ToolError("Give at least one word of two letters or more.")
    size = ctx.settings.fulltext_snippet_chars
    most = ctx.settings.fulltext_max_snippets
    library = ctx.indexes.library
    results: list[dict[str, Any]] = []
    for doi, entry in library.fulltexts.items():
        lowered = entry.text.lower()
        counts = [lowered.count(t) for t in tokens]
        if not all(counts):
            continue
        paper = library.papers.get(doi)
        results.append(
            {
                "doi": doi,
                "title": paper.title if paper else doi,
                "score": sum(counts),
                "snippets": _snippets(entry.text, tokens, size, most),
            }
        )
    results.sort(key=lambda r: -int(r["score"]))
    return {
        "count": len(results),
        "returned": min(len(results), limit),
        "results": results[:limit],
    }


@tool(tier=Tier.INTERNAL)
def get_paper_fulltext(
    ctx: ToolContext,
    doi: Doi,
    query: Annotated[
        str,
        Field(
            description="Words to find within the paper; empty returns the whole text."
        ),
    ] = "",
) -> dict[str, Any]:
    """The full text of one paper. With a query it returns only the passages
    that match, which is usually what an answer needs; without one it returns
    the whole body, which can be tens of thousands of characters."""
    entry = ctx.indexes.library.fulltext(doi)
    if entry is None:
        raise ToolError(f"No full text for {doi}.")
    view: dict[str, Any] = {
        "doi": entry.doi,
        "source": entry.source,
        "url": entry.url,
        "char_count": entry.char_count,
        "fetched_at": entry.fetched_at,
    }
    tokens = query_tokens(query, min_len=2)
    if not tokens:
        view["fulltext"] = entry.text
        return view
    view["passages"] = _snippets(
        entry.text, tokens, ctx.settings.fulltext_snippet_chars * 4, 8
    )
    if not view["passages"]:
        raise ToolError(f"{query!r} does not appear in {doi}.")
    return view


def setup(registry: Registry, settings: Settings, indexes: Indexes) -> None:
    registry.register(search_papers)
    registry.register(get_paper_by_doi)
    registry.register(list_papers)
    if indexes.dense is not None:
        registry.register(get_similar_papers)
    if indexes.semantic_ready:
        registry.register(semantic_search_papers)
    if indexes.library.fulltexts:
        registry.register(search_fulltext)
        registry.register(get_paper_fulltext)
