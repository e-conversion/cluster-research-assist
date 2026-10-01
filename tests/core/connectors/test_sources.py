"""Source descriptors: what is offered, and what the browser is told."""

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
