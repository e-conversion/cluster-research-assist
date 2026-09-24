"""Crossref, the registration agency behind most journal DOIs.

Metadata here is deposited by publishers, so what arrives is uneven: titles
are lists, dates are nested arrays, and abstracts are frequently absent
entirely because many publishers never deposit them. Everything below is
written to survive that rather than to assume a shape.

The client stays deliberately unopinionated about an empty record. A work with
neither title nor abstract comes back as an empty ``Work``; whether that is an
error depends on the caller, and deciding here would make the client
untestable without a request context.
"""

import html
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from cra.core.library.citation import compose
from cra.core.library.records import normalise_doi

log = logging.getLogger(__name__)

# Well under the shared client's 30 s, because a lookup may consult a second
# source afterwards and still has to answer a waiting page.
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
ABSTRACT_CHARS = 4000

_HEADING = re.compile(
    r"<(jats:)?title[^>]*>.*?</(jats:)?title>", re.DOTALL | re.IGNORECASE
)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


class MetadataError(Exception):
    """A lookup that failed in a way the caller must tell the user about."""

    def __init__(self, message: str, reason: str, retry_after: int = 0) -> None:
        super().__init__(message)
        self.reason = reason  # not_found | rate_limited | unavailable
        self.retry_after = retry_after


@dataclass(frozen=True)
class Work:
    doi: str
    title: str = ""
    authors: tuple[str, ...] = ()
    year: str = ""
    journal: str = ""
    abstract: str = ""
    source: str = ""

    @property
    def citation(self) -> str:
        return compose(
            self.authors, self.title, self.journal, self.year, fallback=self.doi
        )

    @property
    def embeddable(self) -> str:
        """The text the library was built from: title, then abstract."""
        return f"{self.title}. {self.abstract}".strip(". ").strip()


def strip_jats(raw: str) -> str:
    """Plain text from the JATS fragment Crossref stores as an abstract.

    These fragments are routinely not well-formed XML: the ``jats:`` prefix is
    undeclared and there is often no single root element, so an XML parser
    raises on a real share of live records. Stripping tags before unescaping
    entities is the part that matters. Done the other way round, an abstract
    that legitimately mentions ``&lt;p&gt;`` would turn into a real tag and the
    next step would eat the surrounding sentence.
    """
    if not raw:
        return ""
    text = _HEADING.sub(" ", raw)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _SPACE.sub(" ", text).strip()
    if text.lower().startswith("abstract "):
        text = text[len("abstract ") :].lstrip(" :.-")
    return text[:ABSTRACT_CHARS]


def _first(values: Any) -> str:
    if isinstance(values, list):
        return str(values[0]).strip() if values and values[0] else ""
    return str(values).strip() if values else ""


def _year(issued: Any) -> str:
    """``{"date-parts": [[2013, 7, 31]]}`` -> ``"2013"``, defensively."""
    if not isinstance(issued, dict):
        return ""
    parts = issued.get("date-parts")
    if not isinstance(parts, list) or not parts:
        return ""
    first = parts[0]
    if not isinstance(first, list) or not first or first[0] is None:
        return ""
    return str(first[0])


def _authors(entries: Any) -> tuple[str, ...]:
    if not isinstance(entries, list):
        return ()
    names = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        family = str(entry.get("family") or "").strip()
        given = str(entry.get("given") or "").strip()
        # Consortium entries carry only `name`.
        whole = str(entry.get("name") or "").strip()
        name = f"{given} {family}".strip() or whole
        if name:
            names.append(name)
    return tuple(names)


def parse_work(message: dict[str, Any]) -> Work:
    return Work(
        doi=normalise_doi(str(message.get("DOI") or "")),
        title=_first(message.get("title")),
        authors=_authors(message.get("author")),
        year=_year(message.get("issued")),
        journal=_first(message.get("container-title")),
        abstract=strip_jats(str(message.get("abstract") or "")),
        source="crossref",
    )


def _retry_after(response: httpx.Response) -> int:
    raw = response.headers.get("retry-after", "")
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return 0


async def fetch_work(
    client: httpx.AsyncClient, base_url: str, doi: str, *, mailto: str = ""
) -> Work:
    """One work by DOI. Raises ``MetadataError`` for anything else."""
    doi = normalise_doi(doi)
    url = f"{base_url.rstrip('/')}/works/{quote(doi, safe='')}"
    # Crossref routes requests carrying a contact address to a pool reserved
    # for identifiable callers, which is throttled far less aggressively.
    params = {"mailto": mailto} if mailto else None
    try:
        response = await client.get(url, params=params, timeout=TIMEOUT)
    except httpx.HTTPError as error:
        log.warning(
            "crossref unreachable",
            extra={"fields": {"doi": doi, "error": type(error).__name__}},
        )
        raise MetadataError(
            "Crossref is not reachable right now.", "unavailable"
        ) from None

    if response.status_code == 404:
        raise MetadataError(f"Crossref has no record for {doi}.", "not_found")
    if response.status_code == 429:
        log.warning("crossref is throttling us", extra={"fields": {"doi": doi}})
        raise MetadataError(
            "Crossref is limiting how often we may ask. Try again shortly.",
            "rate_limited",
            _retry_after(response),
        )
    if response.status_code != 200:
        log.warning(
            "crossref refused",
            extra={"fields": {"doi": doi, "status": response.status_code}},
        )
        raise MetadataError("Crossref is not reachable right now.", "unavailable")

    try:
        message = response.json()["message"]
    except (ValueError, KeyError, TypeError):
        raise MetadataError(
            "Crossref sent something we could not read.", "unavailable"
        ) from None
    work = parse_work(message)
    # Crossref echoes the DOI in the registrant's own casing.
    return work if work.doi else Work(**{**work.__dict__, "doi": doi})
