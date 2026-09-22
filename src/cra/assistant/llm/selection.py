"""Which model answers.

A deployment names the models it offers. On OpenRouter it may name none, and
then the cheapest model that satisfies the account's guardrails is chosen per
session: the catalogue call is slow enough to be worth caching and cheap enough
to repeat once in a while.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from cra.assistant.llm.params import is_openrouter
from cra.config.settings import Settings

log = logging.getLogger(__name__)

CATALOGUE_TTL_S = 600
CATALOGUE_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


@dataclass(frozen=True)
class Choice:
    model: str
    automatic: bool

    @property
    def label(self) -> str:
        return self.model or "auto"


class ModelCatalogue:
    """The OpenRouter models this account may use, cheapest first."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cached: tuple[float, list[str]] | None = None

    def _fits(self, model: dict[str, Any]) -> bool:
        pricing = model.get("pricing") or {}
        try:
            prompt = float(pricing.get("prompt") or 0)
            completion = float(pricing.get("completion") or 0)
        except (TypeError, ValueError):
            return False
        cap = self._settings.openrouter_max_price_per_mtok / 1_000_000
        if prompt < 0 or completion < 0 or prompt > cap or completion > cap:
            return False
        # the assistant answers by calling tools, so one that cannot is useless
        if "tools" not in (model.get("supported_parameters") or []):
            return False
        # Cheapest-first without a quality floor would pick something unusable.
        # Most models carry only one of the two indices, so either will do.
        benchmarks = (model.get("benchmarks") or {}).get("artificial_analysis") or {}
        scores = [benchmarks.get("agentic_index"), benchmarks.get("intelligence_index")]
        best = max((float(s) for s in scores if s is not None), default=0.0)
        return best >= self._settings.openrouter_min_agentic_index

    async def cheapest_first(self, client: httpx.AsyncClient) -> list[str]:
        key = self._settings.llm_api_key.get_secret_value()
        if not key:
            return []
        now = time.monotonic()
        if self._cached and now - self._cached[0] < CATALOGUE_TTL_S:
            return self._cached[1]
        try:
            allowed, catalogue = await self._fetch(client, key)
        except httpx.HTTPError as exc:
            log.warning(
                "the model catalogue is unavailable",
                extra={"fields": {"error": str(exc)}},
            )
            return self._cached[1] if self._cached else []
        priced = [
            (float((m.get("pricing") or {}).get("prompt") or 0), m["id"])
            for m in catalogue
            # a free endpoint offers no data guarantee worth relying on
            if m.get("id") in allowed
            and ":free" not in m.get("id", "")
            and self._fits(m)
        ]
        models = [model for _, model in sorted(priced)]
        self._cached = (now, models)
        log.info("model catalogue refreshed", extra={"fields": {"models": len(models)}})
        return models

    async def _fetch(
        self, client: httpx.AsyncClient, key: str
    ) -> tuple[set[str], list[dict[str, Any]]]:
        base = self._settings.llm_base_url.rstrip("/")
        # the guardrails live on one endpoint and the benchmarks on the other
        mine = await client.get(
            f"{base}/models/user",
            headers={"Authorization": f"Bearer {key}"},
            timeout=CATALOGUE_TIMEOUT,
        )
        mine.raise_for_status()
        every = await client.get(f"{base}/models", timeout=CATALOGUE_TIMEOUT)
        every.raise_for_status()
        allowed = {m.get("id", "") for m in mine.json().get("data", [])}
        return allowed, every.json().get("data", [])


def offered(settings: Settings, policy_models: list[str] | None = None) -> list[str]:
    """What the deployment names, without asking anyone."""
    models = list(policy_models if policy_models is not None else settings.llm_models)
    if settings.llm_model and settings.llm_model not in models:
        models.insert(0, settings.llm_model)
    return models


async def available(
    settings: Settings,
    policy_models: list[str] | None = None,
    catalogue: "ModelCatalogue | None" = None,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """What a person may choose from.

    A deployment that names its models gets exactly those. One that names none
    and runs on OpenRouter gets everything the account may use that is cheap
    enough and good enough, so the choice is not limited to the fallback.
    """
    named = offered(settings, policy_models)
    if policy_models or settings.llm_models or not is_openrouter(settings):
        return named
    if catalogue is None or client is None:
        return named
    eligible = await catalogue.cheapest_first(client)
    return eligible or named


async def resolve(
    settings: Settings,
    catalogue: ModelCatalogue | None = None,
    client: httpx.AsyncClient | None = None,
    wanted: str = "",
    offered_models: list[str] | None = None,
) -> Choice:
    """The model for one turn. An empty pick means the deployment's default,
    or on OpenRouter the cheapest model that is good enough."""
    choices = await available(settings, offered_models, catalogue, client)
    if wanted and wanted in choices:
        return Choice(wanted, automatic=False)
    if not is_openrouter(settings):
        return Choice(
            settings.llm_model or (choices[0] if choices else ""), automatic=False
        )
    if choices and choices[0] != settings.llm_model:
        # the catalogue answered, and it is ordered cheapest first
        return Choice(choices[0], automatic=True)
    return Choice(settings.llm_model, automatic=bool(settings.llm_model))
