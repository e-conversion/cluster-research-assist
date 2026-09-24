"""One DOI, looked up across the sources that might know it.

Two public repositories answer here, and neither owes us an answer. Crossref
holds the registration metadata for most journal DOIs but frequently carries
no abstract; OpenAlex fills that gap. Asking both doubles the chance of a
useful abstract and doubles the chance of being throttled, so the guard below
is as much a part of this module as the fetching is.

Three things keep us welcome. Answers are cached, including failures, because
a person retrying a mistyped DOI is the likeliest way to spend a quota on
nothing. A source that throttles us is set aside rather than retried, for as
long as it asked for. And the whole lookup runs to a wall-clock budget, so a
slow first source cannot leave a page waiting on a second one.

The guard holds process-wide state and is therefore owned by the application,
not by this module: a module-level cache would outlive the app that created
it, leak between tests, and survive a reload it should not.
"""

import logging
import time
from dataclasses import dataclass, field

import httpx

from cra.core.connectors import crossref, openalex
from cra.core.connectors.crossref import MetadataError, Work
from cra.core.library.records import normalise_doi

log = logging.getLogger(__name__)

CACHE_ENTRIES = 512
CACHE_TTL_S = 3600.0
# A failure is remembered briefly: long enough to absorb a person retrying a
# typo, short enough that a source coming back is noticed quickly.
MISS_TTL_S = 300.0
BUDGET_S = 18.0


@dataclass
class _Entry:
    work: Work | None
    error: MetadataError | None
    expires: float


@dataclass
class LookupGuard:
    """Shared cache and per-source cooldown for outward DOI lookups."""

    clock: object = time.monotonic
    cooldown_s: float = 300.0
    _cache: dict[str, _Entry] = field(default_factory=dict)
    _cooling: dict[str, float] = field(default_factory=dict)

    def _now(self) -> float:
        return float(self.clock())  # type: ignore[operator]

    # ---- cooldown ----------------------------------------------------

    def available(self, source: str) -> bool:
        until = self._cooling.get(source)
        if until is None:
            return True
        if self._now() >= until:
            del self._cooling[source]
            return True
        return False

    def cool_down(self, source: str, seconds: float = 0.0) -> None:
        self._cooling[source] = self._now() + (seconds or self.cooldown_s)
        log.warning(
            "backing off a metadata source",
            extra={"fields": {"source": source, "seconds": seconds or self.cooldown_s}},
        )

    # ---- cache -------------------------------------------------------

    def cached(self, doi: str) -> _Entry | None:
        entry = self._cache.get(doi)
        if entry is None:
            return None
        if self._now() >= entry.expires:
            del self._cache[doi]
            return None
        return entry

    def remember(
        self, doi: str, work: Work | None, error: MetadataError | None = None
    ) -> None:
        if len(self._cache) >= CACHE_ENTRIES:
            # Cheap eviction: the keys are user-supplied, so the only thing
            # that matters is that the map stays bounded.
            for stale in list(self._cache)[: CACHE_ENTRIES // 4]:
                del self._cache[stale]
        ttl = CACHE_TTL_S if work is not None else MISS_TTL_S
        self._cache[doi] = _Entry(work, error, self._now() + ttl)


def _merge(primary: Work, extra: Work) -> Work:
    """Fill only what the primary source left empty.

    OpenAlex is consulted for a missing abstract, so it must not quietly
    replace a title or journal that Crossref already stated.
    """
    return Work(
        doi=primary.doi or extra.doi,
        title=primary.title or extra.title,
        authors=primary.authors or extra.authors,
        year=primary.year or extra.year,
        journal=primary.journal or extra.journal,
        abstract=primary.abstract or extra.abstract,
        source=primary.source
        if primary.abstract or not extra.abstract
        else f"{primary.source}+{extra.source}",
    )


async def fetch(
    client: httpx.AsyncClient,
    doi: str,
    *,
    guard: LookupGuard,
    crossref_url: str,
    openalex_url: str = "",
    mailto: str = "",
) -> Work:
    """Metadata for ``doi``, from cache or from whichever source answers.

    Raises ``MetadataError`` when no source could help, including when both
    are cooling down, in which case nothing is sent at all.
    """
    doi = normalise_doi(doi)
    hit = guard.cached(doi)
    if hit is not None:
        if hit.error is not None:
            raise hit.error
        if hit.work is not None:
            return hit.work

    started = time.monotonic()
    primary: Work | None = None
    failure: MetadataError | None = None
    asked = False
    cooling = False

    if crossref_url and not guard.available("crossref"):
        cooling = True
    if crossref_url and guard.available("crossref"):
        asked = True
        try:
            primary = await crossref.fetch_work(
                client, crossref_url, doi, mailto=mailto
            )
        except MetadataError as error:
            failure = error
            if error.reason == "rate_limited":
                guard.cool_down("crossref", error.retry_after)

    # OpenAlex fills a missing abstract, and stands in entirely when Crossref
    # had no record or could not be reached.
    wants_more = primary is None or not primary.abstract
    spent = time.monotonic() - started
    if openalex_url and wants_more and not guard.available("openalex"):
        cooling = True
    if openalex_url and wants_more and spent < BUDGET_S and guard.available("openalex"):
        asked = True
        try:
            secondary = await openalex.fetch_work(
                client, openalex_url, doi, mailto=mailto
            )
            primary = _merge(primary, secondary) if primary else secondary
        except MetadataError as error:
            if error.reason == "rate_limited":
                guard.cool_down("openalex", error.retry_after)
            if primary is None:
                failure = failure or error

    if primary is not None:
        guard.remember(doi, primary)
        return primary

    if failure is None:
        failure = (
            MetadataError(
                "We are asking the metadata sources too often. Try again shortly.",
                "rate_limited",
            )
            if cooling and not asked
            else MetadataError(
                "No metadata source is available right now.", "unavailable"
            )
        )
    # A throttle is about us, not about this DOI, so remembering it would
    # answer a later, healthier request with a stale refusal.
    if failure.reason != "rate_limited":
        guard.remember(doi, None, failure)
    raise failure
