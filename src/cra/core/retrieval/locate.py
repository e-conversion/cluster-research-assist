"""Finding where a paper from outside the library would sit inside it.

One path, used by the map in the browser and by the tool the model calls, so
the two can never disagree about what a DOI means. The steps are: recognise
the DOI, answer from the library when it is already there, otherwise ask the
metadata sources, embed what comes back, and interpolate a position from the
nearest papers.

The library is asked before anything is sent, and before any allowance is
spent: a paper the cluster already has costs nothing to find.
"""

import asyncio
import logging
import re
from dataclasses import dataclass

import httpx

from cra.config.settings import Settings
from cra.core.connectors import doi_lookup
from cra.core.connectors.crossref import MetadataError, Work
from cra.core.library.citation import short
from cra.core.library.records import Paper, normalise_doi
from cra.core.retrieval.indexes import Indexes
from cra.core.retrieval.placement import Placement, place, place_known

log = logging.getLogger(__name__)

# A DOI is a registrant prefix and a suffix the registrant chooses freely.
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$")
MAX_DOI_CHARS = 200


class LocateError(Exception):
    """A lookup that could not finish, with the reason a person needs."""

    def __init__(self, message: str, reason: str, retry_after: int = 0) -> None:
        super().__init__(message)
        self.reason = reason
        self.retry_after = retry_after


@dataclass(frozen=True)
class Located:
    doi: str
    work: Work
    placement: Placement
    in_library: bool
    paper: Paper | None

    @property
    def citation(self) -> str:
        return short(self.paper) if self.paper else self.work.citation

    @property
    def has_abstract(self) -> bool:
        return bool(self.paper.abstract if self.paper else self.work.abstract)


def clean_doi(raw: str) -> str:
    """The DOI a person pasted, or an empty string if it is not one."""
    if not raw or len(raw) > MAX_DOI_CHARS:
        return ""
    doi = normalise_doi(raw)
    return doi if DOI_PATTERN.match(doi) else ""


async def locate(
    raw_doi: str,
    *,
    indexes: Indexes,
    http: httpx.AsyncClient | None,
    guard: doi_lookup.LookupGuard,
    settings: Settings,
    allow_network: bool = True,
) -> Located:
    """Place ``raw_doi`` among the library's papers.

    ``allow_network`` is left False by a caller that has not yet charged the
    lookup against someone's allowance, so the library can be consulted first.
    """
    doi = clean_doi(raw_doi)
    if not doi:
        raise LocateError(
            "That does not look like a DOI. Paste something like "
            "10.1038/s41586-021-03819-2.",
            "invalid_doi",
        )

    stored = indexes.library.map
    if stored is None or indexes.dense is None:
        raise LocateError("This library has no publication map.", "map_unavailable")

    # Already ours: no network, no allowance, and the stored position rather
    # than an estimate of it.
    paper = indexes.library.paper(doi)
    known = place_known(doi, indexes.dense, stored)
    if known is not None:
        work = Work(
            doi=doi,
            title=paper.title if paper else "",
            authors=paper.authors if paper else (),
            year=paper.year if paper else "",
            journal=paper.journal if paper else "",
            abstract=paper.abstract if paper else "",
            source="library",
        )
        return Located(doi, work, known, True, paper)

    if not indexes.semantic_ready:
        raise LocateError(
            "This deployment cannot place a new paper: it has no query encoder.",
            "semantic_unavailable",
        )
    if not allow_network:
        raise LocateError(f"{doi} is not in the library.", "not_in_library")
    if http is None:
        raise LocateError(
            "No HTTP client is available for external requests.", "unavailable"
        )

    try:
        work = await doi_lookup.fetch(
            http,
            doi,
            guard=guard,
            crossref_url=settings.crossref_base_url,
            openalex_url=settings.openalex_base_url,
            mailto=settings.doi_lookup_contact,
        )
    except MetadataError as error:
        raise LocateError(str(error), error.reason, error.retry_after) from None

    text = work.embeddable
    if not text:
        raise LocateError(
            f"{doi} is registered but carries no title or abstract, so there is "
            "nothing to place it by.",
            "no_text",
        )

    # A forward pass is tens of milliseconds of CPU; off the loop it goes, so
    # a lookup cannot stall the chat streams sharing this process.
    encoder = indexes.encoder
    assert encoder is not None  # guaranteed by semantic_ready
    vector = await asyncio.to_thread(encoder.encode, text)

    placement = place(vector, indexes.dense, stored)
    if placement is None:
        raise LocateError("None of the closest papers are on the map.", "unplaceable")
    log.info(
        "placed a paper from outside the library",
        extra={
            "fields": {
                "doi": doi,
                "source": work.source,
                "abstract": bool(work.abstract),
                "confidence": round(placement.confidence, 4),
            }
        },
    )
    return Located(doi, work, placement, False, None)
