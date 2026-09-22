"""Operational settings an admin may change while the service runs.

Configuration and policy are different things. Endpoints, secrets and paths are
configuration: they live in the environment, and changing one needs a restart.
How the service behaves day to day is policy: which tools are on, which model
answers, how many questions a person may ask in a day. Each key below takes its
default from the configuration and may be overridden in the database, so a
deployment still describes itself fully through its `.env`.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cra.app.history.repository import Repository
from cra.config.settings import Settings

log = logging.getLogger(__name__)


class PolicyError(ValueError):
    pass


def _positive_int(value: Any) -> int:
    number = int(value)
    if number < 1:
        raise PolicyError("must be 1 or more")
    return number


def _non_negative_int(value: Any) -> int:
    number = int(value)
    if number < 0:
        raise PolicyError("must not be negative")
    return number


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if str(value).strip().lower() in ("1", "true", "yes", "on"):
        return True
    if str(value).strip().lower() in ("0", "false", "no", "off"):
        return False
    raise PolicyError("must be true or false")


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    items = [str(v).strip() for v in value if str(v).strip()]
    if len(set(items)) != len(items):
        raise PolicyError("must not repeat an entry")
    return items


def _text(value: Any) -> str:
    text = str(value).strip()
    if len(text) > 500:
        raise PolicyError("must be at most 500 characters")
    return text


@dataclass(frozen=True)
class PolicyKey:
    name: str
    setting: str | None
    coerce: Callable[[Any], Any]
    description: str
    fallback: Any = ""


KEYS: dict[str, PolicyKey] = {
    key.name: key
    for key in (
        PolicyKey(
            "tool_modules",
            "tool_modules",
            _string_list,
            "Tool modules that are loaded.",
        ),
        PolicyKey(
            "llm_model",
            "llm_model",
            str,
            "Model used when a visitor expresses no preference.",
        ),
        PolicyKey(
            "llm_models", "llm_models", _string_list, "Models offered in the interface."
        ),
        PolicyKey(
            "llm_max_tool_rounds",
            "llm_max_tool_rounds",
            _positive_int,
            "Tool rounds allowed per answer.",
        ),
        PolicyKey(
            "user_chat_daily_limit",
            "user_chat_daily_limit",
            _non_negative_int,
            "Questions per day for a signed-in user; 0 removes the limit.",
        ),
        PolicyKey(
            "fulltext_snippet_chars",
            "fulltext_snippet_chars",
            _positive_int,
            "Characters per snippet on the public surface.",
        ),
        PolicyKey(
            "fulltext_max_snippets",
            "fulltext_max_snippets",
            _positive_int,
            "Snippets per call on the public surface.",
        ),
        PolicyKey(
            "mcp_server_rate_limit",
            "mcp_server_rate_limit",
            _positive_int,
            "Calls per minute on the outward MCP endpoint.",
        ),
        PolicyKey(
            "notice",
            None,
            _text,
            "Notice shown across the top of the site; empty hides it.",
        ),
    )
}


class Policy:
    """Configured defaults with the database overrides applied on top."""

    def __init__(
        self, settings: Settings, overrides: dict[str, Any] | None = None
    ) -> None:
        self._settings = settings
        self._overrides: dict[str, Any] = dict(overrides or {})

    @classmethod
    async def load(cls, settings: Settings, repo: Repository) -> "Policy":
        stored = await repo.get_policy()
        policy = cls(settings)
        for key, value in stored.items():
            if key not in KEYS:
                log.warning(
                    "ignoring unknown policy key", extra={"fields": {"key": key}}
                )
                continue
            policy._overrides[key] = KEYS[key].coerce(value)
        return policy

    def default(self, key: str) -> Any:
        spec = KEYS[key]
        if spec.setting is None:
            return spec.fallback
        return getattr(self._settings, spec.setting)

    def __getitem__(self, key: str) -> Any:
        if key not in KEYS:
            raise PolicyError(f"unknown setting {key!r}")
        return self._overrides.get(key, self.default(key))

    def source(self, key: str) -> str:
        return "database" if key in self._overrides else "configuration"

    def set(self, key: str, value: Any) -> Any:
        if key not in KEYS:
            raise PolicyError(f"unknown setting {key!r}")
        coerced = KEYS[key].coerce(value)
        self._overrides[key] = coerced
        return coerced

    def clear(self, key: str) -> None:
        self._overrides.pop(key, None)

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "key": key,
                "value": self[key],
                "default": self.default(key),
                "source": self.source(key),
                "description": spec.description,
            }
            for key, spec in KEYS.items()
        ]
