"""A library directory that can be replaced while the service runs.

`CRA_LIBRARY_PATH` may point at either shape:

- a **bundle**, holding `manifest.json` directly. Simple deployments stay
  simple, and nothing can write to it.
- a **root**, holding `versions/<name>/` and a `current` link. An admin may
  then upload a new bundle: it is unpacked beside the live one, verified in
  full, and only then does `current` move, in one atomic step. The previous
  versions stay, so rolling back is moving the link again.
"""

import logging
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cra.core.library.manifest import MANIFEST

log = logging.getLogger(__name__)

VERSIONS = "versions"
CURRENT = "current"
KEEP_VERSIONS = 3


class LibraryLayoutError(Exception):
    pass


@dataclass(frozen=True)
class Version:
    name: str
    path: Path
    active: bool
    bytes: int


def is_bundle(path: Path) -> bool:
    return (path / MANIFEST).exists()


def is_root(path: Path) -> bool:
    return (path / CURRENT).exists() or (path / VERSIONS).is_dir()


def resolve(path: Path) -> Path:
    """The directory to load from, whichever shape ``path`` has."""
    path = Path(path)
    if is_bundle(path):
        return path
    current = path / CURRENT
    if current.exists():
        return current.resolve()
    if is_root(path):
        raise LibraryLayoutError(
            f"{path} has no active version; run `cra library activate`"
        )
    # a plain directory without a manifest: let the loader report what is missing
    return path


def writable(path: Path) -> bool:
    """Whether a new version may be installed here."""
    return is_root(Path(path)) and os.access(path, os.W_OK)


def versions(path: Path) -> list[Version]:
    root = Path(path)
    store = root / VERSIONS
    if not store.is_dir():
        return []
    active = (root / CURRENT).resolve() if (root / CURRENT).exists() else None
    found = []
    for entry in sorted(store.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
        found.append(
            Version(
                entry.name,
                entry,
                active is not None and entry.resolve() == active,
                size,
            )
        )
    return found


def new_version_name() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _free_name(store: Path) -> str:
    base = new_version_name()
    name, suffix = base, 1
    while (store / name).exists():
        name = f"{base}-{suffix}"
        suffix += 1
    return name


def unpack(archive: Path, root: Path, name: str | None = None) -> Path:
    """Extract a bundle tarball into a new version directory and return it.

    The archive may hold the files at the top level or inside a single
    directory; both are common ways to pack one.
    """
    root = Path(root)
    store = root / VERSIONS
    store.mkdir(parents=True, exist_ok=True)
    if name is None:
        name = _free_name(store)
    target = store / name
    if target.exists():
        raise LibraryLayoutError(f"version {name} already exists")

    staging = Path(tempfile.mkdtemp(dir=store, prefix=".unpack-"))
    try:
        with tarfile.open(archive) as tar:
            # filter="data" refuses absolute paths, parent traversal, links and
            # device files, which is what makes an uploaded archive safe
            tar.extractall(staging, filter="data")
        source = _bundle_root(staging)
        source.rename(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def _bundle_root(staging: Path) -> Path:
    if is_bundle(staging):
        return staging
    entries = [p for p in staging.iterdir() if not p.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir() and is_bundle(entries[0]):
        return entries[0]
    raise LibraryLayoutError(f"the archive holds no {MANIFEST}")


def activate(root: Path, version: str) -> Path:
    """Point ``current`` at a version, atomically."""
    root = Path(root)
    target = root / VERSIONS / version
    if not is_bundle(target):
        raise LibraryLayoutError(f"{version} is not a library bundle")
    link = root / CURRENT
    # replacing a symlink is atomic, so a reader never sees a missing link
    temporary = root / f".{CURRENT}-{os.getpid()}"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(Path(VERSIONS) / version, target_is_directory=True)
    temporary.replace(link)
    log.info("library version activated", extra={"fields": {"version": version}})
    return link.resolve()


def prune(root: Path, keep: int = KEEP_VERSIONS) -> list[str]:
    """Drop the oldest inactive versions, keeping the newest ``keep``."""
    removed = []
    for version in versions(root)[keep:]:
        if version.active:
            continue
        shutil.rmtree(version.path, ignore_errors=True)
        removed.append(version.name)
    if removed:
        log.info("library versions pruned", extra={"fields": {"removed": removed}})
    return removed


def init_root(root: Path, bundle: Path) -> Path:
    """Turn a plain bundle directory into a root with one version in it."""
    root, bundle = Path(root), Path(bundle)
    if not is_bundle(bundle):
        raise LibraryLayoutError(f"{bundle} holds no {MANIFEST}")
    store = root / VERSIONS
    store.mkdir(parents=True, exist_ok=True)
    name = new_version_name()
    shutil.copytree(bundle, store / name)
    return activate(root, name)
