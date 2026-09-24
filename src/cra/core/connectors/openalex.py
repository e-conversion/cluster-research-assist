"""OpenAlex, consulted for the abstracts Crossref does not carry.

Many publishers never deposit an abstract with Crossref, so a lookup that
stopped there would fall back to placing papers by title alone far more often
than it needs to. OpenAlex stores an abstract for a good share of those, which
is the whole reason this second source exists.

It stores it inverted: a mapping of each word to the positions it occupies.
Reconstructing that is parsing untrusted input, so the bounds below are not
decoration. A record claiming a word at position 10**9 would otherwise have us
allocate a list of a billion entries.
"""

import logging
from typing import Any

import httpx

from cra.core.connectors.crossref import ABSTRACT_CHARS, MetadataError, Work
from cra.core.library.records import normalise_doi

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MAX_TOKENS = 4000
MAX_POSITION = 8000


def rebuild_abstract(inverted: Any) -> str:
    """Plain text from ``{word: [positions]}``, with every bound enforced."""
    if not isinstance(inverted, dict) or not inverted:
        return ""
    slots: dict[int, str] = {}
    for word, positions in list(inverted.items())[:MAX_TOKENS]:
        if not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int) and 0 <= position <= MAX_POSITION:
                slots[position] = str(word)
        if len(slots) > MAX_TOKENS:
            break
    if not slots:
        return ""
    return " ".join(slots[i] for i in sorted(slots))[:ABSTRACT_CHARS]


def _authors(authorships: Any) -> tuple[str, ...]:
    if not isinstance(authorships, list):
        return ()
    names = []
    for entry in authorships:
        author = entry.get("author") if isinstance(entry, dict) else None
        name = str((author or {}).get("display_name") or "").strip()
        if name:
            names.append(name)
    return tuple(names)


def _journal(record: dict[str, Any]) -> str:
    location = record.get("primary_location")
    source = (location or {}).get("source") if isinstance(location, dict) else None
    return str((source or {}).get("display_name") or "").strip()


def parse_work(record: dict[str, Any]) -> Work:
    year = record.get("publication_year")
    return Work(
        doi=normalise_doi(str(record.get("doi") or "")),
        title=str(record.get("display_name") or record.get("title") or "").strip(),
        authors=_authors(record.get("authorships")),
        year=str(year) if year else "",
        journal=_journal(record),
        abstract=rebuild_abstract(record.get("abstract_inverted_index")),
        source="openalex",
    )


async def fetch_work(
    client: httpx.AsyncClient, base_url: str, doi: str, *, mailto: str = ""
) -> Work:
    """One work by DOI. Raises ``MetadataError`` for anything else."""
    doi = normalise_doi(doi)
    url = f"{base_url.rstrip('/')}/works/doi:{doi}"
    params = {"mailto": mailto} if mailto else None
    try:
        response = await client.get(url, params=params, timeout=TIMEOUT)
    except httpx.HTTPError as error:
        log.warning(
            "openalex unreachable",
            extra={"fields": {"doi": doi, "error": type(error).__name__}},
        )
        raise MetadataError(
            "OpenAlex is not reachable right now.", "unavailable"
        ) from None

    if response.status_code == 404:
        raise MetadataError(f"OpenAlex has no record for {doi}.", "not_found")
    if response.status_code == 429:
        log.warning("openalex is throttling us", extra={"fields": {"doi": doi}})
        raise MetadataError(
            "OpenAlex is limiting how often we may ask. Try again shortly.",
            "rate_limited",
        )
    if response.status_code != 200:
        log.warning(
            "openalex refused",
            extra={"fields": {"doi": doi, "status": response.status_code}},
        )
        raise MetadataError("OpenAlex is not reachable right now.", "unavailable")

    try:
        record = response.json()
    except ValueError:
        raise MetadataError(
            "OpenAlex sent something we could not read.", "unavailable"
        ) from None
    if not isinstance(record, dict):
        raise MetadataError("OpenAlex sent something we could not read.", "unavailable")
    work = parse_work(record)
    return work if work.doi else Work(**{**work.__dict__, "doi": doi})
