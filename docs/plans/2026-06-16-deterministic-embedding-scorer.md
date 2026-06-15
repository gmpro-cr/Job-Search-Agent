# Deterministic + Embedding Scorer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace LLM job scoring with a deterministic + local-embedding scorer so digests never depend on an LLM provider, while keeping match quality high and fully explainable.

**Architecture:** One unified `score_job()` in `scorer.py` blends `0.55*deterministic + 0.45*semantic` and applies deterministic hard-gates. Deterministic = upgraded keyword scoring (synonym/industry maps, rapidfuzz fuzzy, experience-range parsing, negation). Semantic = numpy cosine of `all-MiniLM-L6-v2` embeddings, computed only in GitHub Actions and stored as float32 vectors on `job_listings.embedding` and `user_cv_data.cv_embedding`; Vercel reads vectors and does numpy cosine only.

**Tech Stack:** Python, numpy (slim deploy), rapidfuzz (slim deploy), sentence-transformers (Actions only), SQLite/Neon Postgres dual-driver.

**Design doc:** `docs/plans/2026-06-16-deterministic-embedding-scorer-design.md`

**Test command (always force SQLite — `.env` points at Neon):**
```
DATABASE_URL="" DATABASE_URL_DIRECT="" DATA_DIR=$(mktemp -d) python3 -m pytest <path> -q
```

**Interpreter:** `python3` (the venv/ is gutted; anaconda python3 has the deps). numpy + rapidfuzz already installed; sentence-transformers is NOT installed locally — semantic tests use synthetic precomputed vectors only.

**Pre-existing failing tests (do not attribute to this work):** `tests/test_database.py::test_get_jobs_for_reminder_*` (3) fail on baseline.

---

### Task 1: Scoring maps (`scoring_maps.py`)

**Files:**
- Create: `scoring_maps.py`
- Test: `tests/test_scoring_maps.py`

**Step 1: Write the failing test**
```python
from scoring_maps import canonical_terms, expand_terms

def test_title_synonyms_expand():
    assert "product manager" in expand_terms("PM", "title")
    assert "product manager" in expand_terms("Product Owner", "title")

def test_industry_synonyms_expand():
    assert "fintech" in expand_terms("NBFC", "industry")
    assert "lending" in expand_terms("fintech", "industry")

def test_unknown_term_returns_itself():
    assert expand_terms("astronaut", "title") == {"astronaut"}
```

**Step 2: Run test, expect fail** (`ModuleNotFoundError: scoring_maps`).

**Step 3: Implement**
```python
"""Curated synonym/alias clusters for deterministic scoring. Editable by hand."""

_TITLE_CLUSTERS = [
    {"product manager", "pm", "product owner", "apm", "associate product manager",
     "senior product manager", "group product manager", "product lead"},
    {"credit analyst", "credit risk", "underwriting", "credit appraisal",
     "credit manager", "risk analyst"},
    {"business analyst", "ba", "business analysis"},
    {"program manager", "programme manager", "project manager"},
    {"data analyst", "analytics", "business intelligence"},
]
_SKILL_CLUSTERS = [
    {"a/b testing", "experimentation", "ab testing", "split testing"},
    {"gtm", "go-to-market", "go to market"},
    {"sql", "queries", "data analysis"},
    {"stakeholder management", "stakeholder", "cross-functional"},
]
_INDUSTRY_CLUSTERS = [
    {"fintech", "lending", "nbfc", "payments", "banking", "credit", "financial services"},
    {"saas", "b2b saas", "enterprise software"},
    {"ai/ml", "ai", "ml", "machine learning", "artificial intelligence"},
    {"ecommerce", "e-commerce", "retail", "marketplace"},
]
_TABLES = {"title": _TITLE_CLUSTERS, "skill": _SKILL_CLUSTERS, "industry": _INDUSTRY_CLUSTERS}


def expand_terms(term, table):
    """Return the full equivalence set for `term` (lowercased). If `term` matches
    no cluster, return {term} so callers still match the literal term."""
    t = (term or "").lower().strip()
    if not t:
        return set()
    out = {t}
    for cluster in _TABLES.get(table, []):
        if t in cluster:
            out |= cluster
    return out


def canonical_terms(terms, table):
    """Expand a list of terms into one combined set of equivalents."""
    out = set()
    for term in terms or []:
        out |= expand_terms(term, table)
    return out
```

**Step 4: Run test, expect PASS.**

**Step 5: Commit**
```bash
git add scoring_maps.py tests/test_scoring_maps.py
git commit -m "feat(scorer): add synonym/industry clusters (scoring_maps)"
```

---

### Task 2: Embedding utilities (`embeddings.py`)

**Files:**
- Create: `embeddings.py`
- Test: `tests/test_embeddings.py`

**Step 1: Write the failing test** (synthetic vectors — no model)
```python
import numpy as np
from embeddings import cosine, to_blob, from_blob, semantic_score

def test_cosine_identical_is_one():
    v = np.array([1, 2, 3], dtype=np.float32)
    assert abs(cosine(v, v) - 1.0) < 1e-6

def test_cosine_orthogonal_is_zero():
    assert abs(cosine(np.array([1,0],dtype=np.float32), np.array([0,1],dtype=np.float32))) < 1e-6

def test_cosine_none_is_zero():
    assert cosine(None, np.array([1.0])) == 0.0

def test_blob_roundtrip():
    v = np.array([0.1, -0.2, 0.3], dtype=np.float32)
    assert np.allclose(from_blob(to_blob(v)), v)

def test_semantic_score_rescales_and_clamps():
    assert semantic_score(0.20) == 0.0
    assert semantic_score(0.70) == 100.0
    assert semantic_score(0.45) == 50.0
    assert semantic_score(0.90) == 100.0
    assert semantic_score(0.00) == 0.0
```

**Step 2: Run, expect fail.**

**Step 3: Implement**
```python
"""Embedding helpers. cosine/to_blob/from_blob/semantic_score are Vercel-safe
(numpy only). embed_texts() lazy-imports sentence-transformers and runs only in
GitHub Actions (sentence-transformers lives in requirements-scraper.txt)."""
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
    if vec is None:
        return None
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob):
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
```

**Step 4: Run, expect PASS.**

**Step 5: Commit**
```bash
git add embeddings.py tests/test_embeddings.py
git commit -m "feat(scorer): add embedding cosine/serialize/semantic-score utils"
```

---

### Task 3: Deterministic scorer (`scorer.py` — part 1)

**Files:**
- Create: `scorer.py`
- Test: `tests/test_scorer.py`

**Step 1: Write failing tests**
```python
from scorer import deterministic_score

PREFS = {"job_titles": ["Product Manager"], "locations": ["Pune"],
         "industries": ["Fintech"], "transferable_skills": ["Stakeholder Management"]}
CV = {"skills": ["Product Strategy", "A/B Testing", "Stakeholder Management"]}

def _job(role, jd="", loc="Pune", remote="hybrid"):
    return {"role": role, "job_description": jd, "location": loc, "remote_status": remote}

def test_exact_title_strong_score():
    s, bd = deterministic_score(_job("Product Manager", "We need product strategy and A/B testing."), CV, PREFS)
    assert bd["title"] >= 40 and s >= 70

def test_synonym_title_matches():
    s, bd = deterministic_score(_job("Product Owner"), CV, PREFS)
    assert bd["title"] >= 30

def test_industry_synonym_bonus():
    s, bd = deterministic_score(_job("Product Manager", "Lending and NBFC platform"), CV, PREFS)
    assert bd["domain"] > 0

def test_seniority_gate_penalizes_director():
    s, bd = deterministic_score(_job("Director of Product", "15+ years required"), CV, PREFS)
    assert bd["seniority_penalty"] < 0

def test_negation_blocks_false_title_match():
    s, bd = deterministic_score(_job("Sales Rep", "No product management experience required"), CV, PREFS)
    assert bd["title"] == 0

def test_irrelevant_domain_gate():
    s, bd = deterministic_score(_job("Registered Nurse", "ICU nursing"), CV, PREFS)
    assert bd.get("irrelevant") is True and s == 0
```

**Step 2: Run, expect fail.**

**Step 3: Implement `scorer.py` (deterministic part)**
```python
"""Unified deterministic + semantic job scorer. See
docs/plans/2026-06-16-deterministic-embedding-scorer-design.md."""
import re
from rapidfuzz import fuzz
from scoring_maps import canonical_terms

_IRRELEVANT = ["nurse", "nursing", "phlebotom", "welder", "electrician",
               "truck driver", "chef", "barista", "security guard"]
_SENIOR_TITLE = re.compile(r"\b(vp|vice president|director|head of|chief|cxo|cto|ceo|cfo)\b", re.I)
_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|to)?\s*(\d{1,2})?\s*\+?\s*years?", re.I)
_NEG = re.compile(r"\b(no|not|without|don't need|do not need)\b[^.]{0,40}", re.I)

_FUZZ_STRONG = 88
_FUZZ_PARTIAL = 70


def _negated_text(text):
    return " ".join(m.group(0).lower() for m in _NEG.finditer(text or ""))


def _term_in(term, text):
    """Fuzzy presence: substring OR high token_set_ratio against the text."""
    if not term or not text:
        return 0
    t, x = term.lower(), text.lower()
    if t in x:
        return _FUZZ_STRONG
    return fuzz.token_set_ratio(t, x)


def deterministic_score(job, cv_data, preferences):
    cv_data = cv_data or {}
    preferences = preferences or {}
    role = (job.get("role") or "").lower()
    jd = (job.get("job_description") or "").lower()
    text = f"{role} {jd}"
    bd = {"title": 0, "location": 0, "cv_skills": 0, "domain": 0,
          "experience": 0, "seniority_penalty": 0}

    if any(k in text for k in _IRRELEVANT):
        bd["irrelevant"] = True
        return 0, bd

    negated = _negated_text(text)

    title_terms = canonical_terms(preferences.get("job_titles", []), "title")
    best = 0
    for term in title_terms:
        if term in negated:
            continue
        r = _term_in(term, role)
        if r >= _FUZZ_STRONG:
            best = max(best, 45)
        elif r >= _FUZZ_PARTIAL:
            best = max(best, 30)
    bd["title"] = best

    locs = {l.lower() for l in preferences.get("locations", []) if l.strip()}
    locs |= {"remote", "hybrid", "wfh", "work from home", "work from anywhere"}
    job_loc = f"{job.get('location','')} {job.get('remote_status','')}".lower()
    if any(l in job_loc or l in text for l in locs):
        bd["location"] = 15

    skills = canonical_terms(
        (cv_data.get("skills") or []) + (preferences.get("transferable_skills") or []), "skill")
    hits = sum(1 for s in skills if s and _term_in(s, text) >= _FUZZ_STRONG)
    bd["cv_skills"] = min(hits * 5, 30)

    inds = canonical_terms(preferences.get("industries", []), "industry")
    dhits = sum(1 for d in inds if d and d in text)
    bd["domain"] = min(dhits * 5, 10)

    if _SENIOR_TITLE.search(role):
        bd["seniority_penalty"] = -15
    else:
        m = _YEARS.search(jd)
        if m:
            lo = int(m.group(1))
            if lo >= 10:
                bd["seniority_penalty"] = -15
            elif lo <= 6:
                bd["experience"] = 5

    total = (bd["title"] + bd["location"] + bd["cv_skills"] + bd["domain"]
             + bd["experience"] + bd["seniority_penalty"])
    return max(0, min(100, total)), bd
```

**Step 4: Run, expect PASS** (tune `_FUZZ_*` only if a case is off; keep all 6 green).

**Step 5: Commit**
```bash
git add scorer.py tests/test_scorer.py
git commit -m "feat(scorer): deterministic component (synonyms, fuzzy, experience, negation, gates)"
```

---

### Task 4: Blended `score_job()` (`scorer.py` — part 2)

**Files:**
- Modify: `scorer.py`
- Test: `tests/test_scorer.py` (reuse module-level `CV`/`PREFS`)

**Step 1: Write failing tests**
```python
import numpy as np
from scorer import score_job

def test_blend_uses_both_components():
    job = {"role": "Product Manager", "job_description": "product strategy", "location": "Pune", "remote_status": "hybrid"}
    jv = np.array([1.0, 0.0], dtype=np.float32)
    pv = np.array([1.0, 0.0], dtype=np.float32)
    s, bd = score_job(job, CV, PREFS, job_vec=jv, profile_vec=pv)
    assert bd["semantic"] == 100 and bd["deterministic"] > 0
    assert bd["blended"] == s and 0 <= s <= 100

def test_missing_vectors_is_deterministic_only():
    job = {"role": "Product Manager", "location": "Pune", "remote_status": "hybrid"}
    s, bd = score_job(job, CV, PREFS, job_vec=None, profile_vec=None)
    assert bd["semantic"] == 0 and s == round(0.55 * bd["deterministic"])

def test_irrelevant_gate_overrides_semantic():
    job = {"role": "Registered Nurse", "job_description": "ICU"}
    jv = pv = np.array([1.0, 0.0], dtype=np.float32)
    s, bd = score_job(job, CV, PREFS, job_vec=jv, profile_vec=pv)
    assert s == 0
```

**Step 2: Run, expect fail.**

**Step 3: Append to `scorer.py`**
```python
from embeddings import cosine, semantic_score

DET_WEIGHT = 0.55
SEM_WEIGHT = 0.45


def score_job(job, cv_data, preferences, job_vec=None, profile_vec=None):
    """Unified score 0-100 + breakdown. Semantic component is skipped (0) when
    either vector is missing (deterministic-only fallback)."""
    det, bd = deterministic_score(job, cv_data, preferences)
    if bd.get("irrelevant"):
        bd.update({"deterministic": 0, "semantic": 0, "blended": 0})
        return 0, bd
    sem = semantic_score(cosine(job_vec, profile_vec)) if (job_vec is not None and profile_vec is not None) else 0.0
    blended = max(0, min(100, round(DET_WEIGHT * det + SEM_WEIGHT * sem)))
    bd.update({"deterministic": round(det), "semantic": round(sem), "blended": blended})
    return blended, bd
```

**Step 4: Run, expect PASS.**

**Step 5: Commit**
```bash
git add scorer.py tests/test_scorer.py
git commit -m "feat(scorer): blended score_job (0.55 det + 0.45 sem, gates override)"
```

---

### Task 5: Schema + vector storage (`database.py`)

**Files:**
- Modify: `database.py` (`init_db` column adds; vector helpers)
- Test: `tests/test_database.py`

**Step 1: Write failing test**
```python
def test_job_and_cv_embedding_columns_and_helpers():
    from database import (init_db, get_connection, set_job_embedding,
                          get_job_embedding)
    import numpy as np
    init_db()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("PRAGMA table_info(job_listings)")
    assert "embedding" in [r["name"] for r in cur.fetchall()]
    cur.execute("PRAGMA table_info(user_cv_data)")
    assert "cv_embedding" in [r["name"] for r in cur.fetchall()]
    conn.close()
    _seed_jobs([("Embed Role EMB", 70)], prefix="emb")
    cur2 = get_connection().cursor()
    cur2.execute("SELECT job_id FROM job_listings WHERE role='Embed Role EMB'")
    jid = cur2.fetchone()["job_id"]
    v = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    set_job_embedding(jid, v)
    assert np.allclose(get_job_embedding(jid), v)
```

**Step 2: Run, expect fail.**

**Step 3: Implement**
- Near the top, define `_blob_type = "BYTEA" if USE_POSTGRES else "BLOB"`.
- In `init_db`, add:
  - `_add_columns_idempotent(conn, cursor, "job_listings", [f"embedding {_blob_type}"])`
  - `_add_columns_idempotent(conn, cursor, "user_cv_data", [f"cv_embedding {_blob_type}"])`
- Add helpers (import `from embeddings import to_blob, from_blob` at top of module):
```python
def set_job_embedding(job_id, vec):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("UPDATE job_listings SET embedding = ? WHERE job_id = ?", (to_blob(vec), job_id))
    conn.commit(); conn.close()

def get_job_embedding(job_id):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT embedding FROM job_listings WHERE job_id = ?", (job_id,))
    row = cur.fetchone(); conn.close()
    return from_blob(row["embedding"]) if row and row["embedding"] is not None else None

def set_cv_embedding(user_id, vec):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("UPDATE user_cv_data SET cv_embedding = ? WHERE user_id = ?", (to_blob(vec), user_id))
    conn.commit(); conn.close()

def get_cv_embedding(user_id):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT cv_embedding FROM user_cv_data WHERE user_id = ?", (user_id,))
    row = cur.fetchone(); conn.close()
    return from_blob(row["cv_embedding"]) if row and row["cv_embedding"] is not None else None

def invalidate_cv_embedding(user_id):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("UPDATE user_cv_data SET cv_embedding = NULL WHERE user_id = ?", (user_id,))
    conn.commit(); conn.close()

def get_jobs_missing_embedding(limit=500):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT job_id, role, company, job_description FROM job_listings WHERE embedding IS NULL LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]; conn.close(); return rows
```

**Step 4: Run, expect PASS.**

**Step 5: Commit**
```bash
git add database.py tests/test_database.py
git commit -m "feat(db): embedding/cv_embedding columns + float32 vector helpers"
```

---

### Task 6: Invalidate cv_embedding on CV/prefs change

**Files:**
- Modify: `main.py` (`save_preferences`) and the CV-save path (`analyzer.save_cv_data` / `database.save_user_cv_data`)
- Test: `tests/test_database.py`

**Step 1: Write failing test**
```python
def test_cv_embedding_invalidated_on_prefs_save():
    from database import set_cv_embedding, get_cv_embedding, save_user_preferences, get_or_create_user, init_db, invalidate_cv_embedding
    import numpy as np
    init_db()
    uid = get_or_create_user("inval@test.local", "X")
    # ensure a user_cv_data row exists for this user (insert via existing CV-save helper)
    set_cv_embedding(uid, np.array([1,2,3], dtype=np.float32))
    save_user_preferences(uid, {"job_titles": ["X"]})
    assert get_cv_embedding(uid) is None
```
(If a `user_cv_data` row must exist first, create it via the project's CV-save helper in the test setup.)

**Step 2: Run, expect fail.**

**Step 3: Implement** — call `invalidate_cv_embedding(user_id)` from `save_user_preferences` (database.py) and from the CV-save path. For prefs saved to the per-user table, invalidate after the upsert.

**Step 4: Run, expect PASS.**

**Step 5: Commit**
```bash
git commit -am "feat(scorer): invalidate cv_embedding when CV or prefs change"
```

---

### Task 7: Wire scorer into `analyzer` + remove LLM from scoring

**Files:**
- Modify: `analyzer.py` (`cv_score`, `analyze_jobs`)
- Test: `tests/test_analyzer.py`

**Step 1: Write failing test**
```python
def test_analyze_jobs_no_llm_and_scores():
    from analyzer import analyze_jobs
    prefs = {"job_titles": ["Product Manager"], "locations": ["Pune"], "industries": ["Fintech"]}
    cv = {"skills": ["Product Strategy", "Stakeholder Management"]}
    jobs = [{"role": "Product Manager", "job_description": "fintech product strategy", "location": "Pune", "remote_status": "hybrid"},
            {"role": "Registered Nurse", "job_description": "ICU"}]
    config = {"scoring": {"min_relevance_score": 40}}
    qualified, analyzed = analyze_jobs(jobs, prefs, config, cv_data=cv)
    assert all("relevance_score" in j for j in analyzed)
    assert any(j["role"] == "Product Manager" for j in qualified)
    assert all(j["role"] != "Registered Nurse" for j in qualified)
```

**Step 2: Run, expect fail.**

**Step 3: Implement**
- `cv_score(job, cv_data, preferences=None, job_vec=None, profile_vec=None)` → `return scorer.score_job(job, cv_data, preferences or {}, job_vec, profile_vec)[0]`.
- In `analyze_jobs`: remove the Ollama→Gemini→OpenRouter `llm_score` block + circuit breaker. Score each job with `scorer.score_job(job, cv_data, preferences, job_vec=job.get("_vec"), profile_vec=profile_vec)` where `profile_vec` is a new optional kwarg (default None → deterministic-only). Keep `qualified = [j for j in analyzed if j["relevance_score"] >= min_score]` and the sort.
- Remove now-unused `agent.llm`/`llm_score` imports from `analyzer.py`. Leave `generate_tailored_points` untouched.

**Step 4: Run, expect PASS** (run `tests/test_scorer.py` + `tests/test_analyzer.py`).

**Step 5: Commit**
```bash
git commit -am "refactor(scorer): delegate analyzer scoring to scorer.py; remove LLM from scoring"
```

---

### Task 8: Embedding pipeline in the cron (`scrape_and_push.py`)

**Files:**
- Modify: `scrape_and_push.py`
- Test: `tests/test_scrape_embeddings.py` (monkeypatch `embed_texts`)

**Step 1: Write failing test**
```python
def test_cron_embeds_jobs(monkeypatch):
    import numpy as np, embeddings, scrape_and_push as sp
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts: [np.ones(4, dtype=np.float32) for _ in texts])
    jobs = [{"job_id": "x1", "role": "Product Manager", "company": "C", "job_description": "fintech"}]
    out = sp.embed_jobs(jobs)
    assert out[0]["_vec"] is not None and len(out[0]["_vec"]) == 4
```

**Step 2: Run, expect fail.**

**Step 3: Implement** in `scrape_and_push.py`:
- `embed_jobs(jobs)`: build `f"{role}. {company}. {jd[:2000]}"`, call `embeddings.embed_texts`, attach `job["_vec"]`; return jobs. (Persisting via `set_job_embedding` happens after insert, keyed by job_id.)
- Build owner `profile_vec` once from CV+prefs text via `embed_texts`; `set_cv_embedding(owner_user_id, profile_vec)`.
- Extend `analyze_jobs(...)` call to pass `profile_vec=profile_vec`; ensure jobs carry `_vec`.
- After `insert_jobs_bulk`, persist each job vector with `set_job_embedding`.
- Wrap the sentence-transformers path so a local run without it logs a warning and degrades to deterministic-only (vectors None).

**Step 4: Run, expect PASS.**

**Step 5: Commit**
```bash
git commit -am "feat(scorer): embed jobs + owner profile in cron, store vectors, score with them"
```

---

### Task 9: Backfill script + requirements + Actions cache

**Files:**
- Create: `scripts/backfill_embeddings.py`
- Modify: `requirements.txt` (add `numpy`, `rapidfuzz`), `requirements-scraper.txt` (add `sentence-transformers`)
- Modify: `.github/workflows/scrape.yml` (cache `~/.cache/huggingface` / model dir)

**Step 1:** `scripts/backfill_embeddings.py` — loop `get_jobs_missing_embedding(500)`, `embed_texts`, `set_job_embedding`, until none remain; print progress.

**Step 2:** Add deps to both requirements files; add an `actions/cache` step keyed on the model name.

**Step 3: Commit**
```bash
git add scripts/backfill_embeddings.py requirements.txt requirements-scraper.txt .github/workflows/scrape.yml
git commit -m "chore(scorer): backfill script, deps (numpy/rapidfuzz/sentence-transformers), CI model cache"
```

---

### Task 10: Calibration + end-to-end verification

**Files:**
- Create: `scripts/calibrate_scores.py`
- Modify: `config.json` (`min_relevance_score`)
- Test: `tests/test_database.py` (end-to-end, LLM-free)

**Step 1:** `scripts/calibrate_scores.py` — over a sample of stored jobs, compute `score_job` distribution, print percentiles, recommend `min_relevance_score`.

**Step 2:** Set `config.json` `min_relevance_score` to the calibrated value.

**Step 3: Write end-to-end test**
```python
def test_end_to_end_digest_non_empty_without_llm():
    from analyzer import analyze_jobs
    from database import select_digest_jobs
    prefs = {"job_titles": ["Product Manager"], "locations": ["Pune"], "industries": ["Fintech"]}
    cv = {"skills": ["Product Strategy", "Stakeholder Management"]}
    jobs = [{"role": f"Product Manager {i}", "job_description": "fintech product strategy", "location":"Pune","remote_status":"hybrid", "job_id": f"e2e-{i}"} for i in range(8)]
    qualified, _ = analyze_jobs(jobs, prefs, {"scoring": {"min_relevance_score": 40}}, cv_data=cv)
    picked = select_digest_jobs(qualified, top_n=5, days=7)
    assert len(picked) >= 1
```

**Step 4: Run full suite** (expect only the 3 pre-existing reminder failures):
```
DATABASE_URL="" DATABASE_URL_DIRECT="" DATA_DIR=$(mktemp -d) python3 -m pytest tests/ -q
```

**Step 5: Commit**
```bash
git add scripts/calibrate_scores.py config.json tests/test_database.py
git commit -m "feat(scorer): calibrate thresholds + end-to-end LLM-free digest test"
```

---

### Final: Runtime verification (verify skill)

Before merge, run the real Flask app (local SQLite, dev-login, stubbed SMTP) and `POST /api/digest/send-now`, plus `python3 scrape_and_push.py` against a seeded DB with the model installed — confirm a non-empty digest with `deterministic/semantic/blended` breakdown fields populated and zero LLM calls in the logs.

### Rollout (after merge to main)
1. Deploy (Vercel auto-deploys; numpy+rapidfuzz join the slim deploy).
2. Run `python -m scripts.backfill_embeddings` once in Actions to embed the 14k jobs.
3. Confirm the next cron digest has real blended scores; remove GROQ/OPENROUTER/Gemini scoring secrets.
