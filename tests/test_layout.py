"""Repository layout rules from the design document: unique basenames, no
catch-all module names."""

from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BANNED = {"utils", "helpers", "common", "base", "models"}
SHARED = {"__init__", "conftest", "_version"}


def _modules(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.stem not in SHARED]


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
