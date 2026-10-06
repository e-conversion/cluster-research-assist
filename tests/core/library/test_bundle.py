import json
import shutil

import pytest
from conftest import TOY_LIBRARY
from library_builder import write_library

from cra.core.library import manifest
from cra.core.library.bundle import differences, migrate
from cra.core.library.library import Library, LibraryError


@pytest.fixture
def old(tmp_path):
    return write_library(tmp_path / "old", schema="1.0")


def test_migrate_keeps_every_record(old, tmp_path):
    new = migrate(old, tmp_path / "new", builder="test")
    assert new.schema_version == "2.0"
    assert differences(Library.load(old), new) == []
    assert manifest.read(tmp_path / "new")["schema_version"] == "2.0"


def test_migrate_writes_the_records_already_clean(old, tmp_path):
    migrate(old, tmp_path / "new", builder="test")
    files = {p.name for p in (tmp_path / "new").iterdir()}
    assert "papers.json" in files
    assert not {"papers.csv", "abstracts.json"} & files
    pis = json.loads((tmp_path / "new" / "pis.json").read_text())
    assert pis[0]["publication_dois"] == ["10.1000/alpha", "10.1000/beta"]
    papers = json.loads((tmp_path / "new" / "papers.json").read_text())
    alpha = next(p for p in papers if p["doi"] == "10.1000/alpha")
    assert alpha["datasets"] == [{"doi": "10.5281/zenodo.1", "title": "Raw spectra"}]
    assert alpha["journal"] == "Journal of Toy Science"


def test_migrate_converts_the_toy_bundle(tmp_path):
    new = migrate(TOY_LIBRARY, tmp_path / "new", builder="test")
    assert differences(Library.load(TOY_LIBRARY), new) == []
    assert new.counts == Library.load(TOY_LIBRARY).counts


def test_migrate_refuses_a_target_that_is_not_empty(old, tmp_path):
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / "keep.txt").write_text("mine")
    with pytest.raises(LibraryError, match="not empty"):
        migrate(old, tmp_path / "new", builder="test")
    assert (tmp_path / "new" / "keep.txt").read_text() == "mine"


def test_migrate_refuses_to_write_over_its_source(old):
    with pytest.raises(LibraryError, match="new directory"):
        migrate(old, old, builder="test")


def test_migrate_refuses_a_bundle_that_is_already_2_0(tmp_path):
    current = write_library(tmp_path / "current")
    with pytest.raises(LibraryError, match="schema version '2.0'"):
        migrate(current, tmp_path / "new", builder="test")


def test_migrate_refuses_an_unverified_source(old, tmp_path):
    (old / "pis.json").write_text("[]")
    with pytest.raises(LibraryError, match="checksum mismatch"):
        migrate(old, tmp_path / "new", builder="test")


def test_differences_name_what_changed(old, tmp_path):
    shutil.copytree(old, tmp_path / "changed")
    (tmp_path / "changed" / "pis.json").write_text("[]")
    found = differences(
        Library.load(old), Library.load(tmp_path / "changed", verify=False)
    )
    assert "PIs missing: 1, 2, 3" in found
    assert any(f.startswith("counts differ") for f in found)


def test_differences_name_a_few_and_count_the_rest(old, tmp_path):
    shutil.copytree(old, tmp_path / "changed")
    path = tmp_path / "changed" / "papers.csv"
    path.write_text(path.read_text().replace("Battery", "Bakery"))
    found = differences(
        Library.load(old), Library.load(tmp_path / "changed", verify=False)
    )
    assert found == ["papers changed: 10.1000/gamma"]


def test_a_failed_migration_leaves_no_half_written_target(old, tmp_path, monkeypatch):
    from cra.core.library import bundle

    monkeypatch.setattr(bundle, "differences", lambda *_: ["papers differ"])
    with pytest.raises(LibraryError, match="conversion changed the data"):
        migrate(old, tmp_path / "new", builder="test")
    assert not (tmp_path / "new").exists()
