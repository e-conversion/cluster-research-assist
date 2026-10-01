"""Build the query encoder in src/cra/core/retrieval/bge-small-en-v1.5/.

Downloads BAAI/bge-small-en-v1.5 at a pinned revision and writes
``weights.npz``: every weight in float16, plus the model's name, revision and
attention head count. It refuses a model whose weights float16 would change,
and one whose configuration differs from what ``cra.core.retrieval.encoder``
computes (post-norm BERT, exact GELU, CLS pooling). ``vocab.txt`` is copied
as it is. LICENSE (MIT, from FlagEmbedding) is maintained by hand.

The output is committed through Git LFS. Run this only to change the revision:

    python developer/make_encoder.py
"""

import json
import struct
import sys
from pathlib import Path

import httpx
import numpy as np

MODEL = "BAAI/bge-small-en-v1.5"
# the revision the cluster library's vectors were computed with
REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
OUT = Path(__file__).resolve().parents[1] / "src/cra/core/retrieval/bge-small-en-v1.5"
EXPECTED_CONFIG = {
    "model_type": "bert",
    "hidden_act": "gelu",
    "layer_norm_eps": 1e-12,
    "position_embedding_type": "absolute",
    "max_position_embeddings": 512,
}


def fetch(name: str) -> bytes:
    url = f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}"
    response = httpx.get(url, follow_redirects=True, timeout=300)
    response.raise_for_status()
    return response.content


def read_safetensors(raw: bytes) -> dict[str, np.ndarray]:
    """Eight bytes of header length, a JSON header, then the tensors back to back."""
    (size,) = struct.unpack("<Q", raw[:8])
    header = json.loads(raw[8 : 8 + size])
    header.pop("__metadata__", None)
    data = raw[8 + size :]
    tensors = {}
    for name, entry in header.items():
        if name.endswith("position_ids"):
            continue  # an index buffer, not a weight
        if entry["dtype"] != "F32":
            sys.exit(f"{name}: expected F32, found {entry['dtype']}")
        start, end = entry["data_offsets"]
        tensors[name] = np.frombuffer(data[start:end], dtype="<f4").reshape(
            entry["shape"]
        )
    return tensors


def main() -> None:
    config = json.loads(fetch("config.json"))
    wrong = {k: config.get(k) for k, v in EXPECTED_CONFIG.items() if config.get(k) != v}
    pooling = json.loads(fetch("1_Pooling/config.json"))
    if wrong or not pooling.get("pooling_mode_cls_token"):
        sys.exit(f"the encoder does not compute this model: {wrong or pooling}")

    weights = read_safetensors(fetch("model.safetensors"))
    half = {name: w.astype(np.float16) for name, w in weights.items()}
    changed = [n for n, w in weights.items() if not np.array_equal(half[n], w)]
    if changed:
        sys.exit(f"float16 would change {len(changed)} tensors, {changed[0]} first")

    OUT.mkdir(exist_ok=True)
    np.savez(
        OUT / "weights.npz",
        name=np.array(MODEL),
        revision=np.array(REVISION),
        heads=np.array(config["num_attention_heads"]),
        **half,
    )
    (OUT / "vocab.txt").write_bytes(fetch("vocab.txt"))
    size = (OUT / "weights.npz").stat().st_size / 1e6
    print(f"{MODEL}@{REVISION[:7]}: {len(half)} tensors, {size:.1f} MB -> {OUT}")


if __name__ == "__main__":
    main()
