import shutil

import numpy as np
import pytest
from conftest import TOY_LIBRARY
from fakes import FakeEncoder

from cra.core.library.library import Library
from cra.core.retrieval.dense import DenseIndex
from cra.core.retrieval.encoder import (
    LFS_POINTER,
    MODEL_DIR,
    VOCAB_FILE,
    WEIGHTS_FILE,
    BertEncoder,
    Encoder,
    EncoderError,
    shipped,
)


def test_the_fake_encoder_satisfies_the_protocol():
    encoder = FakeEncoder()
    assert isinstance(encoder, Encoder)
    vector = encoder.encode("perovskite solar cells")
    assert vector.shape == (encoder.dimension,)
    assert float(np.linalg.norm(vector)) == pytest.approx(1.0)


def test_the_fake_encoder_is_deterministic_and_text_dependent():
    encoder = FakeEncoder()
    assert encoder.encode("copper") @ encoder.encode("copper") == pytest.approx(1.0)
    assert encoder.encode("copper") @ encoder.encode("perovskite") < 1.0
    assert not encoder.encode("").any()


@pytest.mark.parametrize(
    ("weights", "message"),
    [(None, "missing"), (LFS_POINTER + b" spec/v1\noid sha256:0\n", "git lfs pull")],
    ids=["no weights", "an LFS pointer"],
)
def test_weights_that_are_not_there_say_why(tmp_path, weights, message):
    shutil.copy(MODEL_DIR / VOCAB_FILE, tmp_path / VOCAB_FILE)
    if weights is not None:
        (tmp_path / WEIGHTS_FILE).write_bytes(weights)
    with pytest.raises(EncoderError, match=message):
        BertEncoder.load(tmp_path)


@pytest.fixture(scope="module")
def encoder():
    try:
        return shipped()
    except EncoderError as exc:
        pytest.skip(str(exc))


def test_the_shipped_encoder_reproduces_the_librarys_vectors(encoder):
    """The toy library's vectors came from sentence-transformers on torch, so
    agreement here checks the tokenizer, the forward pass and the weights at
    once. Built from the title and abstract, as developer/make_toy_library.py
    does."""
    library = Library.load(TOY_LIBRARY)
    assert encoder.name == library.embeddings.model
    for doi, row in library.embeddings.index.items():
        paper = library.papers[doi]
        text = f"{paper.title.strip()}. {paper.abstract.strip()}".strip(". ")
        stored = library.embeddings.vectors[row]
        assert float(encoder.encode(text) @ stored) > 0.9999, doi


def test_the_shipped_encoder_ranks_the_paper_a_query_describes(encoder):
    library = Library.load(TOY_LIBRARY)
    vector = encoder.encode("long-range electrostatics in machine learning potentials")
    assert float(np.linalg.norm(vector)) == pytest.approx(1.0, abs=1e-5)

    hits = DenseIndex(library.embeddings).search(vector, limit=3)
    titles = [library.papers[h.doi].title.lower() for h in hits]
    assert any("electrostatic" in t for t in titles), titles
    assert hits[0].score > 0.5
