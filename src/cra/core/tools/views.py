"""How a record is handed to the model.

Small and flat on purpose: every field costs context, and a paper that arrives
with its abstract twice is a paper the model reads twice.
"""

from typing import Any

from cra.core.library.records import PI, Paper, Summary

ABSTRACT_IN_LIST = 500


def paper_view(
    paper: Paper, *, abstract: str | int | None = None, group: str = ""
) -> dict[str, Any]:
    """``abstract`` caps the abstract's length, or drops it when 0; ``group`` is the
    paper's area on the publication map, left out when empty."""
    view: dict[str, Any] = {
        "doi": paper.doi,
        "title": paper.title,
        "authors": list(paper.authors),
        "year": paper.year,
    }
    if paper.journal:
        view["journal"] = paper.journal
    if group:
        view["group"] = group
    if paper.citation_count is not None:
        view["citation_count"] = paper.citation_count
    if paper.datasets:
        view["datasets"] = [{"doi": d.doi, "title": d.title} for d in paper.datasets]
    if abstract != 0 and paper.abstract:
        limit = abstract if isinstance(abstract, int) else None
        view["abstract"] = paper.abstract[:limit] if limit else paper.abstract
    return view


def summary_view(summary: Summary, *, compact: bool = False) -> dict[str, Any]:
    """A paper's method summary. Compact is what a list of papers carries: the
    sentence, the kind of work and the material."""
    view: dict[str, Any] = {
        "summary": summary.summary,
        "approach": summary.approach,
    }
    if summary.material_system:
        view["material_system"] = summary.material_system
    if compact:
        return view
    for key in ("processing_method", "results", "novelty", "limitations"):
        if value := getattr(summary, key):
            view[key] = value
    for key in ("characterization_methods", "properties_investigated"):
        if value := getattr(summary, key):
            view[key] = list(value)
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
    # The links travel in every view, so a list of people can be followed up
    # without a get_pi per name; the compact view leaves out the empty ones.
    for key in ("website", "profile_url"):
        if full or getattr(pi, key):
            view[key] = getattr(pi, key)
    return view
