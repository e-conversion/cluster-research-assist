"""The settings an administrator may change at runtime."""

import pytest
from conftest import make_settings

from cra.app.policy import Policy, PolicyError


def test_policy_rejects_unknown_keys_outside_the_registry(tmp_path):
    policy = Policy(make_settings(tmp_path))
    with pytest.raises(PolicyError, match="unknown setting"):
        policy.set("nope", 1)


def test_the_service_description_defaults_to_what_the_configuration_says(tmp_path):
    policy = Policy(
        make_settings(
            tmp_path,
            cluster_display_name="Atlas",
            cluster_description="the e-conversion cluster",
            cluster_funding_body="the DFG",
            cluster_host_institutions="TUM,LMU",
            cluster_website="https://www.e-conversion.de",
        )
    )
    text = policy["service_description"]
    assert text.startswith(
        "Atlas answers questions about the research of the e-conversion cluster, "
        "funded by the DFG, hosted at TUM and LMU."
    )
    assert "<https://www.e-conversion.de>" in text
    assert "- ask questions in a chat" in text
    # no DFN-AAI sign-in configured, so the text does not offer one
    assert "DFN-AAI" not in text
    assert policy.source("service_description") == "configuration"


def test_the_default_description_mentions_institutional_sign_in_when_there_is_one(
    tmp_path,
):
    settings = make_settings(
        tmp_path,
        oidc_issuer="https://idp.example.org",
        oidc_client_id="cra",
        oidc_client_secret="s",
        oidc_redirect_uri="https://cra.example.org/auth/callback",
    )
    assert "through DFN-AAI" in Policy(settings)["service_description"]


def test_a_written_service_description_keeps_its_lines_and_drops_trailing_space(
    tmp_path,
):
    policy = Policy(make_settings(tmp_path))
    stored = policy.set("service_description", "  # About  \r\n\r\nWe help.  \n")
    assert stored == "# About\n\nWe help."
    assert policy.source("service_description") == "database"
    policy.set("service_description", "")
    assert policy["service_description"] == ""
    policy.clear("service_description")
    assert policy["service_description"].startswith("Cluster Research Assistant")


def test_a_service_description_has_a_length_limit(tmp_path):
    policy = Policy(make_settings(tmp_path))
    policy.set("service_description", "x" * 4000)
    with pytest.raises(PolicyError, match="4000"):
        policy.set("service_description", "x" * 4001)


def test_only_the_service_description_is_edited_as_a_block(tmp_path):
    described = {e["key"]: e for e in Policy(make_settings(tmp_path)).describe()}
    assert described["service_description"]["multiline"] is True
    assert described["notice"]["multiline"] is False
