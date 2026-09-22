"""Choosing a model and the parameters that go with it."""

import httpx
import pytest
import respx
from conftest import make_settings

from cra.app.web.factory import create_app
from cra.assistant.llm import params as params_
from cra.assistant.llm.selection import ModelCatalogue, offered, resolve

MODELS = ["qwen3.8-27b", "glm-5.3-flash"]


@pytest.fixture
async def app(tmp_path):
    app = create_app(make_settings(tmp_path, llm_model=MODELS[0], llm_models=MODELS))
    async with app.test_app():
        yield app


@pytest.fixture
async def client(app):
    client = app.test_client()
    await client.get("/auth/login")
    return client


async def session_of(client):
    return await (await client.get("/api/session")).get_json()


async def test_a_session_starts_on_the_configured_model(client):
    body = await session_of(client)
    assert body["model"] == MODELS[0]
    assert body["auto_model"] is False
    assert body["params"]["max_tokens"] == "8192"


async def test_the_offered_models_reach_the_interface(client):
    config = await (await client.get("/api/config")).get_json()
    assert config["models"] == MODELS
    assert config["openrouter"] is False
    assert [r["value"] for r in config["routes"]] == ["price", "throughput", "latency"]
    keys = [f["key"] for f in config["parameters"]["spec"]]
    assert keys == list(params_.KEYS)
    routing = next(
        f for f in config["parameters"]["spec"] if f["key"] == "provider_sort"
    )
    assert routing["hidden"] is True


async def test_picking_a_model_sticks_to_the_session(client, app):
    response = await client.post("/api/session/model", json={"model": MODELS[1]})
    assert (await response.get_json())["model"] == MODELS[1]
    assert (await session_of(client))["model"] == MODELS[1]

    # a second browser is unaffected
    other = app.test_client()
    await other.get("/auth/login")
    assert (await session_of(other))["model"] == MODELS[0]


async def test_an_unknown_model_is_refused(client):
    response = await client.post("/api/session/model", json={"model": "gpt-9"})
    assert response.status_code == 400
    assert (await session_of(client))["model"] == MODELS[0]


async def test_parameters_are_stored_and_reset(client):
    response = await client.post(
        "/api/session/params", json={"params": {"top_p": "0.9", "max_tokens": "2048"}}
    )
    body = await response.get_json()
    assert body["params"]["top_p"] == "0.9"
    assert body["params"]["max_tokens"] == "2048"
    assert (await session_of(client))["params"]["top_p"] == "0.9"

    cleared = await (await client.delete("/api/session/params")).get_json()
    assert cleared["params"]["top_p"] == ""
    assert cleared["params"]["max_tokens"] == "8192"


async def stored_params(app, client):
    from cra.app.web.sessions import COOKIE_NAME

    cookie = next(c for c in client.cookie_jar if c.name == COOKIE_NAME)
    state = await app.extensions["cra"].sessions.load(cookie.value)
    return state.data.get("params", {})


async def test_only_what_differs_from_the_deployment_is_kept(client, app):
    await client.post(
        "/api/session/params", json={"params": {"max_tokens": "8192", "top_p": "0.9"}}
    )
    # 8192 is what the deployment already sets, so only the real change is kept
    assert await stored_params(app, client) == {"top_p": "0.9"}


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"params": {"top_p": "3"}}, "must be one of"),
        ({"params": {"max_tokens": "10"}}, "between"),
        ({"params": {"max_tokens": "lots"}}, "number"),
        ({"params": {"nonsense": "1"}}, "Unknown parameter"),
        ({"params": "not an object"}, "under 'params'"),
    ],
)
async def test_invalid_parameters_are_refused(client, payload, message):
    response = await client.post("/api/session/params", json=payload)
    assert response.status_code == 400
    assert message in (await response.get_json())["error"]


def test_the_request_fields_sent_to_a_plain_gateway(tmp_path):
    settings = make_settings(tmp_path, llm_provider="gwdg")
    fields = params_.request_fields(
        settings, {"top_p": "0.9", "reasoning_effort": "high"}
    )
    assert fields == {"top_p": 0.9, "max_tokens": 8192}, (
        "OpenRouter-only fields stay away"
    )
    body, rest = params_.split(fields)
    assert body == {}
    assert rest == fields


def test_openrouter_always_gets_the_privacy_routing(tmp_path):
    settings = make_settings(tmp_path, llm_provider="openrouter")
    fields = params_.request_fields(
        settings, {"reasoning_effort": "low", "provider_sort": "latency"}
    )
    assert fields["provider"] == {
        "sort": "latency",
        "zdr": True,
        "data_collection": "deny",
        "max_price": {"prompt": 1.0, "completion": 1.0},
    }
    assert fields["reasoning"] == {"effort": "low"}
    body, rest = params_.split(fields)
    assert set(body) == {"provider", "reasoning"}
    assert set(rest) == {"max_tokens"}


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


async def test_the_landing_page_is_offered_questions_worth_asking(client):
    from cra.app.web.examples import QUESTIONS, SHOWN

    config = await (await client.get("/api/config")).get_json()
    assert len(config["examples"]) == SHOWN
    assert set(config["examples"]) <= set(QUESTIONS)
    # a different few each time, so the page does not always suggest the same
    seen = {
        tuple(sorted((await (await client.get("/api/config")).get_json())["examples"]))
        for _ in range(12)
    }
    assert len(seen) > 1
