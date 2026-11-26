"""Method summaries of the library's papers, written by the model at build time.

The model reads each paper once, when ``cra library summarise`` runs, and its
answer is kept in ``summaries.json``: serving never calls it. What is sent and
how an answer is judged is decided in ``cra.core.library.summaries``; this
module only asks, a few papers at a time, and reports each answer as it comes
so that a run stopped halfway keeps what it has.

The model is the configured one (``CRA_LLM_MODEL``), reached the way the chat
reaches it. A paper is sent whole, and the endpoint therefore sees the full
texts of the library: run this against an endpoint the cluster trusts with them.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cra.assistant.llm.client import ChatClient
from cra.assistant.llm.params import is_openrouter
from cra.config.settings import Settings
from cra.core.library import summaries as store

log = logging.getLogger(__name__)

INSTRUCTION = (
    "You summarise one research paper for the catalogue of a research cluster's "
    "publications, so that papers can be found by what was done and how. Use only "
    "the text you are given and never add what the paper does not say. Write in "
    "English whatever the paper's language, keeping the names of materials, methods "
    "and instruments as the paper writes them. Leave a field empty (an empty string "
    "or list) when the text does not say. Answer with one JSON object and nothing "
    "else, with these keys:\n"
    '- "summary": one sentence of at most {summary} characters: what was done and found.\n'
    '- "approach": exactly one of "experimental", "computational", "theoretical", '
    '"mixed", "review". Use "mixed" when the paper does substantial experiments and '
    'substantial calculations, and "review" for a review or a perspective.\n'
    '- "material_system": the material or system studied, at most {material_system} characters.\n'
    '- "processing_method": what was done to make or run it, the synthesis or '
    "deposition or the simulation method, at most {processing_method} characters.\n"
    '- "characterization_methods": up to {items} short names of what was measured or '
    'analysed, for example ["XRD", "resistivity"].\n'
    '- "properties_investigated": up to {items} short names of the properties studied.\n'
    '- "results": the main result with its numbers where the text gives them, at most '
    "{results} characters.\n"
    '- "novelty": what is new about it, as the authors say, at most {novelty} characters.\n'
    '- "limitations": limitations the authors state, at most {limitations} characters.'
).format(items=store.LIST_ITEMS, **store.LIMITS)
ABSTRACT_ONLY = "Only the abstract is available, so leave out what it does not say."

# Room for a reasoning model to think before it answers (see naming.py).
MAX_TOKENS = 3000
TIMEOUT_S = 240.0
ATTEMPTS = 2


@dataclass
class Outcome:
    summarised: int = 0
    failed: int = 0


def request_for(settings: Settings, model: str, job: store.Job) -> dict[str, Any]:
    kind = "full text" if job.source == "fulltext" else "abstract only"
    content = f"Title: {job.title}\nText: {kind}\n\n{job.text}"
    if job.source != "fulltext":
        content += f"\n\n{ABSTRACT_ONLY}"
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {"role": "system", "content": INSTRUCTION},
            {"role": "user", "content": content},
        ],
    }
    if is_openrouter(settings):
        request["reasoning"] = {"effort": "minimal"}
    return request


async def summarise_one(
    client: ChatClient, settings: Settings, model: str, job: store.Job
) -> dict[str, Any] | None:
    """The fields to keep for one paper, or None when the model gave none."""
    request = request_for(settings, model, job)
    for attempt in range(ATTEMPTS):
        try:
            response = await client.complete(request, timeout_s=TIMEOUT_S)
            choices = response.get("choices") or [{}]
            answer = store.parse((choices[0].get("message") or {}).get("content") or "")
        except Exception:
            log.warning(
                "no summary from the model",
                extra={"fields": {"doi": job.doi, "attempt": attempt + 1}},
                exc_info=True,
            )
            continue
        fields = store.validate(answer) if answer is not None else None
        if fields is not None:
            return fields
    return None


async def summarise(
    client: ChatClient,
    settings: Settings,
    model: str,
    jobs: list[store.Job],
    on_result: Callable[[store.Job, dict[str, Any] | None], None],
    *,
    concurrency: int = 2,
) -> Outcome:
    """Ask for every job, ``concurrency`` at a time, reporting each as it ends."""
    outcome = Outcome()
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(job: store.Job) -> None:
        async with gate:
            fields = await summarise_one(client, settings, model, job)
        if fields is None:
            outcome.failed += 1
        else:
            outcome.summarised += 1
        on_result(job, fields)

    await asyncio.gather(*(one(job) for job in jobs))
    return outcome
