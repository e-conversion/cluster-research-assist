from pathlib import Path

import pytest
from pydantic import ValidationError

from cra.config.settings import Settings, unknown_keys

REPO = Path(__file__).resolve().parent.parent


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
        {"CRA_AUTH_PROVIDER": "oidc"},
        {"CRA_AUTH_PROVIDER": "saml"},
        {"CRA_PORT": "0"},
        {"CRA_BRAND_DIR": "/no/such/brand"},
    ],
    ids=[
        "relative base path",
        "oidc without client",
        "unknown auth",
        "port",
        "missing brand directory",
    ],
)
def test_invalid_configuration_is_rejected(monkeypatch, env):
    monkeypatch.setenv("CRA_LIBRARY_PATH", "/c")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        Settings.load(None)


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


def test_dev_auth_off_loopback_needs_an_explicit_opt_in(monkeypatch):
    """The dev provider signs anyone in: a container that binds 0.0.0.0 with
    it is an open door unless the operator said so."""
    monkeypatch.setenv("CRA_LIBRARY_PATH", "/c")
    monkeypatch.setenv("CRA_HOST", "0.0.0.0")
    with pytest.raises(ValidationError, match="CRA_AUTH_DEV_INSECURE"):
        Settings.load(None)
    monkeypatch.setenv("CRA_AUTH_DEV_INSECURE", "true")
    assert Settings.load(None).auth_dev_insecure is True
