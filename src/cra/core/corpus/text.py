"""Accent-folded word matching shared by the lexical tools."""

import re
import unicodedata

# Kept tiny on purpose: domain words such as "energy" or "group" must stay
# searchable, only English fillers that match every record are dropped.
STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "from", "this", "that", "are", "was", "were",
        "but", "not", "you", "all", "any", "can", "has", "have", "had", "their",
        "they", "them", "its", "into", "over", "such", "than", "then", "these",
        "those", "which", "who", "whom", "what", "when", "where", "why", "how",
    }
)  # fmt: skip

_WORD = re.compile(r"\w+")


def fold(text: str) -> str:
    """Lowercase without accents: 'Müller' -> 'muller'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def words(text: str) -> set[str]:
    return set(_WORD.findall(fold(text)))


def query_tokens(query: str, min_len: int = 3) -> list[str]:
    return [
        t
        for t in _WORD.findall(fold(query))
        if len(t) >= min_len and t not in STOPWORDS
    ]


def overlap(text: str, tokens: list[str]) -> int:
    """How many query tokens occur as whole words in ``text``."""
    present = words(text)
    return sum(1 for t in tokens if t in present)
