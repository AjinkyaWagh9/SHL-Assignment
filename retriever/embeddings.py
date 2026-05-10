"""Build and load the dense + sparse indices.

Dense: sentence-transformers `all-MiniLM-L6-v2` (384-dim), normalized for cosine.
Sparse: rank_bm25 BM25Okapi over name + description tokens.

We persist to disk under data/vector_index/ so cold start = load arrays, not re-encode.
"""

from __future__ import annotations

import json
import pickle
import re
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "data" / "processed" / "catalog.json"
INDEX_DIR = ROOT / "data" / "vector_index"
DENSE_PATH = INDEX_DIR / "dense.npy"
BM25_PATH = INDEX_DIR / "bm25.pkl"
IDS_PATH = INDEX_DIR / "ids.json"

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def build_index() -> None:
    """One-shot index build. Run after `data/build_catalog.py`."""
    from rank_bm25 import BM25Okapi
    from sentence_transformers import SentenceTransformer

    catalog = json.loads(CATALOG_PATH.read_text())
    ids = [item["entity_id"] for item in catalog]
    texts = [item["text_for_embedding"] for item in catalog]

    model = SentenceTransformer(EMBED_MODEL)
    dense = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)

    bm25 = BM25Okapi([_tokenize(t) for t in texts])

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    np.save(DENSE_PATH, dense)
    BM25_PATH.write_bytes(pickle.dumps(bm25))
    IDS_PATH.write_text(json.dumps(ids))
    print(f"Built index: {len(ids)} items, dense shape {dense.shape}")


class Index:
    """Loaded retrieval index. Singleton via module-level `get_index()`."""

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        self.ids: list[str] = json.loads(IDS_PATH.read_text())
        self.dense: np.ndarray = np.load(DENSE_PATH)
        self.bm25 = pickle.loads(BM25_PATH.read_bytes())
        self.encoder = SentenceTransformer(EMBED_MODEL)

    def encode_query(self, query: str) -> np.ndarray:
        return self.encoder.encode([query], normalize_embeddings=True)[0]

    def dense_topk(self, query_vec: np.ndarray, k: int = 50) -> list[str]:
        scores = self.dense @ query_vec
        top = np.argsort(-scores)[:k]
        return [self.ids[i] for i in top]

    def bm25_topk(self, query: str, k: int = 50) -> list[str]:
        scores = self.bm25.get_scores(_tokenize(query))
        top = np.argsort(-scores)[:k]
        return [self.ids[i] for i in top]


_INDEX: Index | None = None


def get_index() -> Index:
    global _INDEX
    if _INDEX is None:
        _INDEX = Index()
    return _INDEX


if __name__ == "__main__":
    build_index()
