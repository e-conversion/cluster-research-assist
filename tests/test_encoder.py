import os
from pathlib import Path

import numpy as np
import pytest
from conftest import TOY_LIBRARY
from fakes import FakeEncoder

from cra.core.library.library import Library
from cra.core.retrieval.dense import DenseIndex
from cra.core.retrieval.encoder import Encoder, EncoderError, OnnxEncoder

ENCODER_PATH = os.environ.get("CRA_TEST_ENCODER_PATH", "")


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


@pytest.mark.parametrize("missing", ["model.onnx", "tokenizer.json"])
def test_an_incomplete_encoder_directory_says_what_to_run(tmp_path, missing):
    for name in ("model.onnx", "tokenizer.json"):
        if name != missing:
            (tmp_path / name).write_bytes(b"x")
    with pytest.raises(EncoderError, match="cra encoder fetch"):
        OnnxEncoder.load(tmp_path)


@pytest.mark.slow
@pytest.mark.skipif(not ENCODER_PATH, reason="CRA_TEST_ENCODER_PATH not set")
def test_the_real_encoder_ranks_the_paper_a_query_describes():
    """The one test that loads the model: `cra encoder fetch` then set
    CRA_TEST_ENCODER_PATH."""
    encoder = OnnxEncoder.load(Path(ENCODER_PATH))
    library = Library.load(TOY_LIBRARY)
    assert encoder.dimension == library.embeddings.vectors.shape[1]
    assert encoder.name == library.embeddings.model

    vector = encoder.encode("long-range electrostatics in machine learning potentials")
    assert float(np.linalg.norm(vector)) == pytest.approx(1.0, abs=1e-5)

    hits = DenseIndex(library.embeddings).search(vector, limit=3)
    titles = [library.papers[h.doi].title.lower() for h in hits]
    assert any("electrostatic" in t for t in titles), titles
    assert hits[0].score > 0.5
