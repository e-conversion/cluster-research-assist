"""Keyword search over titles and abstracts.

Two BM25 indices rather than one over the concatenation: a title match and an
abstract match mean different things, and the caller is told which it got. The
index with the stronger best hit wins the query outright. Blending the two with
reciprocal rank fusion is a known improvement, deferred until there is a
regression baseline to measure it against.

Documents and queries go through the same ``tokenize``, which is what lets the
spellings of this field meet: "Li-O2", "Li–O₂" and "LiO2", "α-Fe2O3" and
"alpha-Fe2O3", "catalysts" and "catalyst". Adjacent word pairs are indexed as
terms of their own, so a paper holding a query's phrase outranks one that only
holds its words.
"""

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

import numpy as np

from cra.core.library.records import Paper
from cra.core.library.text import fold

Field = Literal["title", "abstract"]

# Okapi's usual constants: term-frequency saturation and length normalisation
K1, B = 1.5, 0.75
# A term in more than half the documents has a negative idf. Okapi floors it at
# this share of the average idf, so a common word still counts a little.
IDF_FLOOR = 0.25


@dataclass(frozen=True)
class Hit:
    doi: str
    score: float
    matched_on: str


# α -> "alpha", by Unicode name, so a query typed without Greek finds it
_GREEK = {
    chr(c): unicodedata.name(chr(c)).split()[-1].lower().replace("lamda", "lambda")
    for c in range(0x3B1, 0x3CA)
}
# A word, or words joined by hyphens, dashes or slashes: "li-o2", "n/c".
_COMPOUND = re.compile(r"\w+(?:[-/‐-―]\w+)*")
_WORD = re.compile(r"\w+")


def stem(word: str) -> str:
    """Plural endings off, as Harman's S-stemmer does: "catalysts" ->
    "catalyst", "batteries" -> "battery". Conservative on purpose: a heavier
    stemmer conflates terms a chemist keeps apart, and formulas are left as
    they are."""
    if len(word) <= 3 or not word.isalpha():
        return word
    if word.endswith("ies") and not word.endswith(("eies", "aies")):
        return word[:-3] + "y"
    if word.endswith("es") and not word.endswith(("aes", "ees", "oes")):
        return word[:-1]
    if word.endswith("s") and not word.endswith(("us", "ss")):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    """Search terms: folded words, adjacent pairs, and joined compounds.

    Folding takes case, accents and sub- and superscripts off ("Li₂O" is
    "li2o"). A compound counts as its parts and as one word, so "Li-O2" meets
    both "Li–O₂" and "LiO2".
    """
    folded = "".join(_GREEK.get(ch, ch) for ch in fold(text))
    words: list[str] = []
    joined: list[str] = []
    for compound in _COMPOUND.findall(folded):
        parts = _WORD.findall(compound)
        words.extend(stem(p) for p in parts)
        if len(parts) > 1:
            joined.append("".join(parts))
    return words + [f"{a} {b}" for a, b in pairwise(words)] + joined


class BM25:
    """Okapi BM25 over an inverted index, so a query touches only the documents
    holding one of its terms. A term's score in a document does not depend on
    the query, so it is computed once, here. The scores are rank_bm25's
    BM25Okapi, which this replaces, up to the last bits of the idf floor:
    ``sum`` adds floats with compensation."""

    def __init__(self, documents: list[list[str]]) -> None:
        self._size = len(documents)
        self._postings: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        if not documents:
            return
        counts: dict[str, dict[int, int]] = {}
        for i, document in enumerate(documents):
            for term, count in Counter(document).items():
                counts.setdefault(term, {})[i] = count
        lengths = np.array([len(d) for d in documents], dtype=np.float64)
        average = sum(len(d) for d in documents) / self._size
        idf = {
            term: math.log(self._size - len(found) + 0.5) - math.log(len(found) + 0.5)
            for term, found in counts.items()
        }
        floor = IDF_FLOOR * (sum(idf.values()) / len(idf))
        for term, found in counts.items():
            ids = np.fromiter(found, dtype=np.intp, count=len(found))
            tf = np.fromiter(found.values(), dtype=np.float64, count=len(found))
            saturation = (
                tf * (K1 + 1) / (tf + K1 * (1 - B + B * lengths[ids] / average))
            )
            self._postings[term] = (
                ids,
                (idf[term] if idf[term] >= 0 else floor) * saturation,
            )

    def scores(self, query: list[str]) -> np.ndarray:
        """One score per document; a repeated query term counts again."""
        total = np.zeros(self._size)
        for term in query:
            if term in self._postings:
                ids, contribution = self._postings[term]
                total[ids] += contribution
        return total


class LexicalIndex:
    def __init__(self, dois: tuple[str, ...], titles: BM25, abstracts: BM25) -> None:
        self._dois = dois
        self._indices: dict[Field, BM25] = {"title": titles, "abstract": abstracts}

    @classmethod
    def build(cls, papers: Mapping[str, Paper]) -> "LexicalIndex":
        dois = tuple(papers)
        # BM25 divides by the average document length, so an empty document
        # must still contribute one token
        corpus = {
            "title": [tokenize(papers[d].title) or [""] for d in dois],
            "abstract": [tokenize(papers[d].abstract) or [""] for d in dois],
        }
        return cls(dois, BM25(corpus["title"]), BM25(corpus["abstract"]))

    def __len__(self) -> int:
        return len(self._dois)

    def search(self, query: str, limit: int = 5) -> list[Hit]:
        tokens = tokenize(query)
        if not tokens or not self._dois:
            return []
        scored = {field: index.scores(tokens) for field, index in self._indices.items()}
        field: Field = (
            "abstract" if max(scored["abstract"]) > max(scored["title"]) else "title"
        )
        scores = scored[field]
        ranked = sorted(range(len(scores)), key=lambda i: -scores[i])[:limit]
        return [
            Hit(self._dois[i], float(scores[i]), field) for i in ranked if scores[i] > 0
        ]
