import json
from pathlib import Path

import pytest
from corpus_builder import write_corpus

from cra.core.corpus import manifest
from cra.core.corpus.corpus import Corpus, CorpusError
from cra.core.corpus.records import normalise_doi

TOY = Path(__file__).resolve().parent / "data" / "corpus"


@pytest.fixture
def corpus_dir(tmp_path):
    return write_corpus(tmp_path / "corpus")


def test_papers_merge_csv_rows_and_abstract_metadata(corpus_dir):
    corpus = Corpus.load(corpus_dir)
    assert list(corpus.papers) == ["10.1000/alpha", "10.1000/beta", "10.1000/gamma"]
    alpha = corpus.paper("10.1000/ALPHA")
    assert alpha.authors == ("Ada Lovelace", "Grace Hopper")
    assert alpha.journal == "Journal of Toy Science"
    assert alpha.citation_count == 3
    assert alpha.abstract.startswith("We study perovskite")
    assert [d.doi for d in alpha.datasets] == ["10.5281/zenodo.1"]
    gamma = corpus.paper("10.1000/gamma")
    assert gamma.abstract == ""
    assert gamma.journal == ""
    assert gamma.citation_count is None


def test_counts_and_availability(corpus_dir):
    corpus = Corpus.load(corpus_dir)
    assert corpus.counts == {
        "papers": 3,
        "abstracts": 2,
        "fulltexts": 2,
        "pis": 3,
        "graph_nodes": 3,
        "graph_edges": 2,
        "embeddings": 3,
        "map_points": 3,
    }
    assert all(corpus.available.values())
    assert corpus.fulltext("10.1000/alpha").char_count > 0
    assert corpus.fulltext("10.1000/gamma") is None
    assert (
        corpus.proposal.paragraphs[2] == "Work Package 1 covers perovskite solar cells."
    )
    assert corpus.embeddings.index["10.1000/beta"] == 1
    assert corpus.graph.number_of_edges() == 2


def test_pi_dois_are_normalised_against_the_brace_quirk(corpus_dir):
    corpus = Corpus.load(corpus_dir)
    assert corpus.pis[0].publication_dois == ("10.1000/alpha", "10.1000/beta")


@pytest.mark.parametrize(
    "missing",
    ["abstracts", "fulltexts", "pis", "embeddings", "graph", "proposal", "map"],
)
def test_optional_files_switch_capabilities_off(tmp_path, missing):
    key = "corpus_map" if missing == "map" else missing
    directory = write_corpus(tmp_path / "c", **{key: False})
    corpus = Corpus.load(directory)
    assert corpus.available[missing] is False
    assert corpus.available["papers"] is True


def test_precomputed_map_is_loaded_with_every_clustering(corpus_dir):
    corpus_map = Corpus.load(corpus_dir).map
    assert corpus_map.dois == ("10.1000/alpha", "10.1000/beta", "10.1000/gamma")
    assert corpus_map.available_counts == (2, 3)
    assert corpus_map.clusters[2] == ((0, 1, 1), ("perovskite", "copper"))
    assert corpus_map.model == "fake"


@pytest.mark.parametrize(("asked", "chosen"), [(1, 2), (2, 2), (3, 3), (8, 3)])
def test_map_falls_back_to_the_nearest_precomputed_clustering(
    corpus_dir, asked, chosen
):
    assert Corpus.load(corpus_dir).map.nearest_count(asked) == chosen


@pytest.mark.parametrize(
    ("break_it", "message"),
    [
        (lambda d: d.update(x=[0.0]), "one coordinate pair per DOI"),
        (
            lambda d: d["clusters"]["2"].update(assignments=[0]),
            "does not cover every DOI",
        ),
        (lambda d: d.update(clusters={}), "no clusterings"),
    ],
)
def test_inconsistent_map_is_rejected(corpus_dir, break_it, message):
    data = json.loads((corpus_dir / "corpus_map.json").read_text())
    break_it(data)
    (corpus_dir / "corpus_map.json").write_text(json.dumps(data))
    with pytest.raises(CorpusError, match=message):
        Corpus.load(corpus_dir, verify=False)


def test_papers_csv_is_required(tmp_path):
    directory = write_corpus(tmp_path / "c")
    (directory / "papers.csv").unlink()
    manifest.write(directory, {})
    with pytest.raises(CorpusError, match="papers.csv missing"):
        Corpus.load(directory)


def test_tampered_file_is_rejected(corpus_dir):
    (corpus_dir / "pis.json").write_text("[]")
    with pytest.raises(CorpusError, match="pis.json: checksum mismatch"):
        Corpus.load(corpus_dir)
    assert Corpus.load(corpus_dir, verify=False).pis == ()


def test_wrong_counts_are_rejected(corpus_dir):
    data = json.loads((corpus_dir / "manifest.json").read_text())
    data["counts"]["pis"] = 99
    (corpus_dir / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(CorpusError, match="count pis"):
        Corpus.load(corpus_dir)


@pytest.mark.parametrize(
    ("version", "required", "ok"),
    [
        ("1.0", "1.x", True),
        ("1.3", "1.x", True),
        ("2.0", "1.x", False),
        ("1.0", "1.0", True),
        ("1.1", "1.0", False),
    ],
)
def test_schema_version_requirement(corpus_dir, version, required, ok):
    data = json.loads((corpus_dir / "manifest.json").read_text())
    data["schema_version"] = version
    (corpus_dir / "manifest.json").write_text(json.dumps(data))
    if ok:
        Corpus.load(corpus_dir, required_schema=required)
    else:
        with pytest.raises(CorpusError, match="schema version"):
            Corpus.load(corpus_dir, required_schema=required)


def test_missing_manifest_is_rejected_unless_unverified(tmp_path):
    directory = write_corpus(tmp_path / "c", with_manifest=False)
    with pytest.raises(CorpusError, match="manifest.json missing"):
        Corpus.load(directory)
    assert Corpus.load(directory, verify=False).counts["papers"] == 3


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1000/ABC", "10.1000/abc"),
        ("https://doi.org/10.1000/abc", "10.1000/abc"),
        (" 10.1000/abc} ", "10.1000/abc"),
        ("doi:10.1000/abc", "10.1000/abc"),
    ],
)
def test_normalise_doi(raw, expected):
    assert normalise_doi(raw) == expected


@pytest.mark.skipif(not TOY.exists(), reason="toy bundle not built yet")
def test_toy_bundle_loads_and_matches_its_manifest():
    corpus = Corpus.load(TOY)
    assert corpus.counts["papers"] >= 12
    assert corpus.embeddings.vectors.shape[1] == 384
    assert len(corpus.pis) == 8
    assert set(corpus.embeddings.dois) == set(corpus.papers)
    assert set(corpus.map.dois) == set(corpus.papers)
    assert corpus.map.available_counts[0] == 2


@pytest.mark.slow
def test_building_the_map_reproduces_the_committed_toy_bundle():
    """The expensive path, exercised only on demand: umap plus scikit-learn."""
    pytest.importorskip("umap")
    from cra.core.corpus.derive import build_map

    corpus = Corpus.load(TOY)
    payload = build_map(
        corpus.embeddings.dois,
        corpus.embeddings.vectors,
        {doi: paper.title for doi, paper in corpus.papers.items()},
        model=corpus.embeddings.model,
    )
    committed = json.loads((TOY / "corpus_map.json").read_text())
    assert payload["dois"] == committed["dois"]
    assert payload["clusters"].keys() == committed["clusters"].keys()
    assert payload["x"] == pytest.approx(committed["x"], abs=1e-3)
