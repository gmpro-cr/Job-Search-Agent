import numpy as np
import pytest
from embeddings import cosine, to_blob, from_blob, semantic_score


def test_cosine_identical_is_one():
    v = np.array([1, 2, 3], dtype=np.float32)
    assert abs(cosine(v, v) - 1.0) < 1e-6


def test_cosine_orthogonal_is_zero():
    assert abs(cosine(np.array([1, 0], dtype=np.float32),
                      np.array([0, 1], dtype=np.float32))) < 1e-6


def test_cosine_none_is_zero():
    assert cosine(None, np.array([1.0])) == 0.0


def test_blob_roundtrip():
    v = np.array([0.1, -0.2, 0.3], dtype=np.float32)
    assert np.allclose(from_blob(to_blob(v)), v)


def test_blob_none_roundtrip():
    assert to_blob(None) is None
    assert from_blob(None) is None


def test_semantic_score_rescales_and_clamps():
    assert semantic_score(0.20) == 0.0
    assert semantic_score(0.70) == 100.0
    assert semantic_score(0.45) == pytest.approx(50.0)
    assert semantic_score(0.90) == 100.0   # clamp high
    assert semantic_score(0.00) == 0.0     # clamp low
