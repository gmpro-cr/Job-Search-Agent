"""Embedding helpers.

cosine/to_blob/from_blob/semantic_score are Vercel-safe (numpy only).
embed_texts() lazy-imports sentence-transformers and runs only in GitHub Actions
(sentence-transformers lives in requirements-scraper.txt, not the slim deploy).
"""
import numpy as np

_MODEL_NAME = "all-MiniLM-L6-v2"
_model = None


def cosine(a, b):
    if a is None or b is None:
        return 0.0
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def semantic_score(cos, lo=0.20, hi=0.70):
    """Linearly rescale a cosine similarity to 0-100 over [lo, hi], clamped."""
    return max(0.0, min(100.0, (cos - lo) / (hi - lo) * 100.0))


def to_blob(vec):
    """Serialize a vector to float32 bytes for BLOB/BYTEA storage."""
    if vec is None:
        return None
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob):
    """Deserialize BLOB/BYTEA bytes (or memoryview) back to a float32 array."""
    if blob is None:
        return None
    return np.frombuffer(bytes(blob), dtype=np.float32)


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer  # Actions-only
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


def embed_texts(texts):
    """Embed a list of strings -> list of float32 numpy arrays. Actions-only."""
    if not texts:
        return []
    arr = _get_model().encode(list(texts), normalize_embeddings=True)
    return [np.asarray(v, dtype=np.float32) for v in arr]
