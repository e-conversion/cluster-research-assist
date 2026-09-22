"""One ``Library`` object per process, loaded explicitly from one directory.

Only ``papers.csv`` is required; each other file switches a capability on.
Nothing here runs at import time.
"""

import csv
import json
import logging
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
from networkx.readwrite import json_graph

from cra.core.library import manifest as manifest_
from cra.core.library.records import (
    PI,
    Dataset,
    Embeddings,
    FullText,
    Paper,
    Proposal,
    PublicationMap,
    normalise_doi,
)
from cra.core.library.versions import resolve

log = logging.getLogger(__name__)

FILES = {
    "papers": "papers.csv",
    "abstracts": "abstracts.json",
    "fulltexts": "fulltexts.json",
    "pis": "pis.json",
    "embeddings": "embeddings.npz",
    "graph": "graph.json",
    "proposal": "proposal.md",
    "proposal_summary": "proposal_summary.md",
    "map": "publication_map.json",
}


class LibraryError(Exception):
    pass


class Library:
    def __init__(
        self,
        path: Path,
        papers: dict[str, Paper],
        fulltexts: dict[str, FullText],
        pis: tuple[PI, ...],
        proposal: Proposal | None,
        embeddings: Embeddings | None,
        graph: nx.Graph | None,
        map_: PublicationMap | None,
    ) -> None:
        self.path = path
        self.papers = papers
        self.fulltexts = fulltexts
        self.pis = pis
        self.proposal = proposal
        self.embeddings = embeddings
        self.graph = graph
        self.map = map_

    @classmethod
    def load(
        cls, path: Path, *, verify: bool = True, required_schema: str = "1.x"
    ) -> "Library":
        configured = Path(path)
        if not configured.is_dir():
            raise LibraryError(f"library directory {configured} does not exist")
        # the configured path may be a bundle or a root holding versions
        path = resolve(configured)
        if verify:
            problems = manifest_.check(path, required_schema)
            if problems:
                raise LibraryError("library bundle rejected: " + "; ".join(problems))
        files = {key: path / name for key, name in FILES.items()}
        if not files["papers"].exists():
            raise LibraryError(f"{FILES['papers']} missing in {path}")

        papers = _load_papers(files["papers"], _load_json(files["abstracts"]) or {})
        library = cls(
            path=path,
            papers=papers,
            fulltexts=_load_fulltexts(_load_json(files["fulltexts"]) or {}),
            pis=_load_pis(_load_json(files["pis"]) or []),
            proposal=_load_proposal(files["proposal"], files["proposal_summary"]),
            embeddings=_load_embeddings(files["embeddings"]),
            graph=_load_graph(_load_json(files["graph"])),
            map_=_load_map(_load_json(files["map"])),
        )
        if verify:
            problems = manifest_.check_counts(
                manifest_.read(path) or {}, library.counts
            )
            if problems:
                raise LibraryError("library bundle rejected: " + "; ".join(problems))
        log.info(
            "library loaded", extra={"fields": {"path": str(path), **library.counts}}
        )
        return library

    @property
    def available(self) -> dict[str, bool]:
        return {
            "papers": bool(self.papers),
            "abstracts": any(p.abstract for p in self.papers.values()),
            "fulltexts": bool(self.fulltexts),
            "pis": bool(self.pis),
            "embeddings": self.embeddings is not None,
            "graph": self.graph is not None,
            "proposal": self.proposal is not None,
            "map": self.map is not None,
        }

    @property
    def counts(self) -> dict[str, int]:
        return {
            "papers": len(self.papers),
            "abstracts": sum(1 for p in self.papers.values() if p.abstract),
            "fulltexts": len(self.fulltexts),
            "pis": len(self.pis),
            "graph_nodes": self.graph.number_of_nodes()
            if self.graph is not None
            else 0,
            "graph_edges": self.graph.number_of_edges()
            if self.graph is not None
            else 0,
            "embeddings": len(self.embeddings) if self.embeddings is not None else 0,
            "map_points": len(self.map.dois) if self.map is not None else 0,
        }

    def paper(self, doi: str) -> Paper | None:
        return self.papers.get(normalise_doi(doi))

    def fulltext(self, doi: str) -> FullText | None:
        return self.fulltexts.get(normalise_doi(doi))


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        raise LibraryError(f"{path.name}: {exc}") from exc


def _split_authors(field: str) -> tuple[str, ...]:
    return tuple(a.strip() for a in field.split(" and ") if a.strip())


def _load_papers(path: Path, abstracts: dict[str, dict[str, Any]]) -> dict[str, Paper]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            doi = normalise_doi(row.get("article_doi") or "")
            if not doi:
                continue
            # one CSV row per linked dataset: the paper repeats, the dataset differs
            paper = rows.setdefault(
                doi,
                {
                    "title": row.get("article_title", "").strip(),
                    "authors": _split_authors(row.get("article_authors", "")),
                    "year": row.get("article_year", "").strip(),
                    "datasets": [],
                },
            )
            if row.get("dataset_doi"):
                paper["datasets"].append(
                    Dataset(
                        doi=row["dataset_doi"].strip(),
                        title=row.get("dataset_title", ""),
                    )
                )
    papers = {}
    for doi, raw in rows.items():
        cached = abstracts.get(doi) or {}
        # OpenAlex author names are clean UTF-8 where the scraped CSV truncates them
        authors = tuple(cached.get("authors") or ()) or raw["authors"]
        papers[doi] = Paper(
            doi=doi,
            title=raw["title"],
            authors=authors,
            year=raw["year"],
            journal=cached.get("journal") or "",
            citation_count=cached.get("citation_count"),
            abstract=cached.get("abstract") or "",
            abstract_source=cached.get("source") or "",
            datasets=tuple(raw["datasets"]),
        )
    return papers


def _load_fulltexts(raw: dict[str, dict[str, Any]]) -> dict[str, FullText]:
    return {
        normalise_doi(doi): FullText(
            doi=normalise_doi(doi),
            text=entry.get("fulltext", ""),
            source=entry.get("source", ""),
            source_origin=entry.get("source_origin", ""),
            url=entry.get("url", ""),
            fetched_at=entry.get("fetched_at", ""),
        )
        for doi, entry in raw.items()
        if entry.get("fulltext")
    }


def _load_pis(raw: list[dict[str, Any]]) -> tuple[PI, ...]:
    return tuple(
        PI(
            smid=str(entry.get("smid", "")),
            name=entry.get("name", ""),
            first_name=entry.get("first_name", ""),
            last_name=entry.get("last_name", ""),
            group=entry.get("group", ""),
            department=entry.get("department", ""),
            institution=entry.get("institution", ""),
            website=entry.get("website", ""),
            profile_url=entry.get("profile_url", ""),
            research_focus=tuple(entry.get("research_focus") or ()),
            application_fields=tuple(entry.get("application_fields") or ()),
            publication_dois=tuple(
                dict.fromkeys(
                    normalise_doi(d) for d in entry.get("publication_dois") or ()
                )
            ),
        )
        for entry in raw
    )


def _load_proposal(text_path: Path, summary_path: Path) -> Proposal | None:
    if not text_path.exists():
        return None
    text = text_path.read_text(encoding="utf-8")
    summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    paragraphs = tuple(p.strip() for p in text.split("\n\n") if p.strip())
    return Proposal(text=text, paragraphs=paragraphs, summary=summary)


def _load_embeddings(path: Path) -> Embeddings | None:
    if not path.exists():
        return None
    with np.load(path, allow_pickle=True) as data:
        dois = tuple(normalise_doi(str(d)) for d in data["dois"])
        vectors = np.asarray(data["vectors"], dtype=np.float32)
        model = str(data["model"]) if "model" in data else ""
    return Embeddings(dois=dois, vectors=vectors, model=model)


def _load_graph(raw: dict[str, Any] | None) -> nx.Graph | None:
    if raw is None:
        return None
    return json_graph.node_link_graph(raw, edges="links")


def _load_map(raw: dict[str, Any] | None) -> PublicationMap | None:
    if raw is None:
        return None
    dois = tuple(normalise_doi(d) for d in raw.get("dois") or ())
    x, y = tuple(raw.get("x") or ()), tuple(raw.get("y") or ())
    if len(x) != len(dois) or len(y) != len(dois):
        raise LibraryError(f"{FILES['map']}: one coordinate pair per DOI expected")
    clusters = {}
    for key, entry in (raw.get("clusters") or {}).items():
        assignments = tuple(int(a) for a in entry.get("assignments") or ())
        if len(assignments) != len(dois):
            raise LibraryError(
                f"{FILES['map']}: clustering {key} does not cover every DOI"
            )
        clusters[int(key)] = (assignments, tuple(entry.get("labels") or ()))
    if not clusters:
        raise LibraryError(f"{FILES['map']}: no clusterings")
    return PublicationMap(
        dois=dois, x=x, y=y, clusters=clusters, model=str(raw.get("model", ""))
    )
