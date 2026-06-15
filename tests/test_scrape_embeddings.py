import numpy as np


def test_embed_jobs_attaches_vectors(monkeypatch):
    import embeddings
    import scrape_and_push as sp
    monkeypatch.setattr(embeddings, "embed_texts",
                        lambda texts: [np.ones(4, dtype=np.float32) for _ in texts])
    jobs = [{"job_id": "x1", "role": "Product Manager", "company": "C",
             "job_description": "fintech product"}]
    out = sp.embed_jobs(jobs)
    assert out[0]["_vec"] is not None and len(out[0]["_vec"]) == 4


def test_embed_jobs_degrades_when_model_unavailable(monkeypatch):
    import embeddings
    import scrape_and_push as sp

    def _boom(texts):
        raise RuntimeError("sentence-transformers not installed")
    monkeypatch.setattr(embeddings, "embed_texts", _boom)
    jobs = [{"job_id": "x1", "role": "PM", "company": "C", "job_description": "x"}]
    out = sp.embed_jobs(jobs)
    assert out[0]["_vec"] is None   # graceful -> deterministic-only


def test_build_profile_vec(monkeypatch):
    import embeddings
    import scrape_and_push as sp
    monkeypatch.setattr(embeddings, "embed_texts",
                        lambda texts: [np.ones(4, dtype=np.float32) for _ in texts])
    v = sp.build_profile_vec({"skills": ["Product Strategy"]},
                             {"job_titles": ["Product Manager"], "industries": ["Fintech"]})
    assert v is not None and len(v) == 4
    assert sp.build_profile_vec({}, {}) is None   # nothing to embed
