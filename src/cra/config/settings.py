"""All configuration. One flat model over ``.env`` and the environment.

Every key is ``CRA_<FIELD>``. Real environment variables win over the file.
Nothing else in the package reads ``os.environ``.
"""

import os
from pathlib import Path
from typing import Annotated, Any, Literal

from dotenv import dotenv_values
from pydantic import BeforeValidator, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ENV_PREFIX = "CRA_"
REDACTED = "***"


def _split_commas(value: Any) -> Any:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


CommaList = Annotated[list[str], NoDecode, BeforeValidator(_split_commas)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX, env_file_encoding="utf-8", extra="ignore"
    )

    # cluster identity
    cluster_name: str = "cluster"
    cluster_display_name: str = "Cluster Research Assistant"
    cluster_description: str = "a research cluster"
    cluster_website: str = ""
    cluster_funding_body: str = ""
    cluster_host_institutions: CommaList = []

    # server
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    base_path: str = ""
    cookie_secure: bool = True
    session_max_age_hours: float = Field(default=12, gt=0)

    # corpus
    corpus_path: Path
    corpus_require_schema: str = "1.x"

    # tools
    tool_modules: CommaList = ["papers", "pis", "proposal", "graph", "status", "nomad"]
    fulltext_snippet_chars: int = Field(default=200, gt=0)
    fulltext_max_snippets: int = Field(default=5, gt=0)

    # LLM, one active provider
    llm_provider: str = "gwdg"
    llm_base_url: str = "https://chat-ai.academiccloud.de/v1"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_models: CommaList = []
    llm_max_tool_rounds: int = Field(default=10, ge=1)
    llm_max_tokens: int = Field(default=8192, ge=1)
    llm_max_context_tokens: int = Field(default=64000, ge=1)
    openrouter_max_price_per_mtok: float = Field(default=1.0, ge=0)
    openrouter_min_agentic_index: float = Field(default=35.0, ge=0)

    # auth
    auth_provider: Literal["dev", "oidc"] = "dev"
    # addresses that are made admin when they sign in; never demotes anyone
    auth_admins: CommaList = []
    auth_dev_user: str = ""
    auth_user_header: str = ""
    auth_admin_contact: str = ""
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")
    oidc_redirect_uri: str = ""
    oidc_scopes: str = "openid email profile"

    # what an unauthenticated visitor may do
    anonymous_chat_enabled: bool = True
    anonymous_chat_daily_limit: int = Field(default=20, ge=1)
    user_chat_daily_limit: int = Field(default=0, ge=0)

    # outward MCP surface
    mcp_server_enabled: bool = True
    mcp_server_path: str = "/mcp"
    mcp_server_require_token: bool = False
    mcp_server_rate_limit: int = Field(default=60, ge=1)
    mcp_token_secret: SecretStr = SecretStr("")

    # history
    history_url: str = "sqlite+aiosqlite:///./cra.sqlite"
    history_retention_days: int = Field(default=365, ge=1)
    history_auto_migrate: bool = False

    # external MCP servers this instance consumes
    mcp_elab_url: str = ""
    mcp_elab_register_url: str = ""
    mcp_datatagger_url: str = ""
    mcp_datatagger_register_url: str = ""
    mcp_pool_idle_s: float = Field(default=600, gt=0)

    # connectors
    nomad_base_url: str = "https://nomad-lab.eu/prod/v1/api/v1"
    nomad_gui_url: str = "https://nomad-lab.eu/prod/v1/gui/entry/id/{}"

    # query encoder
    encoder_path: Path | None = None
    encoder_model: str = "BAAI/bge-small-en-v1.5"

    # logging
    log_dir: Path = Path("logs")
    log_tool_args: bool = False

    @field_validator("base_path", "mcp_server_path")
    @classmethod
    def _normalise_path(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.startswith("/"):
            raise ValueError("must start with '/'")
        return value

    @model_validator(mode="after")
    def _check_cross_field(self) -> "Settings":
        if self.auth_provider == "oidc":
            missing = [
                name
                for name in ("oidc_issuer", "oidc_client_id", "oidc_redirect_uri")
                if not getattr(self, name)
            ]
            if not self.oidc_client_secret.get_secret_value():
                missing.append("oidc_client_secret")
            if missing:
                raise ValueError(
                    "auth_provider=oidc needs "
                    + ", ".join(f"CRA_{m.upper()}" for m in missing)
                )
        if (
            self.mcp_server_require_token
            and not self.mcp_token_secret.get_secret_value()
        ):
            raise ValueError("CRA_MCP_SERVER_REQUIRE_TOKEN needs CRA_MCP_TOKEN_SECRET")
        if self.llm_model and self.llm_models and self.llm_model not in self.llm_models:
            self.llm_models.insert(0, self.llm_model)
        return self

    @classmethod
    def load(cls, env_file: Path | None = Path(".env")) -> "Settings":
        """Settings from the environment plus ``env_file`` (skipped when missing)."""
        if env_file is not None and not env_file.exists():
            env_file = None
        return cls(_env_file=env_file)  # type: ignore[call-arg]

    def dump(self, redact: bool = True) -> dict[str, str]:
        """``CRA_KEY -> value`` for every field, secrets replaced unless ``redact`` is off."""
        out: dict[str, str] = {}
        for name, value in self.model_dump().items():
            key = ENV_PREFIX + name.upper()
            if isinstance(value, SecretStr):
                secret = value.get_secret_value()
                out[key] = (REDACTED if secret else "") if redact else secret
            elif isinstance(value, list):
                out[key] = ",".join(value)
            elif value is None:
                out[key] = ""
            elif isinstance(value, bool):
                out[key] = "true" if value else "false"
            else:
                out[key] = str(value)
        return out


def unknown_keys(env_file: Path | None = Path(".env")) -> list[str]:
    """``CRA_``-prefixed keys in the file or the environment that match no field."""
    known = {ENV_PREFIX + name.upper() for name in Settings.model_fields}
    seen: set[str] = set(os.environ)
    if env_file is not None and env_file.exists():
        seen.update(dotenv_values(env_file))
    return sorted(k for k in seen if k.startswith(ENV_PREFIX) and k not in known)
