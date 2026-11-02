"""The pipeline map: the package's own, kept in step with its tools and files,
and tailored to what a deployment runs."""

import importlib

import pytest
from conftest import make_settings, sign_in
from pydantic import ValidationError

from cra.app.web import pipeline
from cra.app.web.factory import create_app
from cra.config.settings import Settings
from cra.core.library.library import FILES
from cra.core.tools.registry import MODULE_PACKAGE, SPEC_ATTR


def every_tool() -> set[str]:
    """Every tool the package declares, whether or not a library switches it on."""
    names = set()
    for module in Settings.model_fields["tool_modules"].default:
        for member in vars(
            importlib.import_module(f"{MODULE_PACKAGE}.{module}")
        ).values():
            if spec := getattr(member, SPEC_ATTR, None):
                names.add(spec.name)
    return names


def ids(served):
    return {n["id"] for n in served["nodes"]}


def test_every_tool_has_a_box_and_every_tool_box_a_tool():
    assert pipeline.tool_ids(pipeline.load()) == every_tool()


def test_every_library_file_has_a_box():
    assert set(FILES) | {"manifest"} <= ids(pipeline.load().model_dump())


def test_a_tool_the_deployment_did_not_register_is_left_out(tmp_path):
    served = pipeline.served(make_settings(tmp_path), {"search_papers"}, None)
    assert "search_papers" in ids(served)
    assert "get_paper_fulltext" not in ids(served)
    # and no wire is left pointing at it
    assert all(a in ids(served) and b in ids(served) for a, b in served["edges"])


@pytest.mark.parametrize(
    ("overrides", "node", "shown"),
    [
        ({}, "d_mcp", False),
        ({"mcp_server_enabled": True}, "d_mcp", True),
        ({}, "r_elab", False),
        ({"mcp_elab_url": "https://elab.example/mcp"}, "r_elab", True),
        ({"mcp_elab_url": "https://elab.example/mcp"}, "r_datatagger", False),
    ],
)
def test_what_the_configuration_switches_off_is_left_out(
    tmp_path, overrides, node, shown
):
    served = pipeline.served(make_settings(tmp_path, **overrides), set(), None)
    assert (node in ids(served)) == shown


def test_the_counts_and_the_website_come_from_the_deployment(tmp_path):
    settings = make_settings(tmp_path, cluster_website="https://cluster.example")
    served = pipeline.served(settings, set(), {"papers": 1432})
    by_id = {n["id"]: n for n in served["nodes"]}
    assert by_id["papers"]["summary"] == "required · 1,432 entries"
    assert by_id["src_publications"]["summary"] == "https://cluster.example"


def test_an_edge_to_no_box_is_refused():
    broken = pipeline.load().model_dump()
    broken["edges"].append(("papers", "nowhere"))
    with pytest.raises(ValidationError, match="unknown nodes"):
        pipeline.Pipeline.model_validate(broken)


async def test_signed_in_users_get_the_map_of_this_deployment(tmp_path):
    app = create_app(make_settings(tmp_path))
    async with app.test_app():
        client = app.test_client()
        await sign_in(app, client, "ada")
        served = await (await client.get("/api/pipeline")).get_json()
        registered = {spec.name for spec in app.extensions["cra"].registry}
    assert pipeline.tool_ids(pipeline.load()) & ids(served) == registered
