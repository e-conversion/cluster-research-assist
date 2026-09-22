"""Keyword search over titles and abstracts.

Two BM25 indices rather than one over the concatenation: a title match and an
abstract match mean different things, and the caller is told which it got. The
index with the stronger best hit wins the query outright. Blending the two with
reciprocal rank fusion is a known improvement, deferred until there is a
regression baseline to measure it against.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from rank_bm25 import BM25Okapi

from cra.core.library.records import Paper

Field = Literal["title", "abstract"]


@dataclass(frozen=True)
class Hit:
    doi: str
    score: float
    matched_on: str


def tokenize(text: str) -> list[str]:
    return text.lower().split()


class LexicalIndex:
    def __init__(
        self, dois: tuple[str, ...], titles: BM25Okapi, abstracts: BM25Okapi
    ) -> None:
        self._dois = dois
        self._indices: dict[Field, BM25Okapi] = {"title": titles, "abstract": abstracts}

    @classmethod
    def build(cls, papers: Mapping[str, Paper]) -> "LexicalIndex":
        dois = tuple(papers)
        # BM25Okapi divides by the corpus average length, so an empty document
        # must still contribute one token
        corpus = {
            "title": [tokenize(papers[d].title) or [""] for d in dois],
            "abstract": [tokenize(papers[d].abstract) or [""] for d in dois],
        }
        return cls(dois, BM25Okapi(corpus["title"]), BM25Okapi(corpus["abstract"]))

    def __len__(self) -> int:
        return len(self._dois)

    def search(self, query: str, limit: int = 5) -> list[Hit]:
        tokens = tokenize(query)
        if not tokens or not self._dois:
            return []
        scored = {
            field: index.get_scores(tokens) for field, index in self._indices.items()
        }
        field: Field = (
            "abstract" if max(scored["abstract"]) > max(scored["title"]) else "title"
        )
        scores = scored[field]
        ranked = sorted(range(len(scores)), key=lambda i: -scores[i])[:limit]
        return [
            Hit(self._dois[i], float(scores[i]), field) for i in ranked if scores[i] > 0
        ]
