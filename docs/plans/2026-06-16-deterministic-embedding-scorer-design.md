# Deterministic + Embedding Scorer — Design

**Date:** 2026-06-16
**Status:** Validated, ready for implementation
**Goal:** Replace LLM job scoring with a deterministic + local-embedding scorer
so digests never depend on an LLM provider (no quota, 429, secrets, or empty
digests), while keeping match quality close to the LLM and fully explainable.

## Motivation

The daily digest produced 0 jobs because all LLM providers were unavailable
(Ollama down, Gemini 429 quota exhausted, OpenRouter key unset). Scoring fell
back to keyword matching, only ~5/1760 jobs qualified at `min_relevance_score=65`,
and the dedup filter excluded those few. The `select_digest_jobs` backfill fixed
the empty-digest symptom; this design removes the root dependency — the LLM — from
scoring entirely.

## Section 1 — Scoring model

**Unify the two scorers.** `relevance_score` (digest filter) and `cv_score`
(per-user rank) become one function scoring a `(job, profile)` pair 0–100, where
*profile* = a user's CV + preferences. The owner is just another profile.

**Final score = weighted blend + hard gates:**

```
score = round(0.55 * deterministic + 0.45 * semantic)   # then apply gates
```

- **Deterministic (0–100)** — upgraded keyword scorer (Section 3).
- **Semantic (0–100)** — `cosine(job_embedding, profile_embedding)` linearly
  rescaled from a calibrated band (e.g. cosine 0.20→0, 0.70→100, clamped).
- **Hard gates (applied last, deterministic):** irrelevant-domain → 0;
  seniority mismatch (10+ yrs / VP / Director vs candidate level) → capped
  penalty. A strong semantic score cannot bypass these.

**Explainability:** `score_breakdown` gains `deterministic`, `semantic`, and
`blended` fields. Every score remains auditable.

Weights (55/45) and the cosine band are calibrated against the existing ~14k
scored jobs before locking.

## Section 2 — Embeddings & storage

**Model:** `all-MiniLM-L6-v2` (384-dim, ~80 MB, `sentence-transformers`) — in
`requirements-scraper.txt`, loaded **only in GitHub Actions**, never on Vercel.

**Embedded text:**
- *Job vector* → `role + company + job_description[:2000]`.
- *Profile vector* → CV summary + skills + `job_titles` + `industries`.

**Storage** (dual-driver, raw float32 bytes via `np.float32(vec).tobytes()` /
`np.frombuffer`):
- `job_listings.embedding` — BLOB (SQLite) / BYTEA (Postgres). ~1.5 KB/job →
  ~22 MB for 14k jobs (fine on Neon free tier).
- `user_cv_data.cv_embedding` — one per user, nullable.

**Writers:** the cron embeds jobs where `embedding IS NULL` and recomputes
`cv_embedding IS NULL`. One-time `scripts/backfill_embeddings.py` embeds existing
jobs (run in Actions).

**Readers:** Vercel reads stored vectors and computes only `numpy` cosine.
`numpy` is added to the slim `requirements.txt`; no torch on Vercel.

**Bounded CV-change lag (the one caveat to "fully consistent"):** job scoring is
always consistent (job vectors are pre-stored). The *profile* vector can only be
recomputed by the model in Actions, so after a user edits CV/prefs on Vercel,
`cv_embedding` is set NULL and the immediate rescore uses deterministic-only for
the semantic component until the next cron (twice daily). Affects only the
editing user, only for a few hours.

## Section 3 — Deterministic component (upgrades)

Band structure stays (sums to 100, explainable). Upgrades over substring
matching:

1. **Synonym/alias maps** (curated, editable `scoring_maps.py`):
   - Titles: `Product Manager ↔ PM ↔ Product Owner ↔ APM`;
     `Credit Analyst ↔ Credit Risk ↔ Underwriting ↔ Credit Appraisal`; etc.
   - Skills: variants (`A/B Testing ↔ experimentation`, `GTM ↔ go-to-market`).
   - Industries: `fintech ↔ lending ↔ NBFC ↔ payments ↔ banking ↔ credit`.
2. **Fuzzy matching** via `rapidfuzz` (`token_set_ratio`), word-boundary aware.
3. **Experience-range parsing** — regex `"5–8 years"`, `"minimum 10 years"`,
   `"8+ yrs"`; compare to candidate level (in-range bonus, over-senior penalty).
4. **Negation handling** — detect `"not required"` / `"no X experience"` near a
   matched requirement.
5. **Bands** (recalibrated to sum to 100): title, location/remote, CV-skill
   overlap, industry/domain, experience-fit + irrelevant-domain hard-zero and
   seniority gate.

`rapidfuzz` is a small pure-wheel dependency, safe for both deploys.

## Section 4 — Integration, migration, testing, rollout

**Code structure:**
- `scorer.py` — `score_job(job, profile, job_vec=None, profile_vec=None) ->
  (score, breakdown)`. `analyzer.cv_score` and `analyze_jobs` delegate to it.
- `scoring_maps.py` — synonym/industry tables.
- `embeddings.py` — `embed_texts()` (Actions-only, lazy import) and `cosine()`
  (numpy, Vercel-safe).

**Schema migration** (idempotent, both drivers): add `job_listings.embedding`
and `user_cv_data.cv_embedding`. Set `cv_embedding=NULL` on CV/prefs change.

**Dependencies:** `numpy` + `rapidfuzz` → slim `requirements.txt`;
`sentence-transformers` → `requirements-scraper.txt`. Actions caches the model.

**Remove from scoring:** `llm_score` call path in `analyze_jobs` and the
`agent/llm.py` chain for scoring. (LLM stays for `generate_tailored_points`.) No
GROQ/OPENROUTER/Gemini keys needed for scoring.

**Calibration:** after backfill, inspect the blended-score distribution over the
14k jobs, then lock `min_relevance_score`, cosine band, and weights.

**Testing (TDD):** unit tests for synonyms, fuzzy, experience parsing, negation,
gates; semantic blend with precomputed vectors; `cosine()`; end-to-end
`analyze_jobs` → `select_digest_jobs` producing a non-empty, LLM-free digest.

**Rollout:** migrate schema → backfill embeddings (Actions) → calibrate →
deploy → verify digest non-empty with real scores.

## Out of scope (YAGNI)

- LLM reranking of the top-N (revisit only if match quality is missed).
- Embedding the model on Vercel (deliberately avoided — keeps deploy slim).
- Approximate-nearest-neighbour indexes (linear cosine over a user's candidate
  set is fast enough at this scale).
