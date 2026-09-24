"""The one short string that names a paper, wherever a paper is named.

The map overlay, the tooltip, the search list and the DOI lookup all show the
same shape, so a paper looks the same everywhere a person meets it. Author
lists arrive as free-form strings from several sources, which is why the
surname rules below are heuristics rather than a parse.
"""

from collections.abc import Sequence

from cra.core.library.records import Paper

TITLE_CHARS = 42
JOURNAL_CHARS = 32
# Enough surnames to find a paper by a middle author without carrying the
# 500-author lists that consortium papers bring.
SEARCH_AUTHORS = 6

# Fragments that are not the family name, so "van der Waals" keeps its
# particles while "Jr" is dropped.
_SUFFIXES = frozenset({"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "phd", "md"})
_PARTICLES = frozenset(
    {
        "van", "von", "de", "der", "den", "del", "della", "di", "da", "dos",
        "du", "la", "le", "ter", "ten",
    }
)  # fmt: skip


def surname(name: str) -> str:
    """The family name from either written order.

    ``"Ada Lovelace"`` and ``"Lovelace, Ada"`` both give ``"Lovelace"``, and
    ``"Johannes van der Waals"`` keeps its particles.
    """
    name = name.strip()
    if not name:
        return ""
    if "," in name:
        return " ".join(name.split(",")[0].split())
    parts = [p for p in name.split() if p.strip(".").lower() not in _SUFFIXES]
    if not parts:
        return ""
    # Walk back over the particles that belong with the family name.
    start = len(parts) - 1
    while start > 0 and parts[start - 1].lower() in _PARTICLES:
        start -= 1
    return " ".join(parts[start:])


def surnames(authors: Sequence[str], limit: int = SEARCH_AUTHORS) -> str:
    """Surnames only, bounded: the searchable part of an author list."""
    return " ".join(s for a in authors[:limit] if (s := surname(a)))


def _clip(text: str, budget: int) -> str:
    """Cut at the last word boundary inside ``budget``."""
    text = " ".join(text.split())
    if len(text) <= budget:
        return text
    cut = text[: budget - 1].rstrip()
    trimmed = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return (trimmed or cut).rstrip(",;:.") + "…"


def _authors(authors: Sequence[str]) -> str:
    named = [s for a in authors if (s := surname(a))]
    if not named:
        return ""
    return named[0] if len(named) == 1 else f"{named[0]} et al."


def compose(
    authors: Sequence[str],
    title: str,
    journal: str = "",
    year: str = "",
    *,
    fallback: str = "",
    title_chars: int = TITLE_CHARS,
) -> str:
    """``Lovelace et al., Perovskite solar cells with…, Nat. Mater., 2024``.

    Every part is optional; whatever is missing is left out rather than
    leaving an empty slot behind, so a record with only a title still reads as
    a phrase instead of a row of commas.
    """
    parts = [
        p
        for p in (
            _authors(authors),
            _clip(title, title_chars),
            _clip(journal, JOURNAL_CHARS),
            str(year).strip(),
        )
        if p
    ]
    return ", ".join(parts) or fallback.strip()


def short(paper: Paper, *, title_chars: int = TITLE_CHARS) -> str:
    """``compose`` over a library record, falling back to the bare DOI."""
    return compose(
        paper.authors,
        paper.title,
        paper.journal,
        paper.year,
        fallback=paper.doi,
        title_chars=title_chars,
    )
