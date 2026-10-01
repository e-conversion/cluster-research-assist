"""The NOMAD search tool."""

import json

import httpx
import respx
from conftest import call_tool


@respx.mock
async def test_search_nomad_asks_the_repository_and_summarises(registry, ctx, settings):
    route = respx.post(f"{settings.nomad_base_url}/entries/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "pagination": {"total": 412},
                "data": [
                    {
                        "entry_id": "abc",
                        "upload_name": "TiO2 runs",
                        "entry_type": "Simulation",
                        "authors": [{"name": "Ada Lovelace"}],
                        "results": {
                            "material": {
                                "chemical_formula_reduced": "O2Ti",
                                "elements": ["O", "Ti"],
                            }
                        },
                    }
                ],
            },
        )
    )
    found = await call_tool(
        registry, ctx, "search_nomad", elements="Ti,O", author="Prof. Dr. Ada Lovelace"
    )
    assert found["total_matches"] == 412
    assert found["entries"][0]["url"].endswith("abc")
    sent = json.loads(route.calls.last.request.read())
    assert sent["query"] == {
        "results.material.elements": {"all": ["Ti", "O"]},
        # NOMAD stores a depositor's name without a title
        "authors.name": "Ada Lovelace",
    }
    assert sent["pagination"]["order_by"] == "upload_create_time"


@respx.mock
async def test_nomad_failures_are_reported_not_raised(registry, ctx, settings):
    respx.post(f"{settings.nomad_base_url}/entries/query").mock(
        return_value=httpx.Response(422)
    )
    assert (
        "rejected"
        in (await call_tool(registry, ctx, "search_nomad", formula="nonsense"))["error"]
    )
    assert "at least one" in (await call_tool(registry, ctx, "search_nomad"))["error"]


@respx.mock
async def test_nomad_being_unreachable_is_reported(registry, ctx, settings):
    respx.post(f"{settings.nomad_base_url}/entries/query").mock(
        side_effect=httpx.ConnectError("no route")
    )
    assert (
        "not reachable"
        in (await call_tool(registry, ctx, "search_nomad", text="water"))["error"]
    )
