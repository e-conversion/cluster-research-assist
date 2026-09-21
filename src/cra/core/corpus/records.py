"""Immutable views over the corpus files."""

from dataclasses import dataclass, field

import numpy as np


def normalise_doi(doi: str) -> str:
    """Lowercase, no resolver prefix, no stray brace (a scraper quirk in real data)."""
    doi = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        doi = doi.removeprefix(prefix)
    return doi.rstrip("} ")


@dataclass(frozen=True)
class Dataset:
    doi: str
    title: str


@dataclass(frozen=True)
class Paper:
    doi: str
    title: str
    authors: tuple[str, ...]
    year: str
    journal: str = ""
    citation_count: int | None = None
    abstract: str = ""
    abstract_source: str = ""
    datasets: tuple[Dataset, ...] = ()


@dataclass(frozen=True)
class FullText:
    doi: str
    text: str
    source: str
    source_origin: str
    url: str
    fetched_at: str

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class PI:
    smid: str
    name: str
    first_name: str
    last_name: str
    group: str
    department: str
    institution: str
    website: str
    profile_url: str
    research_focus: tuple[str, ...]
    application_fields: tuple[str, ...]
    publication_dois: tuple[str, ...]


@dataclass(frozen=True)
class Proposal:
    text: str
    paragraphs: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class Embeddings:
    dois: tuple[str, ...]
    vectors: np.ndarray
    model: str
    index: dict[str, int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.vectors.ndim != 2 or self.vectors.shape[0] != len(self.dois):
            raise ValueError("embeddings: one vector per DOI expected")
        object.__setattr__(self, "index", {d: i for i, d in enumerate(self.dois)})

    def __len__(self) -> int:
        return len(self.dois)
