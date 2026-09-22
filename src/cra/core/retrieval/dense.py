"""Search over the precomputed embeddings.

The vectors are L2-normalised when the library is built, so a dot product is
the cosine similarity and no model is needed to rank. A model is needed only to
turn a free-text query into a vector; comparing one paper with the rest needs
none at all.
"""

import numpy as np

from cra.core.library.records import Embeddings, normalise_doi
from cra.core.retrieval.lexical import Hit


class DenseIndex:
    def __init__(self, embeddings: Embeddings) -> None:
        self._embeddings = embeddings

    def __len__(self) -> int:
        return len(self._embeddings)

    @property
    def model(self) -> str:
        return self._embeddings.model

    @property
    def dimension(self) -> int:
        return int(self._embeddings.vectors.shape[1])

    def search(self, vector: np.ndarray, limit: int = 5) -> list[Hit]:
        query = np.asarray(vector, dtype=np.float32).ravel()
        if query.shape[0] != self.dimension:
            raise ValueError(
                f"query has {query.shape[0]} dimensions, the library has {self.dimension}"
            )
        return self._rank(self._embeddings.vectors @ query, limit)

    def similar(self, doi: str, limit: int = 5) -> list[Hit] | None:
        """The papers closest to this one, or None when it has no vector."""
        index = self._embeddings.index.get(normalise_doi(doi))
        if index is None:
            return None
        scores = self._embeddings.vectors @ self._embeddings.vectors[index]
        scores[index] = -np.inf  # a paper is not similar to itself
        return self._rank(scores, limit)

    def _rank(self, scores: np.ndarray, limit: int) -> list[Hit]:
        limit = max(1, min(limit, len(self._embeddings)))
        top = np.argpartition(-scores, limit - 1)[:limit]
        top = top[np.argsort(-scores[top])]
        return [
            Hit(self._embeddings.dois[i], float(scores[i]), "semantic")
            for i in top
            if np.isfinite(scores[i])
        ]
