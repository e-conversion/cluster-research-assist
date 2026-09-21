"""Usage: python developer/make_toy_corpus.py tests/data/corpus [--cache DIR]"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import httpx
import networkx as nx
import numpy as np

SEED = 42
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
ARXIV_API = "http://export.arxiv.org/api/query"
ARXIV_OAI = "http://export.arxiv.org/oai2"
ARXIV_DELAY_S = 3.0
FULLTEXT_MAX_CHARS = 20_000
SYNTHETIC_MIN_CHARS = 3_000
USER_AGENT = "cra-toy-corpus/1.0 (developer/make_toy_corpus.py)"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "arxiv": "http://arxiv.org/OAI/arXiv/",
}

# (cluster, title, pinned arXiv id). The id is only a fallback for when the
# title search fails or drifts (arXiv titles get edited between versions).
PAPERS = [
    (
        "a",
        "Long-range electrostatics in atomistic machine learning: a physical perspective",
        "2602.11071",
    ),
    (
        "a",
        "Electrostatic interactions in atomistic and machine-learned potentials for polar materials",
        "2412.01642",
    ),
    (
        "a",
        "Density-Based Long-Range Electrostatic Descriptors for Machine Learning Force Fields",
        "2406.17595",
    ),
    (
        "a",
        "Learning charges and long-range interactions from energies and forces",
        "2412.15455",
    ),
    (
        "a",
        "The dark side of the forces: assessing non-conservative force models for atomistic machine learning",
        "2412.11569",
    ),
    (
        "a",
        "MACE-POLAR-1: A Polarisable Electrostatic Foundation Model for Molecular Chemistry",
        "2602.19411",
    ),
    (
        "b",
        "MatterSim: A Deep Learning Atomistic Model Across Elements, Temperatures and Pressures",
        "2405.04967",
    ),
    (
        "b",
        "The Open Molecules 2025 (OMol25) Dataset, Evaluations, and Models",
        "2505.08762",
    ),
    # OC25 is not on arXiv; OC22 is the closest solid-liquid/electrocatalysis dataset paper.
    (
        "b",
        "The Open Catalyst 2022 (OC22) Dataset and Challenges for Oxide Electrocatalysts",
        "2206.08917",
    ),
    ("b", "LAMBench: A Benchmark for Large Atomistic Models", "2504.19578"),
    (
        "b",
        "High-quality, high-information datasets for universal atomistic machine learning",
        "2603.02089",
    ),
    (
        "c",
        "Electrostatic Phenomenology Benchmarks for Machine-Learned Interatomic Potentials in Electrochemistry",
        "2608.14153",
    ),
    ("c", "Machine learning potentials for redox chemistry in solution", "2410.03299"),
    # Substitutes for two electrochemistry papers that are not on arXiv.
    (
        "c",
        "Machine learning accelerated finite-field simulations for electrochemical interfaces",
        "2506.10548",
    ),
    (
        "c",
        "Hydration free energies from kernel-based machine learning: Compound-database bias",
        "2007.00407",
    ),
    ("d", "Directional Message Passing for Molecular Graphs", "2003.03123"),
    (
        "d",
        "Neural P3M: A Long-Range Interaction Modeling Enhancer for Geometric GNNs",
        "2409.17622",
    ),
    (
        "d",
        "Extending the RANGE of Graph Neural Networks: Relaying Attention Nodes for Global Encoding",
        "2502.13797",
    ),
    (
        "d",
        "Crystalite: A Lightweight Transformer for Efficient Crystal Modeling",
        "2604.02270",
    ),
]

# Left out of fulltexts.json on purpose so the "missing full text" path is testable.
MISSING_FULLTEXT_ID = "2604.02270"

THEMES = {
    "a": "long-range electrostatics in machine-learning potentials",
    "b": "large atomistic models and datasets",
    "c": "electrochemistry and interfaces",
    "d": "graph neural network architectures",
}

# Fictional PIs; paper picks are (cluster, index into that cluster's papers).
PIS = [
    (
        "Prof. Dr.",
        "Ariadne",
        "Thalassor",
        "Department of Chemistry",
        "TUM",
        "Theoretical Chemistry",
        [("a", 0), ("a", 1), ("a", 2), ("d", 1)],
    ),
    (
        "Dr.",
        "Cassiopeia",
        "Vellanor",
        "Department of Physics",
        "LMU",
        "Computational Materials Physics",
        [("a", 1), ("a", 3), ("b", 0), ("b", 4)],
    ),  # bridges clusters (a) and (b)
    (
        "Prof. Dr.",
        "Orpheus",
        "Draumheim",
        "Theory Department",
        "FHI",
        "Machine Learning for Materials",
        [("b", 0), ("b", 1), ("b", 3)],
    ),
    (
        "Dr.",
        "Selene",
        "Morvath",
        "Department of Chemistry",
        "TUM",
        "Data-Driven Catalysis",
        [("b", 1), ("b", 2), ("c", 0)],
    ),
    (
        "Prof. Dr.",
        "Lysander",
        "Quenwyck",
        "Department of Physics",
        "MPI FKF",
        "Electrochemical Interfaces",
        [("c", 0), ("c", 1), ("c", 2), ("a", 4)],
    ),
    (
        "Dr.",
        "Persephone",
        "Ilmarinen",
        "Department of Chemistry",
        "LMU",
        "Solvation and Redox Chemistry",
        [("c", 1), ("c", 3), ("a", 5)],
    ),
    (
        "Prof. Dr.",
        "Icarus",
        "Brenholt",
        "School of Computation, Information and Technology",
        "TUM",
        "Geometric Deep Learning",
        [("d", 0), ("d", 1), ("d", 2), ("d", 3)],
    ),
    (
        "Dr.",
        "Thalia",
        "Skarnvold",
        "Theory Department",
        "FHI",
        "Crystal Representation Learning",
        [("d", 2), ("d", 3), ("b", 4)],
    ),
]

# smid of the PI whose list carries the trailing-"}" DOI quirk, and which entry.
QUIRK_SMID, QUIRK_INDEX = "1006", 1


def log(msg: str) -> None:
    print(msg, flush=True)


def norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", " ".join(text.split()))
    return [p.strip() for p in parts if len(p.strip()) > 20]


class Fetcher:
    def __init__(self, cache: Path):
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=90, follow_redirects=True
        )
        self.last_request = 0.0

    def get(self, url: str, key: str) -> bytes:
        path = self.cache / key
        if path.exists():
            return path.read_bytes()
        wait = ARXIV_DELAY_S - (time.monotonic() - self.last_request)
        if wait > 0:
            time.sleep(wait)
        # export.arxiv.org intermittently answers 406 to httpx requests that urllib gets a 200
        # for (cause unknown; not headers or keep-alive), so the two clients alternate.
        for attempt in range(4):
            try:
                if attempt % 2 == 0:
                    r = self.client.get(url)
                    r.raise_for_status()
                    content = r.content
                else:
                    content = urllib.request.urlopen(url, timeout=90).read()
            except (httpx.HTTPError, urllib.error.URLError) as e:
                self.last_request = time.monotonic()
                log(f"    retry {attempt + 1}: {e}")
                time.sleep(ARXIV_DELAY_S * (attempt + 1))
            else:
                self.last_request = time.monotonic()
                path.write_bytes(content)
                return content
        raise RuntimeError(f"failed to fetch {url}")

    def search_title(self, title: str) -> ET.Element | None:
        # arXiv's query parser chokes on colons/parentheses; use the longest plain stretch as a phrase.
        phrase = max(re.split(r"[:()]", title), key=len).strip()
        q = 'ti:"' + phrase.replace(" ", "+") + '"'
        url = (
            f"{ARXIV_API}?search_query={urllib.parse.quote(q, safe='+:')}&max_results=5"
        )
        root = ET.fromstring(
            self.get(
                url, "search_" + hashlib.sha1(q.encode()).hexdigest()[:12] + ".xml"
            )
        )
        for entry in root.findall("atom:entry", NS):
            if norm_title(entry.findtext("atom:title", "", NS)) == norm_title(title):
                return entry
        return None

    def by_id(self, arxiv_id: str) -> ET.Element | None:
        url = f"{ARXIV_API}?id_list={arxiv_id}&max_results=1"
        root = ET.fromstring(self.get(url, f"id_{arxiv_id}.xml"))
        return root.find("atom:entry", NS)

    def licence(self, arxiv_id: str) -> str:
        url = f"{ARXIV_OAI}?verb=GetRecord&identifier=oai:arXiv.org:{arxiv_id}&metadataPrefix=arXiv"
        root = ET.fromstring(self.get(url, f"oai_{arxiv_id}.xml"))
        return (root.findtext(".//arxiv:license", "", NS) or "").strip()

    def pdf(self, arxiv_id: str) -> bytes:
        return self.get(f"https://arxiv.org/pdf/{arxiv_id}", f"{arxiv_id}.pdf")


def parse_entry(entry: ET.Element) -> dict:
    full_id = entry.findtext("atom:id", "", NS).rsplit("/", 1)[-1]
    arxiv_id = re.sub(r"v\d+$", "", full_id)
    return {
        "arxiv_id": arxiv_id,
        "doi": f"10.48550/arxiv.{arxiv_id}",
        "title": " ".join(entry.findtext("atom:title", "", NS).split()),
        "abstract": " ".join(entry.findtext("atom:summary", "", NS).split()),
        "authors": [
            " ".join(a.findtext("atom:name", "", NS).split())
            for a in entry.findall("atom:author", NS)
        ],
        "year": int(entry.findtext("atom:published", "", NS)[:4]),
    }


def is_open_licence(url: str) -> bool:
    return (
        "creativecommons.org/licenses/by/" in url
        or "creativecommons.org/publicdomain" in url
    )


def extract_pdf_markdown(pdf_bytes: bytes, cache: Path, arxiv_id: str) -> str:
    md_path = cache / f"{arxiv_id}.md"
    if md_path.exists():
        return md_path.read_text(encoding="utf-8")
    import pymupdf4llm

    tmp = cache / f"{arxiv_id}.tmp.pdf"
    tmp.write_bytes(pdf_bytes)
    # OCR off: it needs Tesseract on the build machine and only adds figure captions.
    text = pymupdf4llm.to_markdown(str(tmp), show_progress=False, use_ocr=False)
    tmp.unlink()
    md_path.write_text(text, encoding="utf-8")
    return text


SECTION_TEMPLATES = {
    "Introduction": [
        "Recent work has emphasised that {s}",
        "A recurring theme in the literature is that {s}",
        "It is now widely appreciated that {s}",
        "The motivation for the present study is the observation that {s}",
    ],
    "Methods": [
        "To address this, our protocol assumes that {s}",
        "In practice, the implementation relies on the fact that {s}",
        "The computational setup was chosen such that {s}",
        "For reproducibility we note that {s}",
    ],
    "Results": [
        "Our numerical experiments confirm that {s}",
        "Across all test cases we observe that {s}",
        "The data in this section indicate that {s}",
        "Quantitatively, the benchmark shows that {s}",
    ],
    "Discussion": [
        "These findings support the view that {s}",
        "A limitation worth stating explicitly is that {s}",
        "Looking ahead, we anticipate that {s}",
        "In summary, the central message is that {s}",
    ],
}


def lower_first(s: str) -> str:
    return s[0].lower() + s[1:] if s and not s[:2].isupper() else s


def synthetic_fulltext(paper: dict, rng: random.Random) -> str:
    sents = sentences(paper["abstract"]) or [paper["abstract"]]
    parts = [f"# {paper['title']}", "", "## Abstract", "", paper["abstract"], ""]
    body_len = 0
    while body_len < SYNTHETIC_MIN_CHARS:
        for section, templates in SECTION_TEMPLATES.items():
            order = sents[:]
            rng.shuffle(order)
            para = " ".join(
                rng.choice(templates).format(s=lower_first(s)) for s in order[:4]
            )
            parts += [f"## {section}", "", para, ""]
            body_len += len(para)
    return "\n".join(parts).strip() + "\n"


DATASET_DOI_RE = re.compile(
    r"10\.(?:5281/zenodo\.\d+|24435/materialscloud:[\w-]+|6084/m9\.figshare\.\d+)",
    re.IGNORECASE,
)


def find_dataset_doi(fulltext: str) -> str | None:
    m = DATASET_DOI_RE.search(fulltext)
    return m.group(0).lower() if m else None


def build_pis(papers: dict[str, list[dict]], today: str) -> list[dict]:
    focus_words = {
        "a": [
            "long-range electrostatics",
            "charge equilibration",
            "polarisable ML potentials",
        ],
        "b": [
            "universal atomistic models",
            "benchmark datasets",
            "foundation models for materials",
        ],
        "c": [
            "electrochemical interfaces",
            "redox chemistry in solution",
            "solvation free energies",
        ],
        "d": [
            "geometric GNNs",
            "attention for molecular graphs",
            "crystal transformers",
        ],
    }
    application = {
        "a": "molecular simulation",
        "b": "materials discovery",
        "c": "energy storage and conversion",
        "d": "molecular property prediction",
    }
    out = []
    for i, (title, first, last, dept, inst, focus_label, picks) in enumerate(PIS):
        smid = str(1001 + i)
        clusters = sorted(
            {c for c, _ in picks}, key=lambda c: -sum(1 for cc, _ in picks if cc == c)
        )
        focus = [focus_label] + [focus_words[c][i % 3] for c in clusters[:2]]
        dois = [papers[c][k]["doi"] for c, k in picks]
        if smid == QUIRK_SMID:
            dois[QUIRK_INDEX] += "}"
        out.append(
            {
                "smid": smid,
                "name": f"{title} {first} {last}",
                "title": title,
                "first_name": first,
                "last_name": last,
                "group": f"{last} Group",
                "department": dept,
                "institution": inst,
                "website": f"https://example.org/{last.lower()}",
                "profile_url": f"https://example.org/members/{smid}",
                "image_url": "",
                "research_focus": focus,
                "application_fields": sorted({application[c] for c in clusters[:2]}),
                "publication_dois": dois,
                "fetched_at": today,
            }
        )
    return out


def build_graph(pis: list[dict]) -> dict:
    G = nx.Graph(built_from="pis.json", n_pis=len(pis))
    clean = {
        p["smid"]: {d.rstrip("}").strip().lower() for d in p["publication_dois"]}
        for p in pis
    }
    for p in pis:
        G.add_node(
            p["smid"],
            name=p["name"],
            group=p["group"],
            institution=p["institution"],
            paper_count=len(p["publication_dois"]),
        )
    for i, a in enumerate(pis):
        for b in pis[i + 1 :]:
            shared = sorted(clean[a["smid"]] & clean[b["smid"]])
            if shared:
                G.add_edge(a["smid"], b["smid"], weight=len(shared), shared_dois=shared)
    return nx.node_link_data(G, edges="links")


PROPOSAL_SENTENCES = [
    "The cluster {name} brings together {n} principal investigators from {insts} around a single question: how can machine-learned models of matter capture physics that acts across many nanometres without sacrificing the locality that makes them tractable?",
    "Work on {theme} has shown that {s}",
    "{pi} and the {group} will contribute expertise in {focus}, building on their recent work on {paper}.",
    "A central hypothesis of the cluster is that {s}",
    "Methodologically, we will combine {theme_a} with {theme_b}, an approach whose feasibility is supported by the finding that {s}",
    "Preliminary results from the {group} indicate that {s}",
    "The training data for this effort will be curated jointly by the {group} and the {group2}, with all datasets published under open licences.",
    "We will benchmark every model against the criteria established in {paper}, extended with cluster-specific probes for {theme}.",
    "Deliverables include an open-source software stack, curated datasets, and a yearly summer school hosted alternately at {inst} and {inst2}.",
    "In the first funding period the emphasis lies on {theme}; the second period shifts towards transfer to {app}.",
    "Risk mitigation relies on the redundancy between {pi} and {pi2}, whose groups pursue complementary routes to the same milestone.",
    "The cluster's data stewardship plan follows FAIR principles and is coordinated by the {group}.",
    "Early-career researchers will rotate between {inst} and {inst2} so that each doctoral project is supervised by two principal investigators.",
    "Beyond its scientific goals, {name} will deliver a reference implementation that external groups can adopt for {app}.",
    "This line of research builds on the insight that {s}",
]

PROPOSAL_STRUCTURE = [
    ("# e-toy: Learning Long-Range Physics (EXC 0000)", 0),
    ("## Summary", 3),
    ("## Scientific Context", 4),
    ("## Research Area A: Long-Range Electrostatics", 2),
    ("## Research Area B: Large Atomistic Models and Datasets", 2),
    ("## Research Area C: Electrochemistry and Interfaces", 2),
    ("## Research Area D: Graph Neural Network Architectures", 2),
    ("## Work Package 1: Physically Constrained Long-Range Models", 3),
    ("## Work Package 2: Data Generation and Foundation Models", 3),
    ("## Work Package 3: Electrified Interfaces", 3),
    ("## Work Package 4: Architectures and Software", 3),
    ("## Principal Investigators", 8),
    ("## Infrastructure and Data Management", 2),
    ("## Timeline and Milestones", 2),
    ("## Expected Impact", 2),
]


def build_proposal(
    papers: dict[str, list[dict]], pis: list[dict], rng: random.Random
) -> str:
    all_papers = [p for c in "abcd" for p in papers[c]]
    pool = [s for p in all_papers for s in sentences(p["abstract"])]
    insts = sorted({p["institution"] for p in pis})
    themes = list(THEMES.values())
    apps = [
        "battery electrolytes",
        "heterogeneous catalysis",
        "polar functional materials",
        "aqueous electrochemistry",
    ]

    def fill(template: str) -> str:
        a, b = rng.sample(themes, 2)
        pi, pi2 = rng.sample(pis, 2)
        i1, i2 = rng.sample(insts, 2)
        return template.format(
            name="e-toy",
            n=len(pis),
            insts=", ".join(insts),
            theme=rng.choice(themes),
            theme_a=a,
            theme_b=b,
            s=lower_first(rng.choice(pool)),
            pi=pi["name"],
            pi2=pi2["name"],
            group=pi["group"],
            group2=pi2["group"],
            focus=rng.choice(pi["research_focus"]),
            paper=f'"{rng.choice(all_papers)["title"]}"',
            inst=i1,
            inst2=i2,
            app=rng.choice(apps),
        )

    def paragraph(n_sentences: int) -> str:
        return " ".join(fill(t) for t in rng.sample(PROPOSAL_SENTENCES, n_sentences))

    out = []
    for heading, n_paragraphs in PROPOSAL_STRUCTURE:
        out.append(heading)
        if heading.startswith("## Principal Investigators"):
            for pi in pis:
                out.append(
                    f"{pi['name']} ({pi['group']}, {pi['institution']}, {pi['department']}) leads research on "
                    f"{', '.join(pi['research_focus'])} with applications in {' and '.join(pi['application_fields'])}. "
                    + " ".join(fill(t) for t in rng.sample(PROPOSAL_SENTENCES[1:], 2))
                )
            continue
        for k in range(n_paragraphs):
            para = paragraph(rng.randint(2, 3))
            if heading.startswith("## Work Package 3") and k == 0:
                para += " All interface simulations described in work package 3 share the constant-potential infrastructure developed in Work Package 1."
            out.append(para)
    return "\n\n".join(out) + "\n"


def build_summary(pis: list[dict]) -> str:
    lines = [
        "# e-toy: Learning Long-Range Physics (EXC 0000) - Summary",
        "",
        "The fictional cluster e-toy unites eight principal investigators at TUM, LMU, FHI and MPI FKF.",
        "Its goal is to make machine-learned models of matter aware of long-range physics.",
        "Four research areas structure the work: long-range electrostatics in ML potentials,",
        "large atomistic models and datasets, electrochemistry and interfaces, and GNN architectures.",
        "Work Package 1 develops physically constrained long-range models.",
        "Work Package 2 generates training data and trains foundation models.",
        "Work Package 3 targets electrified solid-liquid interfaces at constant potential.",
        "Work Package 4 delivers architectures and an open-source software stack.",
        f"Principal investigators: {', '.join(p['name'] for p in pis)}.",
        "Doctoral researchers are co-supervised across institutions and rotate between sites.",
        "All datasets and code are published under open licences following FAIR principles.",
        "Milestones are reviewed yearly; the second funding period shifts towards applications.",
        "This document is synthetic test data and does not describe a real funding proposal.",
    ]
    return "\n".join(lines) + "\n"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument(
        "--cache", type=Path, default=Path(tempfile.gettempdir()) / "cra-toy" / "cache"
    )
    args = ap.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    rng = random.Random(SEED)
    fetcher = Fetcher(args.cache)

    papers: dict[str, list[dict]] = {c: [] for c in THEMES}
    licences: dict[str, str] = {}
    for cluster, title, pinned in PAPERS:
        log(f"[arxiv] {title[:60]}")
        entry = fetcher.search_title(title)
        if entry is None:
            log(f"    title search failed, using pinned id {pinned}")
            entry = fetcher.by_id(pinned)
        if entry is None:
            log("    not found, skipping")
            continue
        paper = parse_entry(entry)
        if paper["arxiv_id"] != pinned:
            log(f"    resolved {paper['arxiv_id']} (pinned {pinned})")
        paper["cluster"] = cluster
        papers[cluster].append(paper)
    flat = sorted((p for c in papers.values() for p in c), key=lambda p: p["doi"])
    log(f"[arxiv] {len(flat)} papers resolved")

    for p in flat:
        licences[p["arxiv_id"]] = fetcher.licence(p["arxiv_id"])
        log(f"[licence] {p['arxiv_id']}: {licences[p['arxiv_id']] or '(none)'}")

    fulltexts: dict[str, dict] = {}
    for p in flat:
        if p["arxiv_id"] == MISSING_FULLTEXT_ID:
            continue
        if is_open_licence(licences[p["arxiv_id"]]):
            log(f"[pdf] {p['arxiv_id']}")
            text = extract_pdf_markdown(
                fetcher.pdf(p["arxiv_id"]), args.cache, p["arxiv_id"]
            )[:FULLTEXT_MAX_CHARS]
            source, origin = "pdf", "arxiv"
        else:
            text = synthetic_fulltext(p, rng)
            source, origin = "synthetic", "synthetic"
        fulltexts[p["doi"]] = {
            "fulltext": text,
            "source": source,
            "source_origin": origin,
            "url": f"https://arxiv.org/abs/{p['arxiv_id']}",
            "char_count": len(text),
            "fetched_at": today,
        }
    p_lic = {p["doi"]: licences[p["arxiv_id"]] for p in flat}
    p_source = {d: v["source"] for d, v in fulltexts.items()}

    # One dataset row for cluster (b), only if a real full text names a dataset DOI.
    dataset_row = None
    for p in papers["b"]:
        ft = fulltexts.get(p["doi"])
        if ft and ft["source"] == "pdf" and (ds := find_dataset_doi(ft["fulltext"])):
            dataset_row = (p, ds)
            log(f"[dataset] {p['arxiv_id']} names {ds}")
            break

    with (out / "papers.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
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
        for p in flat:
            w.writerow(
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
            if dataset_row and dataset_row[0] is p:
                w.writerow(
                    [
                        p["doi"],
                        p["title"],
                        " and ".join(p["authors"]),
                        p["year"],
                        "",
                        dataset_row[1],
                        f"Dataset accompanying: {p['title']}",
                        "dataset",
                    ]
                )

    abstracts = {
        p["doi"]: {
            "abstract": p["abstract"],
            "source": "arxiv",
            "authors": p["authors"],
            "journal": "arXiv",
            "citation_count": 0,
        }
        for p in flat
    }
    pis = build_pis(papers, today)
    graph = build_graph(pis)
    proposal = build_proposal(papers, pis, rng)

    def dump(name: str, obj) -> None:
        (out / name).write_text(
            json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    dump("abstracts.json", abstracts)
    dump("fulltexts.json", fulltexts)
    dump("pis.json", pis)
    dump("graph.json", graph)
    (out / "proposal.md").write_text(proposal, encoding="utf-8")
    (out / "proposal_summary.md").write_text(build_summary(pis), encoding="utf-8")

    log(f"[embed] loading {EMBEDDING_MODEL}")
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(EMBEDDING_MODEL, device="cpu")
    texts = [f"{p['title'].strip()}. {p['abstract'].strip()}".strip(". ") for p in flat]
    vectors = model.encode(
        texts, normalize_embeddings=True, convert_to_numpy=True
    ).astype(np.float32)
    np.savez(
        out / "embeddings.npz",
        dois=np.array([p["doi"] for p in flat], dtype=object),
        vectors=vectors,
        model=EMBEDDING_MODEL,
    )
    log(f"[embed] {vectors.shape}")

    files = sorted(
        f for f in out.iterdir() if f.is_file() and f.name != "manifest.json"
    )
    manifest = {
        "schema_version": "1.0",
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "builder": "developer/make_toy_corpus.py",
        "embedding_model": EMBEDDING_MODEL,
        "files": {
            f.name: {"sha256": sha256(f), "bytes": f.stat().st_size} for f in files
        },
        "counts": {
            "papers": len(flat),
            "abstracts": len(abstracts),
            "fulltexts": len(fulltexts),
            "pis": len(pis),
            "graph_nodes": len(graph["nodes"]),
            "graph_edges": len(graph["links"]),
            "embeddings": len(flat),
        },
        "provenance": {
            "metadata": "arXiv API (CC0)",
            "fulltexts": "arXiv CC BY PDFs or synthetic",
            "pis": "fictional",
            "proposal": "fictional",
        },
    }
    dump("manifest.json", manifest)

    log("\narxiv_id      cluster  fulltext   licence")
    for p in flat:
        log(
            f"{p['arxiv_id']:<13} {p['cluster']:<8} {p_source.get(p['doi'], 'MISSING'):<10} {p_lic[p['doi']] or '(none)'}"
        )
    total = sum(f.stat().st_size for f in out.iterdir())
    log(f"\nbundle: {total / 1024:.0f} kB in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
