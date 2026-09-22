"""How a record is handed to the model.

Small and flat on purpose: every field costs context, and a paper that arrives
with its abstract twice is a paper the model reads twice.
"""

from typing import Any

from cra.core.library.records import PI, Paper

ABSTRACT_IN_LIST = 500


def paper_view(paper: Paper, *, abstract: str | int | None = None) -> dict[str, Any]:
    """``abstract`` caps the abstract's length, or drops it when 0."""
    view: dict[str, Any] = {
        "doi": paper.doi,
        "title": paper.title,
        "authors": list(paper.authors),
        "year": paper.year,
    }
    if paper.journal:
        view["journal"] = paper.journal
    if paper.citation_count is not None:
        view["citation_count"] = paper.citation_count
    if paper.datasets:
        view["datasets"] = [{"doi": d.doi, "title": d.title} for d in paper.datasets]
    if abstract != 0 and paper.abstract:
        limit = abstract if isinstance(abstract, int) else None
        view["abstract"] = paper.abstract[:limit] if limit else paper.abstract
    return view


def pi_view(pi: PI, *, full: bool = False) -> dict[str, Any]:
    view: dict[str, Any] = {
        "name": pi.name,
        "group": pi.group,
        "department": pi.department,
        "institution": pi.institution,
        "research_focus": list(pi.research_focus),
        "application_fields": list(pi.application_fields),
        "publication_count": len(pi.publication_dois),
    }
    if full:
        view["website"] = pi.website
        view["profile_url"] = pi.profile_url
    return view
