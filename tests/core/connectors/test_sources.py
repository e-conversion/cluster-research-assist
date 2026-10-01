"""Source descriptors: what is offered, and what the browser is told."""

import json

from conftest import make_settings

from cra.core.connectors.sources import Source, configured

REGISTER_URL = "https://proxy.invalid/el/register"


ELAB = Source(
    kind="elab",
    label="eLabFTW",
    key_label="eLabFTW API key",
    url="https://proxy.invalid/el/mcp",
    register_url=REGISTER_URL,
    default_base_url="https://eln.invalid",
    profiles=(("h", "Hybrid"), ("r", "Read-only")),
)


def test_only_configured_sources_are_offered(tmp_path):
    assert configured(make_settings(tmp_path)) == {}
    sources = configured(
        make_settings(tmp_path, mcp_elab_url="https://p/el/mcp", mcp_datatagger_url="")
    )
    assert list(sources) == ["elab"]
    assert sources["elab"].profiles[0][0] == "h"


def test_the_token_travels_in_the_query_string():
    """elabmcp-proxy reads it there and nowhere else."""
    assert ELAB.authorised("a b/c") == "https://proxy.invalid/el/mcp?token=a%20b%2Fc"
    with_query = Source(**{**vars(ELAB), "url": "https://p/mcp?x=1"})
    assert with_query.authorised("t").endswith("?x=1&token=t")


def test_the_browser_is_told_nothing_internal():
    public = ELAB.public()
    assert public["profiles"] == [
        {"value": "h", "label": "Hybrid"},
        {"value": "r", "label": "Read-only"},
    ]
    assert REGISTER_URL not in str(public)
    assert ELAB.url not in str(public)


def test_the_shared_key_comes_from_the_settings(tmp_path):
    settings = make_settings(
        tmp_path,
        mcp_nomad_url="https://nm.invalid/nm/mcp",
        mcp_nomad_register_url="https://nm.invalid/nm/register",
        mcp_nomad_base_url="https://oasis.invalid/nomad-oasis/api/v1",
        mcp_nomad_token="s3cret",
        mcp_nomad_token_for="demo-venice, GXNjTyxJox8OqRMo",
    )
    source = configured(settings)["nomad"]
    assert (source.label, source.prefix) == ("NOMAD", "nomad_")
    assert source.shared_token == "s3cret"
    assert source.shared_token_for == ("demo-venice", "GXNjTyxJox8OqRMo")
    assert "s3cret" not in json.dumps(source.public())


def test_a_shared_key_without_a_url_is_not_offered(tmp_path):
    assert "nomad" not in configured(make_settings(tmp_path))
