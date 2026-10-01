"""The expected tokens are what Hugging Face ``tokenizers`` produced for the
same text with the model's tokenizer.json."""

import pytest

from cra.core.retrieval.encoder import MODEL_DIR, VOCAB_FILE
from cra.core.retrieval.wordpiece import WordPiece

VOCABULARY = (MODEL_DIR / VOCAB_FILE).read_text(encoding="utf-8").split("\n")


@pytest.fixture(scope="module")
def tokenizer():
    return WordPiece.load(MODEL_DIR / VOCAB_FILE)


def tokens(tokenizer, text, max_tokens=512):
    return [VOCABULARY[i] for i in tokenizer.encode(text, max_tokens)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello, World!", ["hello", ",", "world", "!"]),
        ("$100+50%<x>", ["$", "100", "+", "50", "%", "<", "x", ">"]),
        ("electrocatalysis", ["electro", "##cat", "##aly", "##sis"]),
        ("Ångström naïve café", ["ang", "##strom", "naive", "cafe"]),
        ("İstanbul", ["istanbul"]),
        ("ΣΑΣ ΔΣ", ["σ", "##α", "##σ", "δ", "##σ"]),
        ("中文字符", ["中", "文", "[UNK]", "[UNK]"]),
        (
            "tab\tnew\nline\u00a0nbsp\u2028end",
            ["tab", "new", "line", "n", "##bs", "##p", "end"],
        ),
        ("zero\u200bwidth\x07bell", ["zero", "##wi", "##dt", "##h", "##bell"]),
        ("a\u0378b", ["[UNK]"]),
        ("x" * 101, ["[UNK]"]),
        ("", []),
    ],
    ids=[
        "punctuation",
        "ascii symbols",
        "word pieces",
        "accents",
        "dotted capital",
        "sigma lowercased per character",
        "cjk",
        "whitespace",
        "control and format characters",
        "unassigned code point",
        "overlong word",
        "empty",
    ],
)
def test_tokens_match_the_reference_tokenizer(tokenizer, text, expected):
    assert tokens(tokenizer, text) == ["[CLS]", *expected, "[SEP]"]


def test_long_input_is_cut_to_the_limit_with_its_end_marker(tokenizer):
    cut = tokens(tokenizer, "word " * 600, max_tokens=8)
    assert cut == ["[CLS]", *["word"] * 6, "[SEP]"]
