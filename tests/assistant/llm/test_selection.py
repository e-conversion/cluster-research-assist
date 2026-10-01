"""Which models are offered, and which one answers."""

import httpx
import respx
from conftest import make_settings

from cra.assistant.llm.selection import ModelCatalogue, offered, resolve

MODELS = ["qwen3.8-27b", "glm-5.3-flash"]


@respx.mock
async def test_openrouter_picks_the_cheapest_model_that_can_call_tools(tmp_path):
    settings = make_settings(
        tmp_path,
        llm_provider="openrouter",
        llm_base_url="https://openrouter.test/api/v1",
        llm_api_key="a-key",
    )
    good = {
        "id": "cheap/model",
        "pricing": {"prompt": "0.0000002", "completion": "0.0000002"},
        # most models carry only the intelligence index, so one of the two is enough
        "benchmarks": {
            "artificial_analysis": {"agentic_index": None, "intelligence_index": 40}
        },
        "supported_parameters": ["tools"],
    }
    respx.get("https://openrouter.test/api/v1/models/user").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "cheap/model"},
                    {"id": "no/tools"},
                    {"id": "free/model:free"},
                ]
            },
        )
    )
    respx.get("https://openrouter.test/api/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        **good,
                        "id": "dear/model",
                        "pricing": {"prompt": "0.9", "completion": "0.9"},
                    },
                    good,
                    {**good, "id": "no/tools", "supported_parameters": []},
                    {
                        **good,
                        "id": "unbenchmarked/model",
                        "pricing": {"prompt": "0.0000001", "completion": "0.0000001"},
                        "benchmarks": {},
                    },
                    {**good, "id": "free/model:free"},
                ]
            },
        )
    )
    async with httpx.AsyncClient() as http:
        choice = await resolve(settings, ModelCatalogue(settings), http)
    # unbenchmarked/model is cheaper but nothing says it can do the job
    assert choice.model == "cheap/model"
    assert choice.automatic is True


@respx.mock
async def test_an_unreachable_catalogue_falls_back_to_the_configured_model(tmp_path):
    settings = make_settings(
        tmp_path,
        llm_provider="openrouter",
        llm_base_url="https://openrouter.test/api/v1",
        llm_model="fallback/model",
        llm_api_key="a-key",
    )
    respx.get("https://openrouter.test/api/v1/models/user").mock(
        side_effect=httpx.ConnectError("down")
    )
    async with httpx.AsyncClient() as http:
        choice = await resolve(settings, ModelCatalogue(settings), http)
    assert choice.model == "fallback/model"


def test_the_configured_model_is_always_offered(tmp_path):
    settings = make_settings(tmp_path, llm_model="house/model", llm_models=["other"])
    assert offered(settings) == ["house/model", "other"]


@respx.mock
async def test_the_catalogue_models_are_offered_when_none_are_named(tmp_path):
    """Otherwise a deployment that leaves the choice automatic can only pick
    between "auto" and the fallback."""
    from cra.assistant.llm.selection import available

    settings = make_settings(
        tmp_path,
        llm_provider="openrouter",
        llm_base_url="https://openrouter.test/api/v1",
        llm_api_key="a-key",
        llm_model="fallback/model",
        llm_models=[],
    )
    entry = {
        "pricing": {"prompt": "0.0000002", "completion": "0.0000002"},
        "benchmarks": {"artificial_analysis": {"intelligence_index": 50}},
        "supported_parameters": ["tools"],
    }
    respx.get("https://openrouter.test/api/v1/models/user").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "a/one"}, {"id": "b/two"}]}
        )
    )
    respx.get("https://openrouter.test/api/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        **entry,
                        "id": "b/two",
                        "pricing": {"prompt": "0.9", "completion": "0.9"},
                    },
                    {**entry, "id": "a/one"},
                ]
            },
        )
    )
    async with httpx.AsyncClient() as http:
        choices = await available(settings, [], ModelCatalogue(settings), http)
    assert choices == ["a/one"], "too dear to run is not a choice"


async def test_a_named_list_is_the_whole_choice(tmp_path):
    from cra.assistant.llm.selection import available

    settings = make_settings(tmp_path, llm_models=MODELS, llm_model=MODELS[0])
    assert await available(settings, MODELS) == MODELS


@respx.mock
async def test_a_model_from_the_catalogue_can_be_chosen(tmp_path):
    """Choosing one must beat the automatic pick, or the picker is decoration."""
    settings = make_settings(
        tmp_path,
        llm_provider="openrouter",
        llm_base_url="https://openrouter.test/api/v1",
        llm_api_key="a-key",
        llm_model="fallback/model",
        llm_models=[],
    )
    entry = {
        "pricing": {"prompt": "0.0000002", "completion": "0.0000002"},
        "benchmarks": {"artificial_analysis": {"intelligence_index": 50}},
        "supported_parameters": ["tools"],
    }
    respx.get("https://openrouter.test/api/v1/models/user").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "a/cheap"}, {"id": "b/dearer"}]}
        )
    )
    respx.get("https://openrouter.test/api/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        **entry,
                        "id": "b/dearer",
                        "pricing": {"prompt": "0.0000009", "completion": "0.0000009"},
                    },
                    {**entry, "id": "a/cheap"},
                ]
            },
        )
    )
    async with httpx.AsyncClient() as http:
        catalogue = ModelCatalogue(settings)
        automatic = await resolve(settings, catalogue, http)
        picked = await resolve(settings, catalogue, http, wanted="b/dearer")
    assert (automatic.model, automatic.automatic) == ("a/cheap", True)
    assert (picked.model, picked.automatic) == ("b/dearer", False)
