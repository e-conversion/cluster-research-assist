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
        benchmarks = (model.get("benchmarks") or {}).get("artificial_analysis") or {}
        try:
            agentic = float(benchmarks.get("agentic_index") or 0)
        except (TypeError, ValueError):
            return False
        # the assistant answers by calling tools, so a model that cannot is useless
        return agentic >= self._settings.openrouter_min_agentic_index and "tools" in (
            model.get("supported_parameters") or []
        )

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
    models = policy_models if policy_models is not None else settings.llm_models
    if settings.llm_model and settings.llm_model not in models:
        return [settings.llm_model, *models]
    return list(models)


async def resolve(
    settings: Settings,
    catalogue: ModelCatalogue | None,
    client: httpx.AsyncClient | None,
    wanted: str = "",
    offered_models: list[str] | None = None,
) -> Choice:
    """The model for one turn. An empty pick means the deployment's default, or
    on OpenRouter the cheapest eligible model."""
    available = offered(settings, offered_models)
    if wanted and wanted in available:
        return Choice(wanted, automatic=False)
    if not is_openrouter(settings):
        return Choice(
            settings.llm_model or (available[0] if available else ""), automatic=False
        )
    if catalogue is not None and client is not None:
        cheapest = await catalogue.cheapest_first(client)
        if cheapest:
            return Choice(cheapest[0], automatic=True)
    return Choice(settings.llm_model, automatic=bool(settings.llm_model))
