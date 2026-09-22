"""Unified deterministic + semantic job scorer.

See docs/plans/2026-06-16-deterministic-embedding-scorer-design.md and
docs/plans/2026-09-23-bfsi-ai-scoring-design.md.

The deterministic component is synonym-aware fuzzy matching with negation
handling and hard gates, weighted towards the candidate's two differentiators:
a BFSI background and an AI/ML background, each scored in its own band.

Seniority/experience grading was removed on 2026-09-23 — `experience_min` /
`experience_max` are still extracted in analyzer.py and remain available as an
explicit user filter, rather than being folded invisibly into the score.
"""
import re
from rapidfuzz import fuzz
from scoring_maps import (canonical_terms, domain_terms, domain_terms_ranked,
                          term_in_text, term_pattern)
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
_NEG = re.compile(r"\b(no|not|without|don't need|do not need)\b[^.]{0,40}", re.I)

# Phrases that turn a passing mention of a domain into a stated requirement.
# "5 years in lending" counts; "we are a lending company" does not.
_REQ_CUE = re.compile(
    r"(experience|background|domain knowledge|domain expertise|must[ -]have|"
    r"required|requirement|expertise|familiar|understanding of|exposure to|"
    r"proficien|knowledge of|worked (?:in|on|with)|years? (?:in|of)|"
    r"strong grasp|deep understanding|prior work)", re.I)

# How far either side of a domain term we look for a requirement cue.
_REQ_WINDOW = 60

# Full band when the JD asks for the background, half when the sector is
# merely mentioned. Applies to both the BFSI and AI bands.
_BAND_REQUIRED = 20
_BAND_SECTOR = 10

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


def _band_score(terms, text, negated):
    """Score one domain band: _BAND_REQUIRED when the JD states the background
    as a requirement, _BAND_SECTOR for a bare mention, 0 when absent.

    Returns (points, evidence) where evidence is the matched term, for
    explainability in the UI breakdown.
    """
    sector_hit = None
    for term in terms:
        pat = term_pattern(term)
        if not pat:
            continue
        m = pat.search(text)
        if not m:
            continue
        # A term the JD explicitly says is NOT needed earns nothing.
        if negated and term_in_text(term, negated):
            continue
        window = text[max(0, m.start() - _REQ_WINDOW):m.end() + _REQ_WINDOW]
        if _REQ_CUE.search(window):
            return _BAND_REQUIRED, term        # requirement beats sector; stop early
        if sector_hit is None:
            sector_hit = term
    if sector_hit:
        return _BAND_SECTOR, sector_hit
    return 0, None


def deterministic_score(job, cv_data, preferences):
    """Return (score_0_100, breakdown).

    Bands: title 0-30, location 0-10, cv_skills 0-20, bfsi 0-20, ai 0-20,
    domain (generic industries) 0-5, minus a 15-point no_domain penalty when
    the posting carries neither a BFSI nor an AI signal.

    BFSI and AI are scored separately and weighted above everything except the
    title because they are the candidate's differentiators (9 yrs banking /
    credit risk, shipped LLM products). A posting that merely operates in the
    sector scores half of one that asks for the background outright.

    breakdown carries each band plus an 'irrelevant' flag and the matched
    bfsi/ai evidence terms for full explainability.
    """
    cv_data = cv_data or {}
    preferences = preferences or {}
    role = (job.get("role") or "").lower()
    jd = (job.get("job_description") or "").lower()
    text = f"{role} {jd}"
    bd = {"title": 0, "location": 0, "cv_skills": 0, "bfsi": 0, "ai": 0,
          "domain": 0, "no_domain_penalty": 0}

    # Irrelevant-domain hard gate.
    if any(k in text for k in _IRRELEVANT):
        bd["irrelevant"] = True
        return 0, bd

    negated = _negated_text(text)

    # Title (0-30): synonym-expanded + fuzzy vs role, skipping negated mentions.
    title_terms = canonical_terms(preferences.get("job_titles", []), "title")
    best = 0
    for term in title_terms:
        if term in negated:
            continue
        r = _term_in(term, role)
        if r >= _FUZZ_STRONG:
            best = max(best, 30)
        elif r >= _FUZZ_PARTIAL:
            best = max(best, 20)
    bd["title"] = best

    # Location (0-10): preferred locations + remote synonyms.
    locs = {l.lower() for l in preferences.get("locations", []) if l.strip()}
    locs |= {"remote", "hybrid", "wfh", "work from home", "work from anywhere"}
    job_loc = f"{job.get('location', '')} {job.get('remote_status', '')}".lower()
    if any(l in job_loc or l in text for l in locs):
        bd["location"] = 10

    # CV-skill overlap (0-20): synonym-expanded, 5 pts per distinct strong hit.
    skills = canonical_terms(
        (cv_data.get("skills") or []) + (preferences.get("transferable_skills") or []), "skill")
    hits = sum(1 for s in skills if s and _term_in(s, text) >= _FUZZ_STRONG)
    bd["cv_skills"] = min(hits * 5, 20)

    # BFSI background (0-20) and AI background (0-20).
    bd["bfsi"], bfsi_term = _band_score(domain_terms_ranked("bfsi"), text, negated)
    bd["ai"], ai_term = _band_score(domain_terms_ranked("ai"), text, negated)
    if bfsi_term:
        bd["bfsi_match"] = bfsi_term
    if ai_term:
        bd["ai_match"] = ai_term

    # Generic industry (0-5), for preferred sectors outside BFSI/AI (saas,
    # ecommerce, ...). Terms already covered by a dedicated band are skipped so
    # fintech/AI cannot be counted twice.
    covered = domain_terms("bfsi") | domain_terms("ai")
    inds = canonical_terms(preferences.get("industries", []), "industry") - covered
    dhits = sum(1 for d in inds if d and term_in_text(d, text))
    bd["domain"] = min(dhits * 5, 5)

    # Neither differentiator present -> demote, but never hard-zero: a strong
    # generalist PM role can still clear the threshold on its other bands.
    if not bd["bfsi"] and not bd["ai"]:
        bd["no_domain_penalty"] = -15

    total = (bd["title"] + bd["location"] + bd["cv_skills"] + bd["bfsi"]
             + bd["ai"] + bd["domain"] + bd["no_domain_penalty"])
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
