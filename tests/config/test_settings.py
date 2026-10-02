from pathlib import Path

import pytest
from pydantic import ValidationError

from cra.config.settings import Settings, unknown_keys

REPO = Path(__file__).resolve().parents[2]


def write_env(path: Path, **keys: str) -> Path:
    path.write_text("".join(f"CRA_{k.upper()}={v}\n" for k, v in keys.items()))
    return path


def test_env_example_lists_every_field_and_loads():
    keys = {
        line.split("=", 1)[0]
        for line in (REPO / ".env.example").read_text().splitlines()
        if line.startswith("CRA_")
    }
    assert keys == {f"CRA_{name.upper()}" for name in Settings.model_fields}
    settings = Settings.load(REPO / ".env.example")
    assert settings.library_path == Path("library")
    assert settings.tool_modules == [
        "papers",
        "pis",
        "proposal",
        "graph",
        "nomad",
        "status",
    ]


def test_environment_wins_over_file(tmp_path, monkeypatch):
    env = write_env(tmp_path / ".env", library_path="/from/file", port="1")
    monkeypatch.setenv("CRA_PORT", "2")
    settings = Settings.load(env)
    assert (settings.library_path, settings.port) == (Path("/from/file"), 2)


def test_missing_file_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("CRA_LIBRARY_PATH", "/c")
    assert Settings.load(tmp_path / "absent").library_path == Path("/c")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("a,b", ["a", "b"]), (" a , ,b ", ["a", "b"]), ("", [])],
)
def test_comma_lists(monkeypatch, raw, expected):
    monkeypatch.setenv("CRA_LIBRARY_PATH", "/c")
    monkeypatch.setenv("CRA_LLM_MODELS", raw)
    assert Settings.load(None).llm_models == expected


def test_default_model_is_offered_in_the_ui_list(monkeypatch):
    monkeypatch.setenv("CRA_LIBRARY_PATH", "/c")
    monkeypatch.setenv("CRA_LLM_MODEL", "m0")
    monkeypatch.setenv("CRA_LLM_MODELS", "m1,m2")
    assert Settings.load(None).llm_models == ["m0", "m1", "m2"]


@pytest.mark.parametrize(
    ("raw", "expected"), [("", ""), ("/a/b/", "/a/b"), ("  /a  ", "/a")]
)
def test_base_path_is_normalised(monkeypatch, raw, expected):
    monkeypatch.setenv("CRA_LIBRARY_PATH", "/c")
    monkeypatch.setenv("CRA_BASE_PATH", raw)
    assert Settings.load(None).base_path == expected


@pytest.mark.parametrize(
    "env",
    [
        {"CRA_BASE_PATH": "nomad"},
        {"CRA_OIDC_ISSUER": "https://oidc-testproxy.aai.dfn.de"},
        {"CRA_OIDC_CLIENT_SECRET": "s", "CRA_OIDC_CLIENT_ID": "c"},
        {"CRA_PORT": "0"},
        {"CRA_BRAND_DIR": "/no/such/brand"},
        {"CRA_PUBLIC_URL": "atlas.example.org"},
        {"CRA_PUBLIC_URL": "https://atlas.example.org/cra"},
        {"CRA_PUBLIC_URL": "http://atlas.example.org"},
    ],
    ids=[
        "relative base path",
        "oidc issuer without client",
        "oidc client without issuer",
        "port",
        "missing brand directory",
        "public url without scheme",
        "public url with a path",
        "public url over plain http",
    ],
)
def test_invalid_configuration_is_rejected(monkeypatch, env):
    monkeypatch.setenv("CRA_LIBRARY_PATH", "/c")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        Settings.load(None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://atlas.example.org/", "https://atlas.example.org"),
        ("http://localhost:8000", "http://localhost:8000"),
    ],
)
def test_public_url_is_an_origin(monkeypatch, raw, expected):
    monkeypatch.setenv("CRA_LIBRARY_PATH", "/c")
    monkeypatch.setenv("CRA_PUBLIC_URL", raw)
    assert Settings.load(None).public_url == expected


@pytest.mark.parametrize(
    ("env", "enabled"),
    [
        ({"CRA_PUBLIC_URL": "https://a.example"}, True),
        ({}, False),
        (
            {
                "CRA_PUBLIC_URL": "https://a.example",
                "CRA_MCP_SERVER_REQUIRE_TOKEN": "false",
            },
            False,
        ),
        (
            {"CRA_PUBLIC_URL": "https://a.example", "CRA_MCP_SERVER_ENABLED": "false"},
            False,
        ),
    ],
    ids=["configured", "no public url", "no tokens asked for", "no endpoint"],
)
def test_mcp_sign_in_needs_the_endpoint_tokens_and_an_address(
    monkeypatch, env, enabled
):
    monkeypatch.setenv("CRA_LIBRARY_PATH", "/c")
    monkeypatch.setenv("CRA_MCP_SERVER_ENABLED", "true")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert Settings.load(None).mcp_oauth_enabled is enabled


def test_library_path_is_required():
    with pytest.raises(ValidationError, match="library_path"):
        Settings.load(None)


def test_dump_redacts_secrets_only_when_set(monkeypatch):
    monkeypatch.setenv("CRA_LIBRARY_PATH", "/c")
    monkeypatch.setenv("CRA_LLM_API_KEY", "hunter2")
    monkeypatch.setenv("CRA_LLM_MODELS", "a,b")
    dump = Settings.load(None).dump()
    assert dump["CRA_LLM_API_KEY"] == "***"
    assert dump["CRA_OIDC_CLIENT_SECRET"] == ""
    assert dump["CRA_LLM_MODELS"] == "a,b"
    assert dump["CRA_COOKIE_SECURE"] == "true"
    assert "hunter2" not in "".join(dump.values())


def test_unknown_keys_flags_typos_but_ignores_other_prefixes(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("CRA_LLM_MODLE=x\nMCP_JWT_SECRET=y\n")
    monkeypatch.setenv("CRA_PORTT", "1")
    assert unknown_keys(env) == ["CRA_LLM_MODLE", "CRA_PORTT"]


OIDC_KEYS = {
    "CRA_OIDC_ISSUER": "https://oidc-testproxy.aai.dfn.de",
    "CRA_OIDC_CLIENT_ID": "client",
    "CRA_OIDC_CLIENT_SECRET": "secret",
    "CRA_OIDC_REDIRECT_URI": "https://example.org/auth/callback",
}


@pytest.mark.parametrize(("keys", "enabled"), [({}, False), (OIDC_KEYS, True)])
def test_institutional_sign_in_is_on_only_when_fully_configured(
    monkeypatch, keys, enabled
):
    monkeypatch.setenv("CRA_LIBRARY_PATH", "/c")
    for key, value in keys.items():
        monkeypatch.setenv(key, value)
    assert Settings.load(None).oidc_enabled is enabled


def test_a_partial_oidc_configuration_names_what_is_missing(monkeypatch):
    monkeypatch.setenv("CRA_LIBRARY_PATH", "/c")
    monkeypatch.setenv("CRA_OIDC_ISSUER", OIDC_KEYS["CRA_OIDC_ISSUER"])
    monkeypatch.setenv("CRA_OIDC_CLIENT_ID", OIDC_KEYS["CRA_OIDC_CLIENT_ID"])
    with pytest.raises(ValidationError) as exc:
        Settings.load(None)
    assert "CRA_OIDC_CLIENT_SECRET, CRA_OIDC_REDIRECT_URI" in str(exc.value)


def test_a_source_token_key_must_be_a_fernet_key(monkeypatch):
    monkeypatch.setenv("CRA_LIBRARY_PATH", "/c")
    monkeypatch.setenv("CRA_SOURCE_TOKEN_KEY", "not-a-key")
    with pytest.raises(ValidationError, match="cra secret-key"):
        Settings.load(None)
