# Toy corpus (`tests/data/corpus/`)

A small, redistributable bundle of 19 arXiv preprints in four overlapping
topical clusters, plus fictional PIs, a fictional proposal and precomputed
embeddings. Built by `developer/make_toy_corpus.py`; every file's SHA-256 is
recorded in `manifest.json`.

## Provenance

Metadata (title, abstract, authors, year) comes from the arXiv API and is CC0.
Full text is taken from the arXiv PDF only where the arXiv licence is CC BY;
all other papers get a synthetic body generated from the abstract (title as H1,
the abstract, then generic "Introduction / Methods / Results / Discussion"
sections built from shuffled, templated abstract sentences). The DOI of each
paper is `10.48550/arxiv.<id>`.

| Cluster | arXiv id | Title | Licence | Full text |
|---|---|---|---|---|
| a | 2602.11071 | Long-range electrostatics in atomistic machine learning: a physical perspective | arXiv non-exclusive | synthetic |
| a | 2412.01642 | Electrostatic interactions in atomistic and machine-learned potentials for polar materials | CC BY 4.0 | real (PDF) |
| a | 2406.17595 | Density-Based Long-Range Electrostatic Descriptors for Machine Learning Force Fields | CC BY 4.0 | real (PDF) |
| a | 2412.15455 | Learning charges and long-range interactions from energies and forces | arXiv non-exclusive | synthetic |
| a | 2412.11569 | The dark side of the forces: assessing non-conservative force models for atomistic machine learning | arXiv non-exclusive | synthetic |
| a | 2602.19411 | MACE-POLAR-1: A Polarisable Electrostatic Foundation Model for Molecular Chemistry | arXiv non-exclusive | synthetic |
| b | 2405.04967 | MatterSim: A Deep Learning Atomistic Model Across Elements, Temperatures and Pressures | arXiv non-exclusive | synthetic |
| b | 2505.08762 | The Open Molecules 2025 (OMol25) Dataset, Evaluations, and Models | arXiv non-exclusive | synthetic |
| b | 2206.08917 | The Open Catalyst 2022 (OC22) Dataset and Challenges for Oxide Electrocatalysts | arXiv non-exclusive | synthetic |
| b | 2504.19578 | LAMBench: A Benchmark for Large Atomistic Models | CC BY 4.0 | real (PDF) |
| b | 2603.02089 | High-quality, high-information datasets for universal atomistic machine learning | arXiv non-exclusive | synthetic |
| c | 2608.14153 | Electrostatic Phenomenology Benchmarks for Machine-Learned Interatomic Potentials in Electrochemistry: Beyond the Energy-Force Metric | arXiv non-exclusive | synthetic |
| c | 2410.03299 | Machine learning potentials for redox chemistry in solution | CC BY 4.0 | real (PDF) |
| c | 2506.10548 | Machine learning accelerated finite-field simulations for electrochemical interfaces | arXiv non-exclusive | synthetic |
| c | 2007.00407 | Hydration free energies from kernel-based machine learning: Compound-database bias | arXiv non-exclusive | synthetic |
| d | 2003.03123 | Directional Message Passing for Molecular Graphs | arXiv non-exclusive | synthetic |
| d | 2409.17622 | Neural P$^3$M: A Long-Range Interaction Modeling Enhancer for Geometric GNNs | CC BY 4.0 | real (PDF) |
| d | 2502.13797 | Extending the RANGE of Graph Neural Networks: Relaying Attention Nodes for Global Encoding | CC BY 4.0 | real (PDF) |
| d | 2604.02270 | Crystalite: A Lightweight Transformer for Efficient Crystal Modeling | arXiv non-exclusive | none (deliberately missing) |

"arXiv non-exclusive" is `http://arxiv.org/licenses/nonexclusive-distrib/1.0/`,
which does not permit redistribution of the full text. Real full texts are
`pymupdf4llm` Markdown truncated to 20 000 characters.

Substitutions (the requested paper is not on arXiv): OC25 -> OC22
(2206.08917); "Bulk electricity storage in 1-nm water channels" -> 2506.10548;
"Computing hydration free energies of small molecules with first principles
accuracy" -> 2007.00407.

## Deliberate quirks for tests

- `10.48550/arxiv.2604.02270` (Crystalite) has no entry in `fulltexts.json`,
  so the missing-full-text path is exercised.
- PI `1006` (Dr. Persephone Ilmarinen) lists `10.48550/arxiv.2007.00407}` with
  a trailing `}`; this reproduces a real data quirk the loader MUST tolerate.
  `graph.json` was built with that DOI normalised.
- `papers.csv` has no dataset row: none of the CC BY cluster (b) full texts
  names a dataset DOI (Zenodo, Materials Cloud, figshare), so all
  `dataset_*` columns are empty.

## Fictional parts

PIs (`pis.json`), the co-authorship graph (`graph.json`) and the proposal
(`proposal.md`, `proposal_summary.md`) are invented. PI names are mythological
or made up and are not the papers' authors. PI `1002` bridges clusters (a) and
(b); the graph has 8 nodes and 8 edges.

## Rebuild

```sh
uv venv /tmp/cra-toy/venv
uv pip install --python /tmp/cra-toy/venv/bin/python \
    sentence-transformers httpx numpy networkx pymupdf4llm
/tmp/cra-toy/venv/bin/python developer/make_toy_corpus.py tests/data/corpus
```

The script resolves each title through the arXiv API (falling back to the
pinned id), reads licences from the arXiv OAI endpoint, downloads CC BY PDFs,
and embeds `f"{title}. {abstract}"` with `BAAI/bge-small-en-v1.5`
(L2-normalised, 384-d). Network responses are cached under `--cache`
(default: the system temp dir), so a rerun is fast. All fictional content uses
`random.Random(42)`; only `fetched_at`/`built_at` and any upstream metadata
edits change between rebuilds. Rebuilding after arXiv metadata changes
(e.g. a new paper version) will change abstracts, embeddings and hashes.

## Derived artifacts

`corpus_map.json` is produced by `cra corpus build` (needs the `build` extra)
and holds the 2-D projection plus one clustering per count the UI offers, so
that serving the corpus map costs no computation. Rebuild it after changing
the papers or the embeddings.
