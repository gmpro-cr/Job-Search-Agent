"""Unified deterministic + semantic job scorer.

See docs/plans/2026-06-16-deterministic-embedding-scorer-design.md and
docs/plans/2026-09-23-bfsi-ai-scoring-design.md.

The deterministic component is synonym-aware fuzzy matching with negation
handling and hard gates, weighted towards the candidate's two differentiators,
each scored in its own band. Which two depends on the candidate's scoring
profile (scoring_maps.SCORING_PROFILES): BFSI + AI for a product manager with
a banking and LLM background, delivery methods + PM governance for a project
manager. See resolve_scoring_profile().

Seniority/experience grading was removed on 2026-09-23 as an invisible
factor. It returns on 2026-10-21 as named fit rules (too-senior title,
experience floor) that show up as their own lines in the breakdown, alongside
the other conversion-oriented rules from docs/CANDIDATE_CONTEXT.md. See
_fit_adjustments() and docs/plans/2026-10-21-conversion-scoring-design.md.

score_job() optionally takes a per-user outcome model (outcome_model.py) that
learns from the candidate's own application outcomes; it adds a bounded
"track record" line and is a no-op until there are outcomes to learn from.
"""
import re
from rapidfuzz import fuzz
from scoring_maps import (BANKING_TRACK_TITLE_RE, CREDIT_MOAT_TERMS,
                          DEFAULT_PROFILE, DOMAIN_MISMATCH_TERMS,
                          ENGINEERING_TITLE_RE, FEE_RED_FLAG_RE,
                          PERSONAL_EMAIL_DOMAINS, PM_TENURE_RE, SCORING_PROFILES,
                          SENIOR_PRODUCT_TITLE_RE, SENIOR_TITLE_RE,
                          VOICE_AI_TERMS, canonical_terms, domain_terms,
                          domain_terms_ranked, profile_signal_terms,
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
# Matched against the ROLE TITLE only, on a leading word boundary. Scanning the
# whole JD body substring-matched perks and unrelated prose: a bank's "Senior
# Product Manager (UK Cards)" was hard-zeroed by "lunches prepared by in-house
# chefs", a French PM role by "Chef de projet", and a health-tech PM role by
# "doctors, nurses and care teams". No trailing \b — "phlebotom" is a prefix.
_IRRELEVANT_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _IRRELEVANT) + r")", re.I)
_NEG = re.compile(r"\b(no|not|without|don't need|do not need)\b[^.]{0,40}", re.I)

# Phrases that turn a passing mention of a domain into a stated requirement.
# "5 years in lending" counts; "we are a lending company" does not.
_REQ_CUE = re.compile(
    r"(experience|background|domain knowledge|domain expertise|must[ -]have|"
    r"required|requirement|expertise|familiar|understanding of|exposure to|"
    r"proficien|knowledge of|worked (?:in|on|with)|years? (?:in|of)|"
    r"strong grasp|deep understanding|prior work)", re.I)

# Bump when a scoring change means stored per-user scores must be recomputed;
# the scrape compares it (with the profile) against each user's last full
# rescore stamp. See scoring_stamp().
SCORER_VERSION = "2026-10-21"

# How far either side of a domain term we look for a requirement cue.
_REQ_WINDOW = 60

# Full band when the JD asks for the background, half when the sector is
# merely mentioned. Applies to both differentiator bands of every profile.
_BAND_REQUIRED = 20
_BAND_SECTOR = 10

# Fit rules (points). The sum of all fired rules is clamped to
# [_FIT_FLOOR, _FIT_CEIL] so no stack of rules can dominate the base bands.
_FIT_POINTS = {
    "seniority": -12, "exp_floor": -10, "pm_tenure": -8, "domain_mismatch": -10,
    "engineering": -20, "bank_employer": -8, "relocation": -10, "red_flag": -15,
    "ai_x_bfsi": 5, "voice_ai": 5, "target_company": 10,
}
_MOAT_PER_TERM, _MOAT_MAX = 4, 10
# Penalties that mean the role itself is the wrong shape; they switch boosts off.
_SHAPE_RULES = {"engineering", "domain_mismatch", "red_flag"}
_FIT_FLOOR, _FIT_CEIL = -30, 20
# Experience floor this many years above the candidate's = out of reach.
_EXP_FLOOR_GAP = 4
# Demanded PM tenure at/above this = a stated requirement the candidate
# (no formal PM title on record) cannot meet.
_PM_TENURE_MIN = 4

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


def _cv_text(cv_data, preferences):
    """Lowercased text the profile detector reads: CV body, skills and the
    candidate's target titles."""
    cv_data = cv_data or {}
    preferences = preferences or {}
    parts = [cv_data.get("raw_text") or ""]
    parts += [str(s) for s in (cv_data.get("skills") or [])]
    parts += [str(t) for t in (preferences.get("job_titles") or [])]
    return " ".join(parts).lower()


def resolve_scoring_profile(cv_data, preferences):
    """Pick the scoring profile for a candidate.

    An explicit preferences["scoring_profile"] wins. Otherwise ("auto" or
    unset) the CV is read: the profile whose signal terms appear most often
    (distinct terms) is chosen, ties going to DEFAULT_PROFILE so the original
    BFSI + AI scoring is unchanged for anyone the detector cannot tell apart.
    """
    preferences = preferences or {}
    explicit = (preferences.get("scoring_profile") or "").strip()
    if explicit in SCORING_PROFILES:
        return explicit
    text = _cv_text(cv_data, preferences)

    def hits(name):
        return sum(1 for t in profile_signal_terms(name) if term_in_text(t, text))

    best, best_hits = DEFAULT_PROFILE, hits(DEFAULT_PROFILE)
    for name in SCORING_PROFILES:
        if name != DEFAULT_PROFILE and hits(name) > best_hits:
            best, best_hits = name, hits(name)
    return best


def scoring_stamp(cv_data, preferences, outcome_model=None):
    """Identifies the scorer + profile (+ learned outcome model, once it has
    data) a user's stored scores were built with."""
    stamp = f"{SCORER_VERSION}:{resolve_scoring_profile(cv_data, preferences)}"
    fp = getattr(outcome_model, "fingerprint", "") if outcome_model else ""
    return f"{stamp}:{fp}" if fp else stamp


def _band_terms(table, cv_scoped, cv_text):
    """Terms a band scores against. A cv_scoped band keeps only the terms the
    candidate's CV mentions; if the CV mentions none (thin or unparsed CV) the
    whole table is used so the band still works."""
    terms = domain_terms_ranked(table)
    if not cv_scoped:
        return terms
    own = tuple(t for t in terms if term_in_text(t, cv_text))
    return own or terms


_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:years|yrs)\b", re.I)


def candidate_years(cv_data, preferences):
    """Candidate's years of experience: preferences["years_experience"] when
    set, else the largest "N years" in the top of the CV (where the summary
    lives), else None (the experience-floor rule then stays off)."""
    explicit = (preferences or {}).get("years_experience")
    try:
        if explicit not in (None, ""):
            return int(explicit)
    except (TypeError, ValueError):
        pass
    head = ((cv_data or {}).get("raw_text") or "")[:1500]
    years = [int(m.group(1)) for m in _YEARS_RE.finditer(head)]
    years = [y for y in years if 0 < y <= 40]
    return max(years) if years else None


def target_companies(preferences, spec):
    """The candidate's target companies (lowercase): their own list when set,
    else the profile's default short list."""
    own = [str(c).strip().lower() for c in (preferences or {}).get("target_companies") or []
           if str(c).strip()]
    return tuple(own) if own else tuple(spec.get("default_targets") or ())


def _first_term(terms, text):
    for t in sorted(terms, key=lambda t: (-len(t), t)):
        if term_in_text(t, text):
            return t
    return None


def _fit_adjustments(job, cv_data, preferences, spec, bd, role, text, negated):
    """Conversion-oriented fit rules (docs/CANDIDATE_CONTEXT.md).

    Returns (track, [[key, label, points, evidence], ...]). Each rule is
    opt-in per scoring profile (spec["fit_rules"]). Rules read the role title
    where the JD body is too noisy (seniority, engineering, mismatch) and the
    JD where it states requirements (experience, PM tenure, moat).
    """
    rules = spec.get("fit_rules") or frozenset()
    out = []

    def add(key, label, points, evidence=""):
        if key in rules and points:
            out.append([key, label, points, evidence or ""])

    track = "banking" if BANKING_TRACK_TITLE_RE.search(role) else "product"
    company = (job.get("company") or "").lower()
    targets = target_companies(preferences, spec)
    target_hit = next((t for t in targets if term_in_text(t, company)), None)

    # Penalties --------------------------------------------------------------
    m = SENIOR_TITLE_RE.search(role) or (
        track == "product" and SENIOR_PRODUCT_TITLE_RE.search(role))
    if m:
        add("seniority", "Too senior", _FIT_POINTS["seniority"], m.group(0))

    years = candidate_years(cv_data, preferences)
    try:
        exp_min = int(job.get("experience_min") or 0)
    except (TypeError, ValueError):
        exp_min = 0
    if years is not None and exp_min >= years + _EXP_FLOOR_GAP:
        add("exp_floor", "Experience floor", _FIT_POINTS["exp_floor"], f"{exp_min}+ yrs")

    if track == "product":
        for mt in PM_TENURE_RE.finditer(text):
            n = int(mt.group(1) or mt.group(2))
            if n >= _PM_TENURE_MIN and not term_in_text("product management", negated):
                add("pm_tenure", "Needs PM tenure", _FIT_POINTS["pm_tenure"], f"{n}+ yrs PM")
                break

    mismatch = _first_term(DOMAIN_MISMATCH_TERMS, role)
    if not mismatch:
        pts, mismatch_req = _band_score(
            sorted(DOMAIN_MISMATCH_TERMS, key=lambda t: (-len(t), t)), text, negated)
        mismatch = mismatch_req if pts == _BAND_REQUIRED else None
    if mismatch:
        add("domain_mismatch", "Retail / consumer risk",
            _FIT_POINTS["domain_mismatch"], mismatch)

    m = ENGINEERING_TITLE_RE.search(role)
    if m:
        add("engineering", "Engineering role", _FIT_POINTS["engineering"], m.group(0))

    if track == "product" and not target_hit and term_in_text("bank", company):
        add("bank_employer", "Bank employer (PM track)", _FIT_POINTS["bank_employer"])

    locs = [l.strip().lower() for l in preferences.get("locations", []) or []
            if l and l.strip() and l.strip().lower() != "remote"]
    job_loc = (job.get("location") or "").lower()
    remote = (job.get("remote_status") or "").lower() == "remote" or any(
        k in job_loc for k in ("remote", "anywhere", "work from home", "wfh"))
    if locs and job_loc.strip() and not remote and not any(l in job_loc for l in locs):
        add("relocation", "Relocation needed", _FIT_POINTS["relocation"],
            (job.get("location") or "")[:30])

    poster = (job.get("poster_email") or "").strip().lower()
    flag = None
    if "@" in poster and poster.rsplit("@", 1)[1] in PERSONAL_EMAIL_DOMAINS:
        flag = "personal email"
    else:
        mf = FEE_RED_FLAG_RE.search(text)
        flag = mf.group(0) if mf else None
    if flag:
        add("red_flag", "Red flag", _FIT_POINTS["red_flag"], flag)

    # Boosts -----------------------------------------------------------------
    # A wrong-shaped role (engineering, retail/consumer risk, red flag) is not
    # rescued by domain vocabulary or the company name, so it earns no boosts.
    if any(a[0] in _SHAPE_RULES for a in out):
        return track, out
    moat = [t for t in sorted(CREDIT_MOAT_TERMS, key=lambda t: (-len(t), t))
            if term_in_text(t, text) and not term_in_text(t, negated)]
    if moat:
        add("moat", "Credit moat", min(len(moat) * _MOAT_PER_TERM, _MOAT_MAX),
            ", ".join(moat[:3]))

    if bd.get("bfsi") and bd.get("ai"):
        add("ai_x_bfsi", "AI × BFSI", _FIT_POINTS["ai_x_bfsi"],
            f"{bd.get('ai_match', '')} + {bd.get('bfsi_match', '')}".strip(" +"))
    voice = _first_term(VOICE_AI_TERMS, text)
    if voice and bd.get("bfsi"):
        add("voice_ai", "Voice / conversational AI", _FIT_POINTS["voice_ai"], voice)

    if target_hit:
        add("target_company", "Target company", _FIT_POINTS["target_company"], target_hit)
    return track, out


def deterministic_score(job, cv_data, preferences):
    """Return (score_0_100, breakdown).

    Bands: title 0-30, location 0-10, cv_skills 0-20, two differentiator bands
    0-20 each, domain (generic industries) 0-5, minus a 15-point
    no_domain_penalty when the posting carries neither differentiator, plus
    'fit' (-30..+20): the profile's fit rules, listed in 'adjustments'.

    The differentiator bands come from the candidate's scoring profile:
    bfsi + ai for the BFSI/AI product profile (9 yrs banking / credit risk,
    shipped LLM products), delivery + governance for the project-delivery
    profile. A posting that merely mentions a band's vocabulary scores half
    of one that asks for it outright.

    breakdown carries each band plus an 'irrelevant' flag, the matched
    evidence term per band (<key>_match), the profile name and its band
    layout ('bands', 'penalty_label') so the UI can label it.
    """
    cv_data = cv_data or {}
    preferences = preferences or {}
    profile = resolve_scoring_profile(cv_data, preferences)
    spec = SCORING_PROFILES[profile]
    role = (job.get("role") or "").lower()
    jd = (job.get("job_description") or "").lower()
    text = f"{role} {jd}"
    bd = {"title": 0, "location": 0, "cv_skills": 0, "domain": 0,
          "no_domain_penalty": 0, "profile": profile,
          "bands": [[key, label, _BAND_REQUIRED] for key, label, _t, _s in spec["bands"]],
          "penalty_label": spec["penalty_label"]}
    for key, _label, _table, _scoped in spec["bands"]:
        bd[key] = 0

    # Irrelevant-domain hard gate — role title only (see _IRRELEVANT_RE).
    if _IRRELEVANT_RE.search(role):
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

    # Differentiator bands (0-20 each), per the profile.
    cv_text = _cv_text(cv_data, preferences)
    covered = set()
    for key, _label, table, scoped in spec["bands"]:
        bd[key], term = _band_score(_band_terms(table, scoped, cv_text), text, negated)
        if term:
            bd[f"{key}_match"] = term
        covered |= domain_terms(table)

    # Generic industry (0-5), for preferred sectors outside the profile's
    # bands (saas, ecommerce, ...). Terms already covered by a dedicated band
    # are skipped so they cannot be counted twice.
    inds = canonical_terms(preferences.get("industries", []), "industry") - covered
    dhits = sum(1 for d in inds if d and term_in_text(d, text))
    bd["domain"] = min(dhits * 5, 5)

    # Neither differentiator present -> demote, but never hard-zero: a strong
    # generalist role can still clear the threshold on its other bands.
    band_total = sum(bd[key] for key, _l, _t, _s in spec["bands"])
    if not band_total:
        bd["no_domain_penalty"] = -15

    # Fit rules (clamped to [_FIT_FLOOR, _FIT_CEIL]); each fired rule is its
    # own breakdown line.
    bd["track"], adjustments = _fit_adjustments(
        job, cv_data, preferences, spec, bd, role, text, negated)
    bd["adjustments"] = adjustments
    bd["fit"] = max(_FIT_FLOOR, min(_FIT_CEIL, sum(a[2] for a in adjustments)))

    total = (bd["title"] + bd["location"] + bd["cv_skills"] + band_total
             + bd["domain"] + bd["no_domain_penalty"] + bd["fit"])
    return max(0, min(100, total)), bd


def score_job(job, cv_data, preferences, job_vec=None, profile_vec=None,
              outcome_model=None):
    """Unified score 0-100 + breakdown, blending the deterministic component
    with embedding cosine similarity. The semantic component is skipped (0) when
    either vector is missing (deterministic-only fallback). Hard gates from the
    deterministic component (irrelevant domain) override the blend.

    outcome_model (outcome_model.OutcomeModel) adds the candidate's learned
    track record as a bounded line in 'adjustments'; it contributes 0 until the
    candidate has outcomes to learn from."""
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
    learned = 0
    if outcome_model is not None:
        for line in outcome_model.adjust(job, bd):
            bd["adjustments"].append(line)
            learned += line[2]
    blended = max(0, min(100, blended + learned))
    bd.update({"deterministic": round(det), "semantic": round(sem),
               "learned": learned, "blended": blended})
    return blended, bd
