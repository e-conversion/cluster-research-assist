"""BERT's uncased WordPiece tokenizer, as Hugging Face ``tokenizers`` runs it.

Normalise (drop control characters, unify whitespace, space out CJK
ideographs, strip accents, lowercase), split on whitespace and punctuation,
then cut each word into the longest vocabulary pieces from the left. The
details follow the Rust implementation rather than Google's original Python,
because the library's vectors were made with it and a different token is a
different vector. The two agree on every text in the cluster library; they
differ only on characters newer than the Rust crate's Unicode tables.
"""

import unicodedata
from pathlib import Path

# Rust's is_other() without Cn: an unassigned code point is kept, not dropped
CONTROL = frozenset({"Cc", "Cf", "Co", "Cs"})
SPACE = frozenset({"Zs", "Zl", "Zp"})
# the Rust table; Google's starts the sixth range at 0x2B820
CJK = (
    (0x4E00, 0x9FFF),
    (0x3400, 0x4DBF),
    (0x20000, 0x2A6DF),
    (0x2A700, 0x2B73F),
    (0x2B740, 0x2B81F),
    (0x2B920, 0x2CEAF),
    (0xF900, 0xFAFF),
    (0x2F800, 0x2FA1F),
)
MAX_WORD_CHARS = 100
CONTINUATION = "##"


def _is_space(c: str) -> bool:
    return c in "\t\n\r " or unicodedata.category(c) in SPACE


def _is_punctuation(c: str) -> bool:
    # every printable ASCII symbol counts, $ + < = > ^ ` | ~ included
    return ("!" <= c <= "~" and not c.isalnum()) or unicodedata.category(c).startswith(
        "P"
    )


def _is_cjk(c: str) -> bool:
    code = ord(c)
    return any(low <= code <= high for low, high in CJK)


def normalise(text: str) -> str:
    kept = []
    for c in text:
        if c in "\0\ufffd" or (
            c not in "\t\n\r" and unicodedata.category(c) in CONTROL
        ):
            continue
        if _is_space(c):
            kept.append(" ")
        elif _is_cjk(c):
            kept.append(f" {c} ")
        else:
            kept.append(c)
    decomposed = unicodedata.normalize("NFD", "".join(kept))
    # one character at a time, as Rust does: str.lower() would turn a
    # word-final capital sigma into the final form, which is another token
    return "".join(c.lower() for c in decomposed if unicodedata.category(c) != "Mn")


def words(text: str) -> list[str]:
    """Whitespace separates words; every punctuation mark is a word of its own.
    After ``normalise`` the only whitespace left is the plain space."""
    found: list[str] = []
    current: list[str] = []
    for c in text:
        if c == " " or _is_punctuation(c):
            if current:
                found.append("".join(current))
                current = []
            if c != " ":
                found.append(c)
        else:
            current.append(c)
    if current:
        found.append("".join(current))
    return found


class WordPiece:
    def __init__(self, vocabulary: list[str]) -> None:
        self._ids = {token: i for i, token in enumerate(vocabulary)}
        self._unknown = self._ids["[UNK]"]
        self._start = self._ids["[CLS]"]
        self._end = self._ids["[SEP]"]

    @classmethod
    def load(cls, path: Path) -> "WordPiece":
        """One token per line, the line number its id. Split on newlines only:
        ``splitlines`` would also break at characters a token may contain."""
        text = Path(path).read_text(encoding="utf-8")
        return cls(text.removesuffix("\n").split("\n"))

    def encode(self, text: str, max_tokens: int) -> list[int]:
        """Token ids framed by [CLS] and [SEP], cut to ``max_tokens`` in all."""
        ids = [i for word in words(normalise(text)) for i in self._pieces(word)]
        return [self._start, *ids[: max_tokens - 2], self._end]

    def _pieces(self, word: str) -> list[int]:
        if len(word) > MAX_WORD_CHARS:
            return [self._unknown]
        pieces: list[int] = []
        start = 0
        while start < len(word):
            for end in range(len(word), start, -1):
                piece = (
                    word[start:end] if start == 0 else CONTINUATION + word[start:end]
                )
                if piece in self._ids:
                    pieces.append(self._ids[piece])
                    start = end
                    break
            else:
                # one unknown stretch makes the whole word unknown
                return [self._unknown]
        return pieces
