"""Turning a query into a vector.

Only the query needs encoding while serving: the library ships the paper
vectors. The model is BAAI/bge-small-en-v1.5, a 12-layer BERT. One forward pass
over a query is a few dozen small matrix products, so it runs in numpy (about
ten milliseconds) rather than through an inference runtime and a tokenizer
library. Its weights ship with the package, stored in half precision: every
weight of this model is exactly representable there, so nothing is lost.

The embedding model must match the one the library was built with; the caller
compares the names and refuses a mismatch, because two models' vectors are not
comparable even when the dimensions agree.
"""

import functools
import logging
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

from cra.core.retrieval.wordpiece import WordPiece

log = logging.getLogger(__name__)

# built by developer/make_encoder.py; the weights are a Git LFS object
MODEL_DIR = Path(__file__).with_name("bge-small-en-v1.5")
WEIGHTS_FILE = "weights.npz"
VOCAB_FILE = "vocab.txt"
LFS_POINTER = b"version https://git-lfs"
# the position table has 512 rows; longer input is truncated
MAX_TOKENS = 512
LAYER_NORM_EPS = 1e-12


@runtime_checkable
class Encoder(Protocol):
    name: str
    dimension: int

    def encode(self, text: str) -> np.ndarray:
        """One L2-normalised vector."""


class EncoderError(Exception):
    pass


def _layer_norm(x: np.ndarray, weight: np.ndarray, bias: np.ndarray) -> np.ndarray:
    mean = x.mean(axis=-1, keepdims=True)
    variance = ((x - mean) ** 2).mean(axis=-1, keepdims=True)
    return (x - mean) / np.sqrt(variance + LAYER_NORM_EPS) * weight + bias


def _erf(x: np.ndarray) -> np.ndarray:
    """numpy has no erf. Abramowitz & Stegun 7.1.26 is within 1.5e-7 of it,
    which is float32's own resolution near one."""
    t = 1.0 / (1.0 + 0.3275911 * np.abs(x))
    poly = t * (
        0.254829592
        + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429)))
    )
    return np.sign(x) * (1.0 - poly * np.exp(-x * x))


def _gelu(x: np.ndarray) -> np.ndarray:
    # BERT's exact GELU, not the tanh approximation
    return 0.5 * x * (1.0 + _erf(x / math.sqrt(2.0)))


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


class BertEncoder:
    def __init__(
        self,
        weights: Mapping[str, np.ndarray],
        tokenizer: WordPiece,
        name: str,
        heads: int,
    ) -> None:
        self._w = weights
        self._tokenizer = tokenizer
        self._heads = heads
        self._layers = sum(
            1 for k in weights if k.endswith("attention.self.query.weight")
        )
        self.name = name
        self.dimension = int(weights["embeddings.word_embeddings.weight"].shape[1])

    @classmethod
    def load(cls, path: Path = MODEL_DIR) -> "BertEncoder":
        path = Path(path)
        weights_file = path / WEIGHTS_FILE
        if not weights_file.exists():
            raise EncoderError(f"{weights_file} missing")
        with weights_file.open("rb") as f:
            if f.read(len(LFS_POINTER)) == LFS_POINTER:
                raise EncoderError(
                    f"{weights_file} is a Git LFS pointer, not the weights: "
                    "install git-lfs and run `git lfs pull`"
                )
        with np.load(weights_file) as archive:
            name, heads = str(archive["name"]), int(archive["heads"])
            weights = {
                key: archive[key].astype(np.float32)
                for key in archive.files
                if key not in ("name", "revision", "heads")
            }
        encoder = cls(weights, WordPiece.load(path / VOCAB_FILE), name, heads)
        log.info("encoder loaded", extra={"fields": {"model": name, "path": str(path)}})
        return encoder

    def encode(self, text: str) -> np.ndarray:
        hidden = self._forward(np.array(self._tokenizer.encode(text, MAX_TOKENS)))
        # bge pools the first token, the one standing for the whole sequence
        vector = hidden[0]
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm else vector

    def _linear(self, name: str, x: np.ndarray) -> np.ndarray:
        return x @ self._w[f"{name}.weight"].T + self._w[f"{name}.bias"]

    def _norm(self, name: str, x: np.ndarray) -> np.ndarray:
        return _layer_norm(x, self._w[f"{name}.weight"], self._w[f"{name}.bias"])

    def _attention(self, prefix: str, x: np.ndarray) -> np.ndarray:
        # one sequence and no padding, so no attention mask
        n, width = x.shape

        def split(part: str) -> np.ndarray:  # (heads, tokens, width / heads)
            projected = self._linear(f"{prefix}.self.{part}", x)
            return projected.reshape(n, self._heads, -1).transpose(1, 0, 2)

        q, k, v = split("query"), split("key"), split("value")
        weights = _softmax(q @ k.transpose(0, 2, 1) / math.sqrt(q.shape[-1]))
        mixed = (weights @ v).transpose(1, 0, 2).reshape(n, width)
        return self._linear(f"{prefix}.output.dense", mixed)

    def _forward(self, ids: np.ndarray) -> np.ndarray:
        w = self._w
        x = (
            w["embeddings.word_embeddings.weight"][ids]
            + w["embeddings.position_embeddings.weight"][: len(ids)]
            + w["embeddings.token_type_embeddings.weight"][0]
        )
        x = self._norm("embeddings.LayerNorm", x)
        for i in range(self._layers):
            layer = f"encoder.layer.{i}"
            x = self._norm(
                f"{layer}.attention.output.LayerNorm",
                x + self._attention(f"{layer}.attention", x),
            )
            inner = _gelu(self._linear(f"{layer}.intermediate.dense", x))
            x = self._norm(
                f"{layer}.output.LayerNorm",
                x + self._linear(f"{layer}.output.dense", inner),
            )
        return x


@functools.cache
def shipped() -> BertEncoder:
    """The packaged encoder, loaded once per process: replacing the library
    builds new indexes but needs no second copy of the weights."""
    return BertEncoder.load()
