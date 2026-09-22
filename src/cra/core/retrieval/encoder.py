"""Turning a query into a vector.

Only the query needs encoding while serving: the library ships the paper
vectors. That one forward pass runs on onnxruntime rather than torch, which
keeps a gigabyte of CUDA-capable tensor library out of an image that would
never use it. `cra encoder fetch` downloads the two files it needs.

The embedding model must match the one the library was built with; the caller
compares the names and refuses a mismatch, because two models' vectors are not
comparable even when the dimensions agree.
"""

import logging
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

log = logging.getLogger(__name__)

MODEL_FILE = "model.onnx"
TOKENIZER_FILE = "tokenizer.json"
NAME_FILE = "model.txt"
# bge and its relatives cap at 512; longer input is truncated
MAX_TOKENS = 512


@runtime_checkable
class Encoder(Protocol):
    name: str
    dimension: int

    def encode(self, text: str) -> np.ndarray:
        """One L2-normalised vector."""


class EncoderError(Exception):
    pass


class OnnxEncoder:
    def __init__(self, session: Any, tokenizer: Any, name: str) -> None:
        self._session = session
        self._tokenizer = tokenizer
        self.name = name
        self._inputs = {i.name for i in session.get_inputs()}
        self.dimension = int(session.get_outputs()[0].shape[-1])

    @classmethod
    def load(cls, path: Path) -> "OnnxEncoder":
        import onnxruntime
        from tokenizers import Tokenizer

        path = Path(path)
        model, tokenizer_file = path / MODEL_FILE, path / TOKENIZER_FILE
        for needed in (model, tokenizer_file):
            if not needed.exists():
                raise EncoderError(f"{needed} missing; run `cra encoder fetch`")
        tokenizer = Tokenizer.from_file(str(tokenizer_file))
        tokenizer.enable_truncation(MAX_TOKENS)
        name_file = path / NAME_FILE
        name = (
            name_file.read_text(encoding="utf-8").strip() if name_file.exists() else ""
        )
        session = onnxruntime.InferenceSession(
            str(model), providers=["CPUExecutionProvider"]
        )
        log.info("encoder loaded", extra={"fields": {"model": name, "path": str(path)}})
        return cls(session, tokenizer, name)

    def encode(self, text: str) -> np.ndarray:
        encoded = self._tokenizer.encode(text)
        ids = np.array([encoded.ids], dtype=np.int64)
        feed = {
            "input_ids": ids,
            "attention_mask": np.array([encoded.attention_mask], dtype=np.int64),
        }
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.array([encoded.type_ids], dtype=np.int64)
        hidden = self._session.run(
            None, {k: v for k, v in feed.items() if k in self._inputs}
        )[0]
        # bge pools the first token, the one standing for the whole sequence
        vector = np.asarray(hidden[0][0], dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm else vector


def fetch(model: str, path: Path, onnx_file: str = "onnx/model.onnx") -> Path:
    """Download the model and its tokenizer into ``path``."""
    from huggingface_hub import hf_hub_download

    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    for remote, local in ((onnx_file, MODEL_FILE), (TOKENIZER_FILE, TOKENIZER_FILE)):
        downloaded = hf_hub_download(repo_id=model, filename=remote)
        (path / local).write_bytes(Path(downloaded).read_bytes())
    # remembered so a library built with another model can be refused
    (path / NAME_FILE).write_text(model + "\n", encoding="utf-8")
    return path
