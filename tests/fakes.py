"""Stand-ins for the parts that would otherwise need a model or a network."""

import hashlib

import numpy as np


class FakeEncoder:
    """Deterministic vectors from the text itself.

    Not an embedding in any useful sense, but stable, fast and normalised, so
    everything above it can be tested without loading a model. Repeated words
    move the vector, which is enough for "this query is closer to that text".
    """

    def __init__(self, dimension: int = 8, name: str = "fake") -> None:
        self.dimension = dimension
        self.name = name

    def encode(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dimension, dtype=np.float32)
        for word in text.lower().split():
            digest = hashlib.sha256(word.encode()).digest()
            vector[digest[0] % self.dimension] += 1.0
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm else vector
