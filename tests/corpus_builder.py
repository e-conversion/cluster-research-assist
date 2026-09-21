"""Writes a tiny corpus directory with known content so tests can assert exact
results. The committed toy bundle under tests/data/corpus is the realistic one."""

import csv
import json
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
from networkx.readwrite import json_graph

from cra.core.corpus import manifest

PAPERS: list[dict[str, Any]] = [
    {
        "doi": "10.1000/alpha",
        "title": "Perovskite solar cells with long-range electrostatics",
        "authors": ["Ada Lovelace", "Grace Hopper"],
        "year": "2024",
        "abstract": "We study perovskite stability under illumination using machine-learned potentials.",
        "journal": "Journal of Toy Science",
        "citation_count": 3,
    },
    {
        "doi": "10.1000/beta",
        "title": "Electrocatalysis on copper surfaces",
        "authors": ["Grace Hopper", "Emmy Noether"],
        "year": "2023",
        "abstract": "Copper electrodes convert carbon dioxide into methanol in electrocatalysis experiments.",
        "journal": "Nature Toys",
        "citation_count": 10,
    },
    {
        "doi": "10.1000/gamma",
        "title": "Battery electrolytes at interfaces",
        "authors": ["Emmy Noether"],
        "year": "2022",
        "abstract": "",
        "journal": None,
        "citation_count": None,
    },
]

PIS: list[dict[str, Any]] = [
    {
        "smid": "1",
        "name": "Dr. Ada Lovelace",
        "title": "Dr.",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "group": "Lovelace Group",
        "department": "Physics",
        "institution": "TUM",
        "website": "https://example.org/lovelace",
        "profile_url": "https://example.org/members/1",
        "image_url": "",
        "research_focus": ["Perovskite photovoltaics", "Machine-learned potentials"],
        "application_fields": ["Solar cells"],
        "publication_dois": ["10.1000/alpha", "10.1000/BETA}"],
        "fetched_at": "2026-01-01",
    },
    {
        "smid": "2",
        "name": "Prof. Dr. Grace Hopper",
        "title": "Prof. Dr.",
        "first_name": "Grace",
        "last_name": "Hopper",
        "group": "Hopper Group",
        "department": "Chemistry",
        "institution": "LMU",
        "website": "https://example.org/hopper",
        "profile_url": "https://example.org/members/2",
        "image_url": "",
        "research_focus": ["Electrocatalysis", "CO2 reduction"],
        "application_fields": ["Fuels"],
        "publication_dois": ["10.1000/beta", "10.1000/alpha"],
        "fetched_at": "2026-01-01",
    },
    {
        "smid": "3",
        "name": "Dr. Emmy Noether",
        "title": "Dr.",
        "first_name": "Emmy",
        "last_name": "Noether",
        "group": "Noether Group",
        "department": "Chemistry",
        "institution": "FHI",
        "website": "https://example.org/noether",
        "profile_url": "https://example.org/members/3",
        "image_url": "",
        "research_focus": ["Battery interfaces"],
        "application_fields": ["Batteries"],
        "publication_dois": ["10.1000/gamma", "10.1000/beta"],
        "fetched_at": "2026-01-01",
    },
]

PROPOSAL = (
    "# e-toy proposal\n\nSummary of the proposal: the cluster studies energy conversion.\n\n"
    "Work Package 1 covers perovskite solar cells.\n\nWork package 3 covers electrocatalysis "
    "and copper.\n\nPrincipal Investigators: Lovelace, Hopper, Noether.\n"
)


def write_corpus(
    directory: Path,
    *,
    abstracts: bool = True,
    fulltexts: bool = True,
    pis: bool = True,
    embeddings: bool = True,
    graph: bool = True,
    proposal: bool = True,
    with_manifest: bool = True,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "papers.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "article_doi",
                "article_title",
                "article_authors",
                "article_year",
                "enl_url",
                "dataset_doi",
                "dataset_title",
                "dataset_type",
            ]
        )
        for p in PAPERS:
            writer.writerow(
                [
                    p["doi"],
                    p["title"],
                    " and ".join(p["authors"]),
                    p["year"],
                    "",
                    "",
                    "",
                    "",
                ]
            )
        # a second dataset row for the first paper, as the real CSV has
        writer.writerow(
            [
                PAPERS[0]["doi"],
                PAPERS[0]["title"],
                "",
                "2024",
                "",
                "10.5281/zenodo.1",
                "Raw spectra",
                "dataset",
            ]
        )
    if abstracts:
        (directory / "abstracts.json").write_text(
            json.dumps(
                {
                    p["doi"]: {
                        "abstract": p["abstract"],
                        "source": "test",
                        "authors": p["authors"],
                        "journal": p["journal"],
                        "citation_count": p["citation_count"],
                    }
                    for p in PAPERS
                }
            )
        )
    if fulltexts:
        (directory / "fulltexts.json").write_text(
            json.dumps(
                {
                    p["doi"]: {
                        "fulltext": f"# {p['title']}\n\n{p['abstract']}\n\nMethods: we measured things carefully.",
                        "source": "synthetic",
                        "source_origin": "synthetic",
                        "url": "",
                        "char_count": 0,
                        "fetched_at": "2026-01-01",
                    }
                    for p in PAPERS[:2]
                }
            )
        )
    if pis:
        (directory / "pis.json").write_text(json.dumps(PIS))
    if embeddings:
        dois = sorted(p["doi"] for p in PAPERS)
        rng = np.random.default_rng(0)
        vectors = rng.standard_normal((len(dois), 8)).astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        np.savez(
            directory / "embeddings.npz",
            dois=np.array(dois, dtype=object),
            vectors=vectors,
            model="fake",
        )
    if graph:
        g = nx.Graph(built_from="pis.json", n_pis=len(PIS))
        for pi in PIS:
            g.add_node(
                pi["smid"],
                name=pi["name"],
                group=pi["group"],
                institution=pi["institution"],
                paper_count=len(pi["publication_dois"]),
            )
        g.add_edge("1", "2", weight=2, shared_dois=["10.1000/alpha", "10.1000/beta"])
        g.add_edge("2", "3", weight=1, shared_dois=["10.1000/beta"])
        (directory / "graph.json").write_text(
            json.dumps(json_graph.node_link_data(g, edges="links"))
        )
    if proposal:
        (directory / "proposal.md").write_text(PROPOSAL)
        (directory / "proposal_summary.md").write_text(
            "Summary of the proposal: energy conversion.\n"
        )
    if with_manifest:
        counts = {
            "papers": 3,
            "abstracts": 2 if abstracts else 0,
            "fulltexts": 2 if fulltexts else 0,
            "pis": 3 if pis else 0,
            "graph_nodes": 3 if graph else 0,
            "graph_edges": 2 if graph else 0,
            "embeddings": 3 if embeddings else 0,
        }
        manifest.write(directory, counts, embedding_model="fake" if embeddings else "")
    return directory
