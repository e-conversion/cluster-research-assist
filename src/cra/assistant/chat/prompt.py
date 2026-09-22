"""What the assistant is told before it is asked anything.

Built from the deployment's own configuration and what the library actually
holds, so the numbers it is given cannot drift from the numbers it can see.
"""

from cra.config.settings import Settings
from cra.core.library.library import Library

PROPOSAL_CHARS = 12_000


def identity(settings: Settings) -> str:
    parts = []
    if settings.cluster_funding_body:
        parts.append(f"funded by {settings.cluster_funding_body}")
    if settings.cluster_host_institutions:
        parts.append("hosted at " + " and ".join(settings.cluster_host_institutions))
    return ("; ".join(parts) + ".") if parts else ""


def build(settings: Settings, library: Library, tool_names: set[str]) -> str:
    """The system prompt. Guidance is added only for tools that exist, so the
    assistant is never told to use something this deployment does not have."""
    counts = library.counts
    lines = [
        f"You are a research assistant for {settings.cluster_description}.",
    ]
    if line := identity(settings):
        lines.append(line)
    lines.append(
        f"\nYou can search {counts['papers']} publications from the cluster"
        + (
            f" and profiles of {counts['pis']} principal investigators"
            if counts["pis"]
            else ""
        )
        + ". Use the tools before answering, and cite papers by title and DOI. "
        "If something is not in the library, say so rather than guessing."
    )

    if {"search_papers", "semantic_search_papers"} <= tool_names:
        lines.append(
            "\nTwo searches complement each other. search_papers matches words, so it is "
            "right for exact terms, acronyms, formulas and names. semantic_search_papers "
            "matches meaning, so it is right when the question is worded differently from "
            "the papers. When unsure, run both and combine what they return."
        )
    if "get_similar_papers" in tool_names:
        lines.append(
            "get_similar_papers takes a DOI rather than a query, for 'what else is like this'."
        )
    if "list_papers" in tool_names:
        lines.append(
            "list_papers filters by author, year or journal, for exhaustive questions such as "
            "'everything by X' where the top few results of a search are not enough."
        )
    if "search_fulltext" in tool_names:
        lines.append(
            "\nsearch_fulltext looks inside the papers themselves and returns short passages. "
            "Use it for methods, materials or numbers that an abstract leaves out."
        )
    if "get_paper_fulltext" in tool_names:
        lines.append(
            "get_paper_fulltext reads one paper. Give it a query to get the relevant passages "
            "rather than the whole text."
        )
    if {"get_collaborators", "collaboration_centrality"} & tool_names:
        lines.append(
            "\nThe collaboration tools answer questions search cannot: who publishes with whom, "
            "which papers two people share, who bridges otherwise separate groups, and which "
            "groups cluster together."
        )
    if "search_nomad" in tool_names:
        lines.append(
            "\nsearch_nomad searches a public materials-data repository outside the cluster. "
            "Report total_matches first, since the entries returned are only a sample, and "
            "never present an entry as the data behind a cluster paper: there is no such link."
        )
    lines.append(
        "\nFor a question about the cluster as a whole, one search is not enough: ask several "
        "different ways, then summarise what you found."
    )
    lines.append("Answer in the language of the question.")

    if library.proposal and library.proposal.summary:
        lines.append(
            "\nThe following is the summary of the cluster's own funding proposal, as "
            "background.\n\n<proposal_summary>\n"
            + library.proposal.summary[:PROPOSAL_CHARS]
            + "\n</proposal_summary>"
        )
    return "\n".join(lines)
