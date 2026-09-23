import json
from pathlib import Path

import pytest
from library_builder import write_library

from cra.core.library import manifest
from cra.core.library.library import Library, LibraryError
from cra.core.library.records import normalise_doi

TOY = Path(__file__).resolve().parent / "data" / "library"


@pytest.fixture
def library_dir(tmp_path):
    return write_library(tmp_path / "library")


def test_papers_merge_csv_rows_and_abstract_metadata(library_dir):
    library = Library.load(library_dir)
    assert list(library.papers) == ["10.1000/alpha", "10.1000/beta", "10.1000/gamma"]
    alpha = library.paper("10.1000/ALPHA")
    assert alpha.authors == ("Ada Lovelace", "Grace Hopper")
    assert alpha.journal == "Journal of Toy Science"
    assert alpha.citation_count == 3
    assert alpha.abstract.startswith("We study perovskite")
    assert [d.doi for d in alpha.datasets] == ["10.5281/zenodo.1"]
    gamma = library.paper("10.1000/gamma")
    assert gamma.abstract == ""
    assert gamma.journal == ""
    assert gamma.citation_count is None


def test_counts_and_availability(library_dir):
    library = Library.load(library_dir)
    assert library.counts == {
        "papers": 3,
        "abstracts": 2,
        "fulltexts": 2,
        "pis": 3,
        "graph_nodes": 3,
        "graph_edges": 2,
        "embeddings": 3,
        "map_points": 3,
    }
    assert all(library.available.values())
    assert library.fulltext("10.1000/alpha").char_count > 0
    assert library.fulltext("10.1000/gamma") is None
    assert (
        library.proposal.paragraphs[2]
        == "Work Package 1 covers perovskite solar cells."
    )
    assert library.embeddings.index["10.1000/beta"] == 1
    assert library.graph.number_of_edges() == 2


def test_pi_dois_are_normalised_against_the_brace_quirk(library_dir):
    library = Library.load(library_dir)
    assert library.pis[0].publication_dois == ("10.1000/alpha", "10.1000/beta")


def test_graph_edges_are_deduplicated_against_the_brace_quirk(library_dir):
    """The real bundle carries every shared DOI twice on some edges, once with
    a stray brace, and a weight that counts both."""
    path = library_dir / "graph.json"
    raw = json.loads(path.read_text())
    link = next(link for link in raw["links"] if link["weight"] == 2)
    link["shared_dois"] = [
        "10.1000/alpha",
        "10.1000/alpha}",
        "10.1000/BETA",
        "10.1000/beta}",
    ]
    link["weight"] = 4
    path.write_text(json.dumps(raw))
    library = Library.load(library_dir, verify=False)
    data = library.graph.edges[link["source"], link["target"]]
    assert data["shared_dois"] == ["10.1000/alpha", "10.1000/beta"]
    assert data["weight"] == 2


@pytest.mark.parametrize(
    "missing",
    ["abstracts", "fulltexts", "pis", "embeddings", "graph", "proposal", "map"],
)
def test_optional_files_switch_capabilities_off(tmp_path, missing):
    key = "publication_map" if missing == "map" else missing
    directory = write_library(tmp_path / "c", **{key: False})
    library = Library.load(directory)
    assert library.available[missing] is False
    assert library.available["papers"] is True


def test_precomputed_map_is_loaded_with_every_clustering(library_dir):
    publication_map = Library.load(library_dir).map
    assert publication_map.dois == ("10.1000/alpha", "10.1000/beta", "10.1000/gamma")
    assert publication_map.available_counts == (2, 3)
    assert publication_map.clusters[2] == ((0, 1, 1), ("perovskite", "copper"))
    assert publication_map.model == "fake"


@pytest.mark.parametrize(("asked", "chosen"), [(1, 2), (2, 2), (3, 3), (8, 3)])
def test_map_falls_back_to_the_nearest_precomputed_clustering(
    library_dir, asked, chosen
):
    assert Library.load(library_dir).map.nearest_count(asked) == chosen


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
def test_inconsistent_map_is_rejected(library_dir, break_it, message):
    data = json.loads((library_dir / "publication_map.json").read_text())
    break_it(data)
    (library_dir / "publication_map.json").write_text(json.dumps(data))
    with pytest.raises(LibraryError, match=message):
        Library.load(library_dir, verify=False)


def test_papers_csv_is_required(tmp_path):
    directory = write_library(tmp_path / "c")
    (directory / "papers.csv").unlink()
    manifest.write(directory, {})
    with pytest.raises(LibraryError, match="papers.csv missing"):
        Library.load(directory)


def test_tampered_file_is_rejected(library_dir):
    (library_dir / "pis.json").write_text("[]")
    with pytest.raises(LibraryError, match="pis.json: checksum mismatch"):
        Library.load(library_dir)
    assert Library.load(library_dir, verify=False).pis == ()


def test_wrong_counts_are_rejected(library_dir):
    data = json.loads((library_dir / "manifest.json").read_text())
    data["counts"]["pis"] = 99
    (library_dir / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(LibraryError, match="count pis"):
        Library.load(library_dir)


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
def test_schema_version_requirement(library_dir, version, required, ok):
    data = json.loads((library_dir / "manifest.json").read_text())
    data["schema_version"] = version
    (library_dir / "manifest.json").write_text(json.dumps(data))
    if ok:
        Library.load(library_dir, required_schema=required)
    else:
        with pytest.raises(LibraryError, match="schema version"):
            Library.load(library_dir, required_schema=required)


def test_missing_manifest_is_rejected_unless_unverified(tmp_path):
    directory = write_library(tmp_path / "c", with_manifest=False)
    with pytest.raises(LibraryError, match="manifest.json missing"):
        Library.load(directory)
    assert Library.load(directory, verify=False).counts["papers"] == 3


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
    library = Library.load(TOY)
    assert library.counts["papers"] >= 12
    assert library.embeddings.vectors.shape[1] == 384
    assert len(library.pis) == 8
    assert set(library.embeddings.dois) == set(library.papers)
    assert set(library.map.dois) == set(library.papers)
    assert library.map.available_counts[0] == 2


@pytest.mark.slow
def test_building_the_map_reproduces_the_committed_toy_bundle():
    """The expensive path, exercised only on demand: umap plus scikit-learn."""
    pytest.importorskip("umap")
    from cra.core.library.derive import build_map

    library = Library.load(TOY)
    payload = build_map(
        library.embeddings.dois,
        library.embeddings.vectors,
        {doi: paper.title for doi, paper in library.papers.items()},
        model=library.embeddings.model,
    )
    committed = json.loads((TOY / "publication_map.json").read_text())
    assert payload["dois"] == committed["dois"]
    assert payload["clusters"].keys() == committed["clusters"].keys()
    assert payload["x"] == pytest.approx(committed["x"], abs=1e-3)
