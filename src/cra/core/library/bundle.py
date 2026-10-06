"""Writing a library as a 2.0 bundle, and converting a 1.x bundle into one.

A 2.0 bundle holds the cleaned records themselves: one paper per entry in
``papers.json``, with the scraped list and its enrichment already merged, and
PIs and graph with the scraper quirks repaired. The loader reads it as it is.
"""

import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

from networkx.readwrite import json_graph

from cra.core.library import manifest
from cra.core.library.library import FILES, Library, LibraryError
from cra.core.library.versions import resolve

# written as received: nothing in them needs cleaning
COPIED = ("embeddings", "map", "proposal", "proposal_summary")


def _dump(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False) + "\n", encoding="utf-8")


def write(library: Library, target: Path) -> None:
    """The library's records as 2.0 files in ``target``, without a manifest."""
    target.mkdir(parents=True, exist_ok=True)
    _dump(
        target / FILES["papers"],
        [asdict(paper) for _, paper in sorted(library.papers.items())],
    )
    if library.pis:
        _dump(target / FILES["pis"], [asdict(pi) for pi in library.pis])
    if library.fulltexts:
        _dump(
            target / FILES["fulltexts"],
            {
                doi: {
                    "fulltext": text.text,
                    "source": text.source,
                    "source_origin": text.source_origin,
                    "url": text.url,
                    "fetched_at": text.fetched_at,
                }
                for doi, text in sorted(library.fulltexts.items())
            },
        )
    if library.graph is not None:
        _dump(
            target / FILES["graph"],
            json_graph.node_link_data(library.graph, edges="links"),
        )
    for key in COPIED:
        source = library.path / FILES[key]
        if source.exists():
            shutil.copy2(source, target / FILES[key])


SHOWN = 5


def _changed(kind: str, old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Which keys a conversion lost, added or altered, a few of each by name."""
    found = []
    for what, keys in (
        ("missing", sorted(old.keys() - new.keys())),
        ("added", sorted(new.keys() - old.keys())),
        ("changed", sorted(k for k in old.keys() & new.keys() if old[k] != new[k])),
    ):
        if keys:
            more = f" and {len(keys) - SHOWN} more" if len(keys) > SHOWN else ""
            found.append(f"{kind} {what}: {', '.join(keys[:SHOWN])}{more}")
    return found


def differences(old: Library, new: Library) -> list[str]:
    """What a conversion changed, empty when the two hold the same data."""
    found = _changed("papers", old.papers, new.papers)
    found += _changed(
        "PIs", {pi.smid: pi for pi in old.pis}, {pi.smid: pi for pi in new.pis}
    )
    found += _changed("full texts", old.fulltexts, new.fulltexts)
    if (old.graph is None) != (new.graph is None) or (
        old.graph is not None
        and new.graph is not None
        and (
            dict(old.graph.nodes(data=True)) != dict(new.graph.nodes(data=True))
            or {frozenset(e[:2]): e[2] for e in old.graph.edges(data=True)}
            != {frozenset(e[:2]): e[2] for e in new.graph.edges(data=True)}
        )
    ):
        found.append("graph differs")
    if old.counts != new.counts:
        found.append(f"counts differ: {old.counts} != {new.counts}")
    return found


def migrate(source: Path, target: Path, *, builder: str) -> Library:
    """Convert a 1.x bundle into a 2.0 bundle in a new directory.

    The source is verified first, and the result is loaded back and compared
    with it, so a conversion that loses or changes anything fails here rather
    than on the server.
    """
    source, target = resolve(Path(source)), Path(target)
    if target.resolve() == source.resolve():
        raise LibraryError("migrate writes a new directory; give another target")
    if target.exists() and any(target.iterdir()):
        raise LibraryError(f"{target} is not empty")
    old = Library.load(source, required_schema="1.x")
    try:
        return _convert(old, target, builder)
    except BaseException:
        # the target was empty or absent, so nothing of anyone else's is lost
        shutil.rmtree(target, ignore_errors=True)
        raise


def _convert(old: Library, target: Path, builder: str) -> Library:
    write(old, target)
    new = Library.load(target, verify=False)
    if problems := differences(old, new):
        raise LibraryError("conversion changed the data: " + "; ".join(problems))
    manifest.write(
        target,
        new.counts,
        schema_version=new.schema_version,
        embedding_model=new.embeddings.model if new.embeddings else "",
        builder=builder,
    )
    return Library.load(target, required_schema="2.x")
