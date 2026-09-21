"""``manifest.json``: the contract between a corpus bundle and the code."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"
MANIFEST = "manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def schema_matches(version: str, required: str) -> bool:
    """``required`` is exact (``1.0``) or major-only (``1.x``)."""
    major, _, minor = required.partition(".")
    if minor == "x":
        return version.split(".")[0] == major
    return version == required


def write(
    directory: Path,
    counts: dict[str, int],
    *,
    embedding_model: str = "",
    builder: str = "cra corpus manifest",
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    files = sorted(p for p in directory.iterdir() if p.is_file() and p.name != MANIFEST)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "builder": builder,
        "embedding_model": embedding_model,
        "files": {
            p.name: {"sha256": sha256(p), "bytes": p.stat().st_size} for p in files
        },
        "counts": counts,
        "provenance": provenance or {},
    }
    (directory / MANIFEST).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def read(directory: Path) -> dict[str, Any] | None:
    path = directory / MANIFEST
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def check(directory: Path, required_schema: str = "1.x") -> list[str]:
    """Every problem found, empty when the bundle is intact."""
    manifest = read(directory)
    if manifest is None:
        return [f"{MANIFEST} missing"]
    problems = []
    version = str(manifest.get("schema_version", ""))
    if not schema_matches(version, required_schema):
        problems.append(
            f"schema version {version!r} does not satisfy {required_schema!r}"
        )
    for name, expected in manifest.get("files", {}).items():
        path = directory / name
        if not path.exists():
            problems.append(f"{name}: listed in manifest but missing")
        elif sha256(path) != expected.get("sha256"):
            problems.append(f"{name}: checksum mismatch")
    return problems


def check_counts(manifest: dict[str, Any], counts: dict[str, int]) -> list[str]:
    return [
        f"count {key}: manifest says {expected}, loaded {counts.get(key)}"
        for key, expected in manifest.get("counts", {}).items()
        if key in counts and counts[key] != expected
    ]
