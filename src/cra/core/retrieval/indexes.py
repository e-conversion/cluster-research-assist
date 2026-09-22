"""Everything needed to answer a question about the library, built once."""

from dataclasses import dataclass

from cra.core.library.library import Library
from cra.core.retrieval.collaboration_graph import CollaborationGraph
from cra.core.retrieval.dense import DenseIndex
from cra.core.retrieval.encoder import Encoder
from cra.core.retrieval.lexical import LexicalIndex


@dataclass(frozen=True)
class Indexes:
    library: Library
    lexical: LexicalIndex
    dense: DenseIndex | None = None
    graph: CollaborationGraph | None = None
    encoder: Encoder | None = None

    @classmethod
    def build(cls, library: Library, encoder: Encoder | None = None) -> "Indexes":
        return cls(
            library=library,
            lexical=LexicalIndex.build(library.papers),
            dense=DenseIndex(library.embeddings) if library.embeddings else None,
            graph=CollaborationGraph(library.graph) if library.graph else None,
            encoder=encoder,
        )

    @property
    def semantic_ready(self) -> bool:
        """Whether a free-text query can be turned into a vector.

        The library's vectors and the encoder must come from the same model;
        otherwise the numbers are comparable only by accident.
        """
        return (
            self.dense is not None
            and self.encoder is not None
            and self.encoder.name == self.dense.model
        )
