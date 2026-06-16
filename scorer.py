"""Unified deterministic + semantic job scorer.

See docs/plans/2026-06-16-deterministic-embedding-scorer-design.md.
The deterministic component replaces the old keyword_score with synonym-aware,
fuzzy matching plus experience-range parsing, negation handling, and hard gates.
"""
import re
from rapidfuzz import fuzz
from scoring_maps import canonical_terms
from embeddings import cosine, semantic_score

# Semantic agreement LIFTS a deterministic match by up to this many points
# (never sinks it). A pure weighted average was miscalibrated: MiniLM cosines
# for profile-vs-JD cluster low (~0.2-0.5), so a 0.45 semantic weight dragged
# strong deterministic matches below the threshold. An additive, bounded bonus
# keeps the deterministic score as the floor (blended >= deterministic) while
# still rewarding genuine semantic similarity.
SEMANTIC_BONUS_MAX = 20

# Domains that mean "definitely not for this candidate" -> hard zero.
_IRRELEVANT = ["nurse", "nursing", "phlebotom", "welder", "electrician",
               "truck driver", "chef", "barista", "security guard"]
_SENIOR_TITLE = re.compile(r"\b(vp|vice president|director|head of|chief|cxo|cto|ceo|cfo)\b", re.I)
_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|to)?\s*(\d{1,2})?\s*\+?\s*years?", re.I)
_NEG = re.compile(r"\b(no|not|without|don't need|do not need)\b[^.]{0,40}", re.I)

_FUZZ_STRONG = 88    # token_set_ratio at/above this = strong match
_FUZZ_PARTIAL = 70


def _negated_text(text):
    """Concatenated text spans that follow a negation cue, so we can avoid
    crediting requirements the JD explicitly says are NOT needed."""
    return " ".join(m.group(0).lower() for m in _NEG.finditer(text or ""))


def _term_in(term, text):
    """Fuzzy presence: exact substring -> strong; otherwise token_set_ratio."""
    if not term or not text:
        return 0
    t, x = term.lower(), text.lower()
    if t in x:
        return _FUZZ_STRONG
    return fuzz.token_set_ratio(t, x)


def deterministic_score(job, cv_data, preferences):
    """Return (score_0_100, breakdown). breakdown carries each band plus an
    'irrelevant' flag and the seniority penalty for full explainability."""
    cv_data = cv_data or {}
    preferences = preferences or {}
    role = (job.get("role") or "").lower()
    jd = (job.get("job_description") or "").lower()
    text = f"{role} {jd}"
    bd = {"title": 0, "location": 0, "cv_skills": 0, "domain": 0,
          "experience": 0, "seniority_penalty": 0}

    # Irrelevant-domain hard gate.
    if any(k in text for k in _IRRELEVANT):
        bd["irrelevant"] = True
        return 0, bd

    negated = _negated_text(text)

    # Title (0-45): synonym-expanded + fuzzy vs role, skipping negated mentions.
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

    # Location (0-15): preferred locations + remote synonyms.
    locs = {l.lower() for l in preferences.get("locations", []) if l.strip()}
    locs |= {"remote", "hybrid", "wfh", "work from home", "work from anywhere"}
    job_loc = f"{job.get('location', '')} {job.get('remote_status', '')}".lower()
    if any(l in job_loc or l in text for l in locs):
        bd["location"] = 15

    # CV-skill overlap (0-30): synonym-expanded, 5 pts per distinct strong hit.
    skills = canonical_terms(
        (cv_data.get("skills") or []) + (preferences.get("transferable_skills") or []), "skill")
    hits = sum(1 for s in skills if s and _term_in(s, text) >= _FUZZ_STRONG)
    bd["cv_skills"] = min(hits * 5, 30)

    # Industry/domain (0-10).
    inds = canonical_terms(preferences.get("industries", []), "industry")
    dhits = sum(1 for d in inds if d and d in text)
    bd["domain"] = min(dhits * 5, 10)

    # Experience fit: penalize over-senior, small bonus for in-range.
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


def score_job(job, cv_data, preferences, job_vec=None, profile_vec=None):
    """Unified score 0-100 + breakdown, blending the deterministic component
    with embedding cosine similarity. The semantic component is skipped (0) when
    either vector is missing (deterministic-only fallback). Hard gates from the
    deterministic component (irrelevant domain) override the blend."""
    det, bd = deterministic_score(job, cv_data, preferences)
    if bd.get("irrelevant"):
        bd.update({"deterministic": 0, "semantic": 0, "blended": 0})
        return 0, bd
    if job_vec is not None and profile_vec is not None:
        sem = semantic_score(cosine(job_vec, profile_vec))
        # Deterministic score is the floor; semantic adds a bounded lift.
        blended = round(det + SEMANTIC_BONUS_MAX * (sem / 100.0))
    else:
        sem = 0.0
        blended = round(det)
    blended = max(0, min(100, blended))
    bd.update({"deterministic": round(det), "semantic": round(sem), "blended": blended})
    return blended, bd
