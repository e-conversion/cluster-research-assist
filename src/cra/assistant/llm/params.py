"""What a person may change about how their questions are answered.

One place decides the fields the interface renders, the values a deployment
configures, the overrides a session may set and how each reaches the request.
Values are stored as strings, with the empty string meaning "not set, use the
provider's own default", so a configured value, a session override and the
value in a form all compare equal.
"""

from dataclasses import dataclass, field
from typing import Any

from cra.config.settings import Settings

# OpenRouter takes these in extra_body rather than as request fields
EXTRA_BODY_KEYS = frozenset(
    {
        "provider",
        "reasoning",
        "verbosity",
        "web_search_options",
        "models",
        "transforms",
        "route",
    }
)

ROUTES = (
    {"value": "price", "label": "cheapest"},
    {"value": "throughput", "label": "fastest"},
    {"value": "latency", "label": "lowest latency"},
)
ROUTE_VALUES = tuple(r["value"] for r in ROUTES)


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    kind: str
    help: str
    setting: str = ""
    options: tuple[str, ...] = ()
    option_labels: dict[str, str] = field(default_factory=dict)
    minimum: int = 0
    maximum: int = 0
    step: int = 1
    hidden: bool = False

    def payload(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "type": self.kind,
            "help": self.help,
        }
        if self.kind == "enum":
            entry["options"] = list(self.options)
            entry["option_labels"] = dict(self.option_labels)
        else:
            entry |= {"min": self.minimum, "max": self.maximum, "step": self.step}
        if self.hidden:
            entry["hidden"] = True
        return entry


PARAMS: tuple[Param, ...] = (
    Param(
        key="reasoning_effort",
        label="Reasoning effort",
        kind="enum",
        options=("", "minimal", "low", "medium", "high"),
        option_labels={"": "off (provider default)"},
        help="How long a reasoning model thinks before answering. Ignored by models without it.",
    ),
    Param(
        key="top_p",
        label="Top P",
        kind="enum",
        options=("", "0.8", "0.9", "0.95", "1.0"),
        option_labels={"": "default (1.0)"},
        help="Restrict sampling to the most likely tokens.",
    ),
    Param(
        key="max_tokens",
        label="Max answer length",
        kind="number",
        setting="llm_max_tokens",
        minimum=256,
        maximum=32_768,
        step=256,
        help="Tokens the answer may use. Empty leaves it to the deployment's setting.",
    ),
    Param(
        key="max_tool_rounds",
        label="Tool call limit",
        kind="number",
        setting="llm_max_tool_rounds",
        minimum=1,
        maximum=100,
        step=1,
        help="How many rounds of tool calls one answer may take before it answers with what it has.",
    ),
    Param(
        key="provider_sort",
        label="Provider routing",
        kind="enum",
        hidden=True,  # chosen in the model picker, not in the panel
        options=ROUTE_VALUES,
        option_labels={r["value"]: r["label"] for r in ROUTES},
        help="Which upstream OpenRouter prefers.",
    ),
)
KEYS = tuple(p.key for p in PARAMS)
BY_KEY = {p.key: p for p in PARAMS}


class ParamError(ValueError):
    pass


def normalise(key: str, value: Any) -> str:
    """The stored form of one value, or a ParamError saying why not."""
    param = BY_KEY.get(key)
    if param is None:
        raise ParamError(f"unknown parameter: {key}")
    text = "" if value is None else str(value).strip()
    if param.kind == "enum":
        if text not in param.options:
            allowed = ", ".join(o or "default" for o in param.options)
            raise ParamError(f"{key} must be one of {allowed}")
        return text
    if not text:
        return ""
    try:
        number = int(float(text))
    except ValueError:
        raise ParamError(f"{key} must be a number") from None
    if not param.minimum <= number <= param.maximum:
        raise ParamError(f"{key} must be between {param.minimum} and {param.maximum}")
    return str(number)


def defaults(settings: Settings) -> dict[str, str]:
    """What the deployment configures. A field the deployment does not set is
    empty, meaning the provider decides."""
    return {
        p.key: str(getattr(settings, p.setting)) if p.setting else "" for p in PARAMS
    } | {"provider_sort": "price"}


def effective(
    settings: Settings, overrides: dict[str, Any] | None = None
) -> dict[str, str]:
    """The configured values with a session's overrides on top. An override
    that is no longer valid is ignored rather than breaking the session."""
    values = defaults(settings)
    for key, value in (overrides or {}).items():
        if key not in BY_KEY:
            continue
        try:
            values[key] = normalise(key, value)
        except ParamError:
            continue
    return values


def request_fields(
    settings: Settings, overrides: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The fields one turn adds to the request.

    Sampling fields go to any OpenAI-compatible endpoint. The routing and
    reasoning controls are OpenRouter's own and are sent only there, so another
    gateway never sees a field it would reject.
    """
    values = effective(settings, overrides)
    fields: dict[str, Any] = {}
    if values["top_p"]:
        fields["top_p"] = float(values["top_p"])
    if values["max_tokens"]:
        fields["max_tokens"] = int(values["max_tokens"])
    if not is_openrouter(settings):
        return fields
    # Prompts must not be retained upstream, so zero-data-retention routing is
    # always requested rather than offered as a choice.
    fields["provider"] = {
        "sort": values["provider_sort"],
        "zdr": True,
        "data_collection": "deny",
        "max_price": {
            "prompt": settings.openrouter_max_price_per_mtok,
            "completion": settings.openrouter_max_price_per_mtok,
        },
    }
    if settings.openrouter_quantizations:
        fields["provider"]["quantizations"] = list(settings.openrouter_quantizations)
    if settings.openrouter_ignore_providers:
        fields["provider"]["ignore"] = list(settings.openrouter_ignore_providers)
    if values["reasoning_effort"]:
        fields["reasoning"] = {"effort": values["reasoning_effort"]}
    return fields


def split(fields: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Into (extra_body, plain keyword arguments). The SDK rejects an unknown
    top-level field, and the gateway ignores one it does not know in the body."""
    body = {k: v for k, v in fields.items() if k in EXTRA_BODY_KEYS}
    rest = {k: v for k, v in fields.items() if k not in EXTRA_BODY_KEYS}
    return body, rest


def is_openrouter(settings: Settings) -> bool:
    return (
        "openrouter" in settings.llm_provider.lower()
        or "openrouter" in settings.llm_base_url
    )


def payload(
    settings: Settings, overrides: dict[str, Any] | None = None
) -> dict[str, Any]:
    """What the interface needs to render the panel."""
    return {
        "spec": [p.payload() for p in PARAMS],
        "defaults": defaults(settings),
        "effective": effective(settings, overrides),
    }
