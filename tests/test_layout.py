"""Repository layout rules from the design document: unique basenames, no
catch-all module names, and the import layering that keeps the corpus tools
extractable."""

import ast
from collections import defaultdict
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "cra"
BANNED = {"utils", "helpers", "common", "base", "models"}
SHARED = {"__init__", "conftest", "_version"}

# layer -> layers it may never import; the directory tree encodes the direction
CONTRACTS = {
    "cra.core": ("cra.assistant", "cra.app"),
    "cra.assistant": ("cra.app",),
}


def _modules(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.stem not in SHARED]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_no_two_python_files_share_a_basename():
    seen = defaultdict(list)
    for root in (REPO / "src", REPO / "tests"):
        for path in _modules(root):
            seen[path.stem].append(path.relative_to(REPO))
    duplicates = {k: v for k, v in seen.items() if len(v) > 1}
    assert not duplicates, duplicates


def test_no_catch_all_module_names():
    offenders = [
        p.relative_to(REPO)
        for root in (REPO / "src", REPO / "tests")
        for p in _modules(root)
        if p.stem in BANNED
    ]
    assert not offenders, offenders


@pytest.mark.parametrize(("package", "forbidden"), CONTRACTS.items())
def test_import_layering(package, forbidden):
    root = SRC / package.removeprefix("cra.")
    assert root.is_dir(), root
    violations = {
        str(path.relative_to(REPO)): sorted(bad)
        for path in root.rglob("*.py")
        if (bad := {m for m in _imports(path) if m.startswith(forbidden)})
    }
    assert not violations, violations
