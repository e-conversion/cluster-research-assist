"""Which callers may reach which tool."""

from cra.core.tools.tiers import Tier


def test_every_tool_is_registered_with_a_tier(registry):
    by_tier = {spec.name: spec.tier for spec in registry}
    assert by_tier == {
        "collaboration_centrality": Tier.PUBLIC,
        "collaboration_communities": Tier.PUBLIC,
        "count_papers": Tier.PUBLIC,
        "get_collaborators": Tier.PUBLIC,
        "get_paper_by_doi": Tier.PUBLIC,
        "locate_paper_by_doi": Tier.PUBLIC,
        "get_paper_fulltext": Tier.INTERNAL,
        "get_pi": Tier.PUBLIC,
        "find_experts": Tier.PUBLIC,
        "get_proposal_fulltext": Tier.INTERNAL,
        "get_similar_papers": Tier.PUBLIC,
        "joint_papers": Tier.PUBLIC,
        "library_status": Tier.PUBLIC,
        "list_papers": Tier.PUBLIC,
        "list_pis": Tier.PUBLIC,
        "most_collaborative_papers": Tier.PUBLIC,
        "search_fulltext": Tier.INTERNAL,
        "search_nomad": Tier.PUBLIC,
        "search_papers": Tier.PUBLIC,
        "search_pis": Tier.PUBLIC,
        "semantic_search_papers": Tier.PUBLIC,
    }


def test_everything_that_touches_the_full_texts_is_internal(registry):
    public = {spec.name for spec in registry.specs(Tier.PUBLIC)}
    assert "get_paper_fulltext" not in public
    assert "get_proposal_fulltext" not in public
    assert "search_fulltext" not in public, (
        "passages around a query reconstruct the text, one query at a time"
    )
