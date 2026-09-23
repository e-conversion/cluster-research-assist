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
        + ". Use the tools before answering, and cite papers by title and DOI, copied "
        "from a tool result: a DOI you cannot see in a result does not exist. If "
        "something is not in the library, say so rather than guessing."
    )
    holdings = [
        "each paper's title, authors, year, journal, abstract and citation count"
    ]
    if library.fulltexts:
        holdings.append("full texts")
    if counts["pis"]:
        holdings.append(
            "each principal investigator's group, institution, stated research focus and "
            "application fields, and the papers attributed to them"
        )
    if library.graph is not None:
        holdings.append("the co-authorship graph between principal investigators")
    if library.proposal:
        holdings.append("the cluster's funding proposal")
    lines.append(
        "The library holds "
        + "; ".join(holdings)
        + ". It holds nothing else: no h-index or other author metrics, no funding "
        "figures, no contact details, no data outside the cluster's own papers. When a "
        "question needs something that is not there, say so at once instead of searching "
        "for it. Quote counts and numbers exactly as the tools return them; never estimate "
        "a number a tool could have given you."
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
    if "count_papers" in tool_names:
        lines.append(
            "count_papers sizes many topics in one call, for 'how well covered is X' and "
            "'which of these topics have few papers'; search only the ones worth reading."
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
    if "find_experts" in tool_names:
        lines.append(
            "\nfind_experts answers 'who could help me with this' by what people have "
            "published, which is what a profile usually leaves out. Prefer it over search_pis "
            "for a method or a technique, and use search_pis for a name or a stated field."
        )
    if "most_collaborative_papers" in tool_names:
        lines.append(
            "most_collaborative_papers ranks papers by how many of the cluster's principal "
            "investigators are among the authors, for 'which paper joins the most groups', "
            "and its by_year counts are the measure of collaboration over time."
        )
    if "list_pis" in tool_names:
        lines.append(
            "list_pis returns every principal investigator with their focus and application "
            "fields in one call. Use it, not repeated searches, for anything about the groups "
            "as a whole: which groups name a topic, how many work on something, what the "
            "group descriptions cover."
        )
    if {"get_collaborators", "collaboration_centrality"} & tool_names:
        lines.append(
            "\nThe collaboration tools answer questions search cannot: who publishes with whom "
            "(get_collaborators), which papers two people share (joint_papers), who bridges "
            "otherwise separate groups or has the most collaborators (collaboration_centrality, "
            "ranked by betweenness, collaborators or shared_papers), and which groups cluster "
            "together (collaboration_communities). You cannot draw: for a picture of the "
            "network point to the Collaboration Graph page of this interface, and for the "
            "landscape of topics to its Publication Map page."
        )
    if "search_nomad" in tool_names:
        lines.append(
            "\nsearch_nomad searches a public materials-data repository outside the cluster. "
            "Report total_matches first, since the entries returned are only a sample, and "
            "never present an entry as the data behind a cluster paper: there is no such link."
        )
    lines.append(
        "\nMost questions take one to three tool calls. Fill in every required argument: "
        "a call without them fails and costs a round. Never repeat a call you have already "
        "made; if a search returns nothing useful, try one differently worded search, then "
        "say what is missing. For 'list all' or 'how many' questions raise the limit rather "
        "than searching many times, and report the total count the tool returns. Stop "
        "searching once you can answer. Do not announce what you are about to search: "
        "write only the answer, and say what you found rather than what you looked for."
    )
    lines.append("Answer in the language of the question.")

    if library.proposal and library.proposal.summary:
        lines.append(
            "\nThe following is the summary of the cluster's own funding proposal, as "
            "background. It states what the cluster set out to do, not what its papers "
            "found: when asked about the papers, answer from the papers, and say when a "
            "point comes from the proposal instead.\n\n<proposal_summary>\n"
            + library.proposal.summary[:PROPOSAL_CHARS]
            + "\n</proposal_summary>"
        )
    return "\n".join(lines)
