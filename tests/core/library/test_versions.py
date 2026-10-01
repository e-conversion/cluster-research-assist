"""A library directory that can be replaced while the service runs."""

import shutil
import tarfile

import pytest
from library_builder import write_library

from cra.core.library.library import Library
from cra.core.library.versions import (
    LibraryLayoutError,
    activate,
    init_root,
    is_bundle,
    prune,
    resolve,
    unpack,
    versions,
    writable,
)


@pytest.fixture
def bundle(tmp_path):
    return write_library(tmp_path / "bundle")


@pytest.fixture
def root(tmp_path, bundle):
    root = tmp_path / "library"
    init_root(root, bundle)
    return root


def archive_of(directory, path, inside=None):
    with tarfile.open(path, "w:gz") as tar:
        tar.add(directory, arcname=inside or ".")
    return path


def test_a_plain_bundle_is_used_as_is_and_is_not_writable(bundle):
    assert resolve(bundle) == bundle
    assert writable(bundle) is False
    assert Library.load(bundle).counts["papers"] == 3


def test_a_root_resolves_to_its_active_version(root):
    active = resolve(root)
    assert active.parent.name == "versions"
    assert is_bundle(active)
    assert writable(root) is True
    assert Library.load(root).counts["papers"] == 3


def test_a_root_without_an_active_version_says_so(tmp_path):
    root = tmp_path / "library"
    (root / "versions").mkdir(parents=True)
    with pytest.raises(LibraryLayoutError, match="no active version"):
        resolve(root)


@pytest.mark.parametrize("inside", [None, "bundle"], ids=["flat", "one directory"])
def test_uploading_a_new_version_leaves_the_live_one_alone(
    tmp_path, root, bundle, inside
):
    live = resolve(root)
    shutil.rmtree(bundle / "pis.json", ignore_errors=True)
    (bundle / "pis.json").write_text("[]")
    tarball = archive_of(bundle, tmp_path / "new.tar.gz", inside)

    installed = unpack(tarball, root, name="second")
    assert is_bundle(installed)
    assert resolve(root) == live, "the live version must not move until activated"

    activate(root, "second")
    assert resolve(root) == installed.resolve()
    assert [v.name for v in versions(root) if v.active] == ["second"]


def test_rolling_back_is_moving_the_link(root, tmp_path, bundle):
    first = versions(root)[0].name
    unpack(archive_of(bundle, tmp_path / "n.tar.gz"), root, name="second")
    activate(root, "second")
    activate(root, first)
    assert resolve(root).name == first
    assert {v.name for v in versions(root)} == {first, "second"}


def test_an_archive_without_a_manifest_is_refused(tmp_path, root):
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "papers.csv").write_text("article_doi\n")
    before = [v.name for v in versions(root)]
    with pytest.raises(LibraryLayoutError, match="manifest.json"):
        unpack(archive_of(empty, tmp_path / "bad.tar.gz"), root)
    assert [v.name for v in versions(root)] == before


def test_an_archive_that_escapes_its_directory_is_refused(tmp_path, root):
    nasty = tmp_path / "nasty.tar.gz"
    victim = tmp_path / "secret.txt"
    victim.write_text("secret")
    with tarfile.open(nasty, "w:gz") as tar:
        tar.add(victim, arcname="../escaped.txt")
    with pytest.raises((LibraryLayoutError, tarfile.TarError, OSError)):
        unpack(nasty, root)
    assert not (tmp_path / "escaped.txt").exists()


def test_activating_something_that_is_not_a_bundle_is_refused(root):
    (root / "versions" / "junk").mkdir()
    with pytest.raises(LibraryLayoutError, match="not a library bundle"):
        activate(root, "junk")


def test_pruning_keeps_the_newest_and_never_the_active_one(root, tmp_path, bundle):
    original = versions(root)[0].name
    tarball = archive_of(bundle, tmp_path / "n.tar.gz")
    for name in ("v2", "v3", "v4"):
        unpack(tarball, root, name=name)

    # newest first: v4, v3, v2, then the original, which is still active
    assert prune(root, keep=1) == ["v3", "v2"]
    assert {v.name for v in versions(root)} == {"v4", original}

    activate(root, "v4")
    assert prune(root, keep=1) == [original]
    assert [v.name for v in versions(root)] == ["v4"]
