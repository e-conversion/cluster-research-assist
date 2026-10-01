"""The collaboration-graph tools."""

from conftest import call_tool


async def test_the_graph_answers_who_works_with_whom(registry, ctx):
    collaborators = await call_tool(
        registry, ctx, "get_collaborators", pi_query="hopper"
    )
    assert collaborators["collaborator_count"] == 2
    joint = await call_tool(
        registry, ctx, "joint_papers", pi_a="lovelace", pi_b="hopper"
    )
    assert joint["count"] == 2
    assert [p["doi"] for p in joint["papers"]] == ["10.1000/alpha", "10.1000/beta"]
    assert joint["papers"][0]["title"], "a title saves a lookup per paper"
    assert joint["not_in_library"] == []
    central = await call_tool(registry, ctx, "collaboration_centrality", limit=1)
    assert central["ranked_by"] == "betweenness"
    assert central["results"][0]["name"] == "Prof. Dr. Grace Hopper"
    assert central["results"][0]["collaborators"] == 2
    assert central["results"][0]["shared_papers"] >= 2
    by_partners = await call_tool(
        registry, ctx, "collaboration_centrality", limit=3, by="collaborators"
    )
    counts = [row["collaborators"] for row in by_partners["results"]]
    assert counts == sorted(counts, reverse=True)
    assert (
        "not called correctly"
        in (await call_tool(registry, ctx, "collaboration_centrality", by="fame"))[
            "error"
        ]
    )
    assert (await call_tool(registry, ctx, "collaboration_communities"))[
        "community_count"
    ] == 1
    assert (
        "No principal"
        in (await call_tool(registry, ctx, "get_collaborators", pi_query="zz"))["error"]
    )
