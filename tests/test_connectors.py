"""Source descriptors and the registration that trades a key for a token."""

import httpx
import pytest
import respx
from conftest import make_settings

from cra.core.connectors.registration import RegistrationError, register
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
PAGE = "<p>Your endpoint: https://proxy.invalid/el/mcp?token=eyJhbGci.payload-9_x</p>"


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


@respx.mock
async def test_registration_validates_before_it_mints():
    """Only the first step checks the key, so skipping it would connect a typo."""
    route = respx.post(REGISTER_URL).mock(
        side_effect=[httpx.Response(200, text="ok"), httpx.Response(200, text=PAGE)]
    )
    async with httpx.AsyncClient() as http:
        token = await register(
            http, ELAB, base_url="https://eln.invalid/", api_key="k", profile="r"
        )
    assert token == "eyJhbGci.payload-9_x"
    first, second = (
        dict(httpx.QueryParams(c.request.content.decode())) for c in route.calls
    )
    assert first == {
        "api_key": "k",
        "base_url": "https://eln.invalid",
        "validated": "0",
        "profile": "r",
    }
    assert second | {"validated": "0"} == first


@respx.mock
async def test_an_unknown_profile_falls_back_to_the_first():
    route = respx.post(REGISTER_URL).mock(
        side_effect=[httpx.Response(200, text="ok"), httpx.Response(200, text=PAGE)]
    )
    async with httpx.AsyncClient() as http:
        await register(
            http, ELAB, base_url="https://eln.invalid", api_key="k", profile="x"
        )
    sent = dict(httpx.QueryParams(route.calls[0].request.content.decode()))
    assert sent["profile"] == "h"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "rejected that API key"), (403, "no access"), (418, "check the address")],
)
@respx.mock
async def test_a_refusal_is_explained(status, expected):
    respx.post(REGISTER_URL).mock(return_value=httpx.Response(status, text="no"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(RegistrationError) as raised:
            await register(http, ELAB, base_url="https://eln.invalid", api_key="k")
    assert expected in str(raised.value)
    assert raised.value.status == 400


@respx.mock
async def test_a_page_without_a_token_is_not_a_success():
    respx.post(REGISTER_URL).mock(return_value=httpx.Response(200, text="<p>hello</p>"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(RegistrationError, match="Registration failed"):
            await register(http, ELAB, base_url="https://eln.invalid", api_key="k")


@respx.mock
async def test_an_unreachable_service_says_so():
    respx.post(REGISTER_URL).mock(side_effect=httpx.ConnectError("no route"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(RegistrationError) as raised:
            await register(http, ELAB, base_url="https://eln.invalid", api_key="k")
    assert raised.value.status == 502


async def test_the_form_needs_both_fields():
    async with httpx.AsyncClient() as http:
        with pytest.raises(RegistrationError, match="both required"):
            await register(http, ELAB, base_url="https://eln.invalid", api_key="  ")
