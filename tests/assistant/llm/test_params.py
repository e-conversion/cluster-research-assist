"""The request fields a deployment and a session's choices produce."""

from conftest import make_settings

from cra.assistant.llm import params as params_


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
        "quantizations": ["fp8", "fp16", "bf16", "fp32", "unknown"],
    }
    assert fields["reasoning"] == {"effort": "low"}
    body, rest = params_.split(fields)
    assert set(body) == {"provider", "reasoning"}
    assert set(rest) == {"max_tokens"}


def test_openrouter_routing_can_be_narrowed_or_opened(tmp_path):
    narrowed = make_settings(
        tmp_path, llm_provider="openrouter", openrouter_ignore_providers="Sail Research"
    )
    provider = params_.request_fields(narrowed)["provider"]
    assert provider["ignore"] == ["Sail Research"]

    opened = make_settings(
        tmp_path, llm_provider="openrouter", openrouter_quantizations=""
    )
    provider = params_.request_fields(opened)["provider"]
    assert "quantizations" not in provider
    assert "ignore" not in provider
