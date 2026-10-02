"""The admin console's log view: the JSON-lines log read back, newest first.

The file rotates (``app.log``, ``app.log.1`` …), so a page boundary is a
timestamp plus how many entries with exactly that timestamp the reader has
already seen, not a byte offset that a rotation would shift.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cra.logsetup import BACKUP_COUNT, LOG_FILE

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
BLOCK = 64 * 1024
MAX_LIMIT = 500


def log_files(log_dir: Path) -> list[Path]:
    """Newest first."""
    names = [LOG_FILE] + [f"{LOG_FILE}.{n}" for n in range(1, BACKUP_COUNT + 1)]
    return [log_dir / name for name in names if (log_dir / name).is_file()]


def _lines_backwards(path: Path) -> Iterator[str]:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        tail = b""
        while position > 0:
            step = min(BLOCK, position)
            position -= step
            handle.seek(position)
            chunk = handle.read(step) + tail
            lines = chunk.split(b"\n")
            # the first piece may be the end of a line that starts earlier
            tail = lines.pop(0)
            for line in reversed(lines):
                if line.strip():
                    yield line.decode("utf-8", "replace")
        if tail.strip():
            yield tail.decode("utf-8", "replace")


def entries(log_dir: Path) -> Iterator[dict[str, Any]]:
    """Every parseable entry, newest first."""
    for path in log_files(log_dir):
        for line in _lines_backwards(path):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and "ts" in entry:
                yield entry


@dataclass(frozen=True)
class Query:
    levels: frozenset[str] = frozenset(LEVELS)
    text: str = ""
    # older than this timestamp, skipping the first ``skip`` entries that
    # carry exactly it (the previous page already showed them)
    before: str | None = None
    skip: int = 0
    # newer than this timestamp, for following the log live
    after: str | None = None
    limit: int = 50

    def matches(self, entry: dict[str, Any]) -> bool:
        if str(entry.get("level", "")) not in self.levels:
            return False
        if not self.text:
            return True
        haystack = json.dumps(entry, ensure_ascii=False).lower()
        return self.text.lower() in haystack


def read(log_dir: Path, query: Query) -> dict[str, Any]:
    """One page, newest first, with the cursor for the page after it."""
    limit = max(1, min(query.limit, MAX_LIMIT))
    found: list[dict[str, Any]] = []
    skipped = 0
    more = False
    for entry in entries(log_dir):
        ts = str(entry["ts"])
        if query.after is not None and ts <= query.after:
            break
        if query.before is not None and ts > query.before:
            continue
        if not query.matches(entry):
            continue
        if ts == query.before and skipped < query.skip:
            skipped += 1
            continue
        if len(found) == limit:
            more = True
            break
        found.append(entry)
    cursor = None
    if more and found:
        last = str(found[-1]["ts"])
        same = sum(1 for e in found if str(e["ts"]) == last)
        cursor = {
            "before": last,
            "skip": same + (skipped if last == query.before else 0),
        }
    return {"entries": found, "next": cursor}
