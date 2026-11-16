"""Measure keyword search against queries whose answer is known.

Each query in ``search_queries.json`` names one paper of the e-conversion
library the way a person might type it: another spelling of a formula, a Greek
letter written out, a plural, a fragment of the title. The script reports how
often that paper comes first and how often it is among the five results the
tool returns by default, and lists the misses, so a change to the tokenizer or
the ranking can be judged by numbers rather than by a few tried queries.

    python developer/search_eval.py <library directory> [queries.json]

Queries whose paper is not in the library are skipped and counted.
"""

import json
import sys
from pathlib import Path

from cra.core.library.library import Library
from cra.core.retrieval.lexical import LexicalIndex

DEPTH = 10  # ranks below this count as a miss
SHOWN = 5  # what search_papers returns by default


def evaluate(index: LexicalIndex, queries: list[dict]) -> dict:
    ranks = []
    for q in queries:
        found = [h.doi for h in index.search(q["query"], DEPTH)]
        ranks.append(found.index(q["doi"]) + 1 if q["doi"] in found else None)
    n = len(ranks)
    return {
        "first": sum(r == 1 for r in ranks) / n,
        "shown": sum(r is not None and r <= SHOWN for r in ranks) / n,
        "mrr": sum(1 / r for r in ranks if r) / n,
        "ranks": ranks,
    }


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write(__doc__)
        return 2
    library = Library.load(Path(sys.argv[1]), verify=False)
    path = (
        Path(sys.argv[2])
        if len(sys.argv) > 2
        else Path(__file__).with_name("search_queries.json")
    )
    queries = json.loads(path.read_text(encoding="utf-8"))
    present = [q for q in queries if q["doi"] in library.papers]
    if not present:
        sys.stderr.write("none of the queries' papers is in this library\n")
        return 1
    result = evaluate(LexicalIndex.build(library.papers), present)
    print(
        f"{len(present)} queries ({len(queries) - len(present)} skipped): "
        f"first {result['first']:.0%}, in top {SHOWN} {result['shown']:.0%}, "
        f"MRR {result['mrr']:.2f}"
    )
    for q, rank in zip(present, result["ranks"], strict=True):
        if rank is None or rank > SHOWN:
            where = f"rank {rank}" if rank else f"not in top {DEPTH}"
            print(f"  {where:>14}  [{q['kind']}] {q['query']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
