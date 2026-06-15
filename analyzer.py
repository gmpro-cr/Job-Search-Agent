"""
analyzer.py - Job analysis and scoring using Ollama (mistral) with keyword-based fallback.
Scores each job 0-100 based on relevance to user preferences.
"""

import logging
import os
import re
import json
import scorer

logger = logging.getLogger(__name__)


# =============================================================================
# Keyword-based scoring (fallback when Ollama is unavailable)
# =============================================================================

REMOTE_KEYWORDS = {
    "remote": 10,
    "work from home": 10,
    "wfh": 10,
    "flexible": 5,
    "hybrid": 7,
    "work from anywhere": 10,
}

ONSITE_KEYWORDS = {"on-site": 0, "onsite": 0, "office": 0, "in-office": 0}

FINTECH_KEYWORDS = {
    "fintech": 15,
    "banking": 12,
    "credit": 10,
    "payments": 12,
    "lending": 12,
    "upi": 10,
    "neobank": 15,
    "financial services": 10,
    "nbfc": 12,
    "saas": 8,
    "insurance": 8,
    "wealth management": 8,
    "defi": 6,
    "blockchain": 5,
    "crypto": 5,
}

PM_KEYWORDS = {
    "product manager": 20,
    "product management": 18,
    "product lead": 18,
    "associate product manager": 20,
    "apm": 15,
    "product owner": 15,
    "product strategy": 15,
    "product roadmap": 12,
    "user stories": 8,
    "agile": 3,
    "scrum": 3,
    "sprint": 3,
    "stakeholder": 3,
}

STARTUP_KEYWORDS = [
    "startup", "early stage", "series a", "series b", "seed",
    "pre-seed", "founded in", "co-founder", "founding team",
    "fast-paced", "0 to 1", "greenfield",
]

CORPORATE_KEYWORDS = [
    "fortune 500", "mnc", "established", "global leader",
    "publicly traded", "enterprise", "large scale",
]

GROWTH_KEYWORDS = {
    "leadership": 3,
    "mentorship": 3,
    "career growth": 5,
    "learning": 2,
    "promotion": 3,
    "impact": 3,
    "ownership": 5,
    "autonomy": 3,
    "cross-functional": 3,
}

# Negative signals: roles that match "product manager" or "project manager" keywords
# but are clearly in unrelated domains
IRRELEVANT_KEYWORDS = [
    "sheet pile", "construction", "civil engineer", "mechanical engineer",
    "electrical engineer", "lab equipment", "laboratory", "chemical",
    "clinical", "pharmaceutical", "oil and gas", "oil & gas", "mining",
    "real estate agent", "property dealer", "interior design",
    "garment", "textile", "apparel", "food processing",
    "hvac", "plumbing", "welding", "carpentry",
]

# Seniority overqualification — split into title-level patterns (role only)
# and years-based patterns (full text, but context-anchored).

# Title-level seniority — only match in the job ROLE/TITLE, not the description body
_OVERQUALIFIED_TITLE_RE = re.compile(
    r'\b(?:vp|vice\s+president|chief\s+\w+\s+officer|cxo|c-suite'
    r'|managing\s+director|president)\b',
    re.IGNORECASE,
)

# Director alone is only overqualified when it's in the role title
_DIRECTOR_TITLE_RE = re.compile(r'\bdirector\b', re.IGNORECASE)

# Experience-requirement overqualification — safe to check in full text (context-anchored)
_OVERQUALIFIED_EXP_RE = re.compile(
    r'(?:minimum|required?|must\s+have|at\s+least|needs?\s+)\s*(?:15|18|20)\s*\+?\s*(?:years?|yrs?)',
    re.IGNORECASE,
)

# Underqualified/irrelevant role patterns
_WRONG_LEVEL_PATTERNS = [
    r'\bfreshers?\s+only\b',
    r'\b0[\s-]1\s*(?:year|yr)\b',
    r'\binternship\s+(?:only|position|role|opportunity)\b',
]
_WRONG_LEVEL_RE = re.compile('|'.join(_WRONG_LEVEL_PATTERNS), re.IGNORECASE)

# Seniority-overreach penalty split into two targeted regexes:
# 1. Title patterns — must only be matched against the role/title string, NOT the JD body,
#    to avoid false penalties when the JD mentions "reports to VP" or "works with Director".
_SENIORITY_TITLE_ONLY_RE = re.compile(
    r'\b(?:vp|vice\s+president|chief\s+\w+\s+officer|cxo|director|managing\s+director)\b',
    re.IGNORECASE,
)
# 2. Years-of-experience patterns — context-anchored, safe to match against full text.
_SENIORITY_EXP_RE = re.compile(
    r'(?:(?:minimum|required?|must\s+have|at\s+least|needs?\s+)\s*(?:10|12)\s*\+?\s*(?:years?|yrs?)'
    r'|\b(?:10|12)\s*\+\s*(?:years?|yrs?)\b)',
    re.IGNORECASE,
)


def quality_gate(job):
    """
    Pre-filter a job before scoring.
    Returns (passed: bool, reason: str).
    'passed=False' means the job should be skipped entirely.
    """
    desc = job.get("job_description") or ""
    role = job.get("role") or ""
    combined = f"{role} {desc}"

    # Check seniority titles only against the role field (not JD body)
    if _OVERQUALIFIED_TITLE_RE.search(role) or _DIRECTOR_TITLE_RE.search(role):
        return False, "overqualified_level"

    # Check years-based overqualification against full text (pattern is context-anchored)
    if _OVERQUALIFIED_EXP_RE.search(combined):
        return False, "overqualified_level"

    # Reject fresher-only / intern-only postings
    if _WRONG_LEVEL_RE.search(combined):
        return False, "wrong_level"

    # Reject thin descriptions (< 40 words)
    word_count = len(desc.split())
    if 0 < word_count < 40:
        return False, f"thin_description:{word_count}_words"

    return True, ""


# =============================================================================
# Experience & Salary extraction
# =============================================================================

def extract_experience_years(text):
    """
    Extract experience range from job text.
    Returns (min_years, max_years) or (None, None) if not found.
    Examples: "5-10 years", "3+ years", "minimum 5 years", "Senior" title inference.
    """
    if not text:
        return None, None
    text_lower = text.lower()

    # Pattern: "5-10 years", "5 - 10 yrs"
    m = re.search(r'(\d{1,2})\s*[-–to]+\s*(\d{1,2})\s*(?:years?|yrs?)', text_lower)
    if m:
        return int(m.group(1)), int(m.group(2))

    # Pattern: "3+ years", "3 plus years"
    m = re.search(r'(\d{1,2})\s*\+?\s*(?:plus\s+)?(?:years?|yrs?)', text_lower)
    if m:
        val = int(m.group(1))
        return val, val + 5

    # Pattern: "minimum 5 years", "at least 5 years"
    m = re.search(r'(?:minimum|at\s+least|min)\s*(\d{1,2})\s*(?:years?|yrs?)', text_lower)
    if m:
        val = int(m.group(1))
        return val, val + 5

    # Title-based inference
    title_experience = {
        "intern": (0, 1),
        "fresher": (0, 2),
        "junior": (0, 3),
        "associate": (1, 4),
        "mid": (3, 7),
        "senior": (5, 12),
        "staff": (7, 15),
        "lead": (7, 15),
        "principal": (10, 20),
        "director": (10, 20),
        "head": (10, 20),
        "vp": (12, 25),
    }
    for keyword, (lo, hi) in title_experience.items():
        if keyword in text_lower.split()[:10]:  # Check title area only
            return lo, hi

    return None, None


def parse_salary_to_annual_inr(text, currency=None):
    """
    Parse salary text to (min_annual_inr, max_annual_inr).
    Handles: "INR 10-20 Lacs PA", "10-15 LPA", "$100k-$150k", "50,000/month"
    Returns (None, None) if unparseable.
    """
    if not text:
        return None, None

    text_lower = text.lower().replace(",", "").replace("₹", "").strip()

    # Detect currency
    is_usd = currency == "USD" or "$" in text or "usd" in text_lower
    multiplier = 83 if is_usd else 1  # Approximate USD to INR

    # Extract numbers
    numbers = re.findall(r'(\d+(?:\.\d+)?)', text_lower)
    if not numbers:
        return None, None

    nums = [float(n) for n in numbers[:2]]

    # Determine scale
    is_monthly = "month" in text_lower or "/m" in text_lower or "per month" in text_lower
    is_lakh = "lac" in text_lower or "lpa" in text_lower or "lakh" in text_lower or "l " in text_lower
    is_k = "k" in text_lower and not is_lakh
    is_crore = "cr" in text_lower or "crore" in text_lower

    scale = 1
    if is_crore:
        scale = 10_000_000
    elif is_lakh:
        scale = 100_000
    elif is_k:
        scale = 1_000

    results = [n * scale * multiplier for n in nums]
    if is_monthly:
        results = [r * 12 for r in results]

    # Return as integers (annual INR)
    if len(results) >= 2:
        return int(min(results)), int(max(results))
    elif len(results) == 1:
        return int(results[0]), int(results[0])
    return None, None


def extract_company_info(text):
    """
    Extract company size, funding stage, and glassdoor rating hints from JD text.
    Returns dict with keys: company_size, company_funding_stage, company_glassdoor_rating
    """
    if not text:
        return {}
    text_lower = text.lower()
    info = {}

    # Funding stage detection
    funding_patterns = {
        "Pre-Seed": ["pre-seed", "pre seed"],
        "Seed": ["seed stage", "seed funded", "seed round"],
        "Series A": ["series a"],
        "Series B": ["series b"],
        "Series C": ["series c"],
        "Series D+": ["series d", "series e", "series f"],
        "IPO/Public": ["publicly traded", "listed on", "ipo", "nasdaq", "nyse", "bse", "nse listed"],
        "Bootstrapped": ["bootstrapped", "self-funded", "profitable startup"],
    }
    for stage, patterns in funding_patterns.items():
        if any(p in text_lower for p in patterns):
            info["company_funding_stage"] = stage
            break

    # Company size
    size_patterns = [
        (r'(\d[\d,]*)\s*\+?\s*employees', None),
        (r'team\s+of\s+(\d[\d,]*)', None),
    ]
    for pattern, _ in size_patterns:
        m = re.search(pattern, text_lower)
        if m:
            count = int(m.group(1).replace(",", ""))
            if count < 50:
                info["company_size"] = "Startup (<50)"
            elif count < 200:
                info["company_size"] = "Small (50-200)"
            elif count < 1000:
                info["company_size"] = "Mid-size (200-1K)"
            elif count < 10000:
                info["company_size"] = "Large (1K-10K)"
            else:
                info["company_size"] = "Enterprise (10K+)"
            break

    # Size from keywords if not found
    if "company_size" not in info:
        if any(kw in text_lower for kw in ["startup", "early stage", "small team", "founding"]):
            info["company_size"] = "Startup (<50)"
        elif any(kw in text_lower for kw in ["fortune 500", "mnc", "global leader", "enterprise"]):
            info["company_size"] = "Enterprise (10K+)"

    # Glassdoor rating mention
    m = re.search(r'glassdoor\s*(?:rating)?[:\s]*(\d(?:\.\d)?)', text_lower)
    if m:
        info["company_glassdoor_rating"] = m.group(1)

    return info


def detect_remote_status(text):
    """Detect whether a job is remote, hybrid, or on-site."""
    text_lower = text.lower()
    if any(kw in text_lower for kw in ["remote", "work from home", "wfh", "work from anywhere"]):
        return "remote"
    if "hybrid" in text_lower:
        return "hybrid"
    return "on-site"


def detect_company_type(text):
    """Detect whether a company is a startup or corporate."""
    text_lower = text.lower()
    startup_score = sum(1 for kw in STARTUP_KEYWORDS if kw in text_lower)
    corporate_score = sum(1 for kw in CORPORATE_KEYWORDS if kw in text_lower)
    if startup_score > corporate_score:
        return "startup"
    if corporate_score > startup_score:
        return "corporate"
    return "corporate"


def extract_skills(text, max_skills=8):
    """Extract key skills from job description text."""
    found = []
    for pattern, display in _SKILL_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE) and display not in found:
            found.append(display)
    return found[:max_skills] if max_skills else found


def keyword_score(job, preferences, cv_data=None, breakdown=False):
    """
    Score a job 0-100 using keyword matching against the user's own profile.

    Scoring breakdown:
      - Title match:        0-30  (user's preferred job titles vs role)
      - Location match:     0-10  (user's preferred locations)
      - CV skill match:     0-30  (user's CV skills + transferable skills in JD)
      - Penalty:            -20   (irrelevant domain detected)
      - Seniority penalty:  -15   (10+ yr / VP / Director requirement detected)
    """
    score = 0
    role_lower = job.get("role", "").lower()
    text = " ".join([
        role_lower,
        job.get("company", ""),
        job.get("job_description", ""),
        job.get("location", ""),
        job.get("salary", "") or "",
    ]).lower()

    # --- Irrelevance penalty: bail early for obviously wrong domains ---
    for kw in IRRELEVANT_KEYWORDS:
        if kw in text:
            if breakdown:
                return {"total": 0, "irrelevant": True}
            return max(0, score - 20)

    # Title match (0-30) — strongest signal
    user_titles = [t.lower().strip() for t in preferences.get("job_titles", [])]
    best_title_score = 0
    for title in user_titles:
        if title in role_lower:
            best_title_score = max(best_title_score, 30)
        else:
            title_words = [w for w in title.split() if len(w) > 2]
            if title_words:
                matches = sum(1 for w in title_words if w in role_lower)
                ratio = matches / len(title_words)
                if ratio >= 0.8:
                    best_title_score = max(best_title_score, 22)
                elif ratio >= 0.5:
                    best_title_score = max(best_title_score, 12)
    score += best_title_score

    # Location match (0-10)
    job_loc = job.get("location", "").lower()
    remote_status = job.get("remote_status", "").lower()
    pref_locs = {l.lower().strip() for l in preferences.get("locations", []) if l.strip()}
    pref_locs.update({"remote", "hybrid", "wfh", "work from home", "work from anywhere"})
    location_score = 0
    if any(kw in job_loc or kw in remote_status or kw in text for kw in pref_locs):
        location_score = 10
    score += location_score

    # CV skill match in JD (0-30) — fully driven by the user's own skills/CV
    cv_skills = (cv_data or {}).get("skills") or []
    pref_skills = preferences.get("transferable_skills") or []
    all_skills = list({s.lower() for s in cv_skills + pref_skills if s})
    ts_score = 0
    if all_skills:
        for skill in all_skills:
            if skill in text:
                ts_score += 4
        score += min(ts_score, 30)

    # Seniority penalty (-15): title patterns checked against role only (not full JD body),
    # experience-year patterns checked against full text (they are context-anchored).
    seniority_penalty = -15 if (
        _SENIORITY_TITLE_ONLY_RE.search(role_lower) or _SENIORITY_EXP_RE.search(text)
    ) else 0
    score = max(0, score + seniority_penalty)

    if breakdown:
        return {
            "total": min(score, 100),
            "title": best_title_score,
            "location": location_score,
            "cv_skills": min(ts_score, 30) if all_skills else 0,
            "seniority_penalty": seniority_penalty,
        }
    return min(score, 100)


# =============================================================================
# LLM-based scoring (Ollama → OpenRouter fallback via agent/llm.py)
# =============================================================================

_SCORING_PROMPT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "agent", "scoring_prompt.md"
)
_scoring_prompt_cache: str | None = None


def _load_scoring_prompt() -> str:
    global _scoring_prompt_cache
    if _scoring_prompt_cache is None:
        try:
            with open(_SCORING_PROMPT_PATH, "r", encoding="utf-8") as f:
                _scoring_prompt_cache = f.read()
        except FileNotFoundError:
            # Minimal fallback — should never happen in normal operation
            _scoring_prompt_cache = (
                'Score this job for the candidate 0-100.\n'
                'Role: {role}\nCompany: {company}\nDescription: {jd}\n'
                'Skills: {cv_skills}\nBackground: {cv_summary}\n'
                'Return JSON: {"score": <int>, "reason": "<str>"}'
            )
    return _scoring_prompt_cache


def _build_scoring_rubric(cv_data: dict, preferences: dict) -> dict:
    """
    Build dynamic rubric sections from the user's CV data and preferences.
    Returns dict with keys: target_roles_rubric, domain_rubric.
    """
    # Role rubric from user's preferred job titles
    titles = [t.strip() for t in (preferences.get("job_titles") or []) if t.strip()]
    if titles:
        primary = titles[0]
        variants = ", ".join(titles[1:4]) if len(titles) > 1 else f"Senior/Lead {primary}"
        role_rubric = (
            f"   35 — Exact match: {primary}\n"
            f"   25 — Close variant: {variants}\n"
            f"   12 — Adjacent: related function with strong skill overlap\n"
            f"    4 — Wrong function but partial skill overlap\n"
            f"    0 — Unrelated function"
        )
    else:
        role_rubric = (
            "   35 — Exact title match to candidate's target role\n"
            "   25 — Close adjacent role with same core function\n"
            "   12 — Related role with meaningful skill overlap\n"
            "    4 — Wrong function but some transferable skills\n"
            "    0 — Completely unrelated function"
        )

    # Domain rubric: detect user's primary domain from skills + raw CV text
    skills_lower = [s.lower() for s in (cv_data.get("skills") or [])]
    raw_lower = (cv_data.get("raw_text") or "").lower()

    _DOMAIN_SIGNALS = [
        ("Fintech / Banking / Payments / Lending / NBFC / Credit / Insurance",
         ["fintech", "banking", "payments", "lending", "nbfc", "credit", "upi",
          "insurance", "wealth", "neobank", "treasury"]),
        ("B2B SaaS / AI / ML / Data platform",
         ["saas", "machine learning", "llm", "genai", "data platform", "b2b",
          "api", "microservices"]),
        ("E-commerce / Edtech / Healthtech / Consumer tech",
         ["e-commerce", "ecommerce", "edtech", "healthtech", "consumer", "marketplace"]),
        ("Manufacturing / FMCG / Logistics / Operations",
         ["manufacturing", "fmcg", "logistics", "supply chain", "operations", "procurement"]),
    ]

    domain_scores = []
    for domain, keywords in _DOMAIN_SIGNALS:
        count = sum(1 for kw in keywords if kw in skills_lower or kw in raw_lower)
        domain_scores.append((count, domain))
    domain_scores.sort(reverse=True)

    top_count, top_domain = domain_scores[0]
    if top_count >= 2:
        others = [d for _, d in domain_scores[1:]]
        domain_rubric = (
            f"   25 — {top_domain}\n"
            f"   18 — {others[0]}\n"
            f"   10 — {others[1]}\n"
            f"    4 — {others[2]}\n"
            f"    0 — Completely unrelated domain"
        )
    else:
        domain_rubric = (
            "   25 — Domain matches candidate's primary background closely\n"
            "   18 — Related domain with transferable knowledge\n"
            "   10 — Adjacent domain, some overlap\n"
            "    4 — Distant domain, minimal overlap\n"
            "    0 — Completely unrelated domain"
        )

    return {"target_roles_rubric": role_rubric, "domain_rubric": domain_rubric}


def llm_score(job: dict, cv_data: dict, preferences: dict = None) -> dict | None:
    """
    Score a job using the shared scoring_prompt.md via call_llm_json.
    Tries Ollama first, falls back to OpenRouter automatically.
    Returns dict with score, reason, remote_status, company_type — or None on failure.
    """
    try:
        from agent.llm import call_llm_json
    except ImportError:
        logger.warning("agent.llm not importable, falling back to keyword scoring")
        return None

    cv_skills = ", ".join((cv_data.get("skills") or [])[:25])
    cv_summary = (cv_data.get("raw_text") or "")[:400]

    if not cv_skills:
        logger.warning("No CV skills found — LLM scoring will be generic")

    template = _load_scoring_prompt()
    role = job.get("role", "")
    company = job.get("company", "")
    jd = (job.get("job_description") or "")[:2000]

    rubric = _build_scoring_rubric(cv_data, preferences or {})
    substitutions = [
        ("role", role), ("company", company), ("jd", jd),
        ("cv_skills", cv_skills), ("cv_summary", cv_summary),
        ("target_roles_rubric", rubric["target_roles_rubric"]),
        ("domain_rubric", rubric["domain_rubric"]),
    ]
    prompt = template
    for key, val in substitutions:
        prompt = prompt.replace("{" + key + "}", str(val))

    try:
        result = call_llm_json(prompt)
        if not result or "score" not in result:
            logger.warning("LLM returned no score for %s @ %s", role, company)
            return None
        score = max(0, min(100, int(result["score"])))
        return {
            "score": score,
            "reason": result.get("reason", ""),
            "remote_status": None,
            "company_type": None,
        }
    except Exception as e:
        logger.warning("LLM scoring failed for %s @ %s: %s", role, company, e)
        return None


# =============================================================================
# Main analysis pipeline
# =============================================================================

def generate_application_email(job, preferences, cv_data=None):
    """Generate a short personalized application email draft."""
    role = job.get("role", "the role")
    company = job.get("company", "your company")
    description = job.get("job_description", "")

    skills = extract_skills(description, max_skills=3)
    skills_text = ", ".join(skills) if skills else "the required skills"

    # Derive background descriptor from CV summary or preferences
    target_titles = [t.strip() for t in (preferences.get("job_titles") or []) if t.strip()]
    background = _user_background(cv_data, target_titles)

    email = (
        f"Dear Hiring Team at {company},\n\n"
        f"I am writing to express my interest in the {role} position. "
        f"With my background as {background}, I bring a strong foundation in "
        f"analytical thinking, stakeholder management, and problem solving. "
        f"My experience with {skills_text} aligns well with this role's requirements. "
        f"I am excited about the opportunity to contribute at {company}.\n\n"
        f"I would welcome the chance to discuss how my skills can add value to your team.\n\n"
        f"Best regards"
    )
    return email


def _user_background(cv_data, target_titles=None):
    """Return a short background descriptor derived from the user's CV and preferences."""
    # Try to extract a headline from the CV's raw text (second non-empty line)
    raw = (cv_data or {}).get("raw_text", "") or ""
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    if len(lines) >= 2:
        headline = lines[1]
        # Strip contact fragments
        headline = re.sub(
            r'[\|,]?\s*(\+?\d[\d\s\-]{7,}|[\w.]+@[\w.]+|\blinkedin\b.*|pune|bangalore|mumbai|india)\s*',
            '', headline, flags=re.IGNORECASE,
        ).strip().strip('|').strip()
        if len(headline) > 10:
            return headline
    if target_titles:
        return target_titles[0]
    return "an experienced professional"


def generate_tailored_points(job, preferences, config, cv_data=None):
    """
    Generate tailored resume/cover-letter bullet points for a specific job.
    Uses Ollama if available, otherwise keyword-based fallback.
    """
    role = job.get("role", "the role")
    company = job.get("company", "the company")
    description = job.get("job_description", "")
    transferable = preferences.get("transferable_skills", [])
    skills = extract_skills(description, max_skills=5)
    target_titles = [t.strip() for t in (preferences.get("job_titles") or []) if t.strip()]
    background = _user_background(cv_data, target_titles)

    # Try Ollama first
    use_ollama = config.get("scoring", {}).get("use_ollama", True)
    if use_ollama:
        try:
            import ollama as ollama_client
            model = config.get("scoring", {}).get("ollama_model", "mistral")

            prompt = f"""Generate 4-5 tailored resume bullet points for this candidate applying to a job.

Candidate background: {background}
Target role: {role} at {company}
Key skills needed: {', '.join(skills) if skills else 'see job description'}
Candidate's transferable skills: {', '.join(transferable) if transferable else 'stakeholder management, data analysis, problem solving'}
Job Description: {description[:600]}

Write bullet points that:
1. Map the candidate's background to the role requirements
2. Use specific, quantifiable achievements
3. Highlight transferable skills
4. Show domain knowledge advantage

Respond with ONLY a JSON array of strings, like: ["point 1", "point 2", ...]"""

            response = ollama_client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.3},
            )
            content = response["message"]["content"].strip()
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                points = json.loads(json_match.group())
                if isinstance(points, list) and len(points) > 0:
                    return points
        except Exception:
            pass  # Fall through to keyword-based

    # Keyword-based fallback — generic skill templates
    points = []
    skill_map = {
        "stakeholder management": f"Led cross-functional stakeholder alignment across multiple departments, directly applicable to {role} coordination needs at {company}",
        "risk management": f"Built risk assessment frameworks processing high-volume decisions, transferable to risk evaluation requirements at {company}",
        "data analysis": f"Analyzed large-scale datasets to drive business decisions, relevant to data-driven work at {company}",
        "regulatory compliance": f"Navigated complex regulatory requirements, an advantage for {company}'s compliance-sensitive environment",
        "p&l ownership": f"Managed P&L with direct revenue impact, applicable to ownership metrics required for {role} at {company}",
        "process optimization": f"Optimized workflows reducing processing time by 30%, bringing operational efficiency mindset to {role}",
        "cross-functional leadership": f"Led cross-functional teams of 10+ across complex projects, relevant to collaboration at {company}",
        "client relationship management": f"Managed relationships with enterprise clients, bringing customer-centric approach to {role} at {company}",
        "project management": f"Delivered end-to-end projects on time and within scope, directly applicable to {role} execution at {company}",
        "business development": f"Drove business growth through strategic partnerships, relevant to growth objectives at {company}",
    }
    for skill in transferable:
        key = skill.lower()
        if key in skill_map:
            points.append(skill_map[key])
    if not points:
        points = [
            f"Leverage domain expertise as {background} to bring a unique perspective to {role} at {company}",
            f"Apply analytical rigour and problem-solving skills to drive measurable impact at {company}",
            f"Bring stakeholder management and cross-functional collaboration experience to {role}",
            f"Translate deep understanding of customer needs into effective solutions at {company}",
        ]
    return points[:5]


def analyze_jobs(jobs, preferences, config, progress_callback=None, cv_data=None,
                 profile_vec=None):
    """
    Analyze and score all jobs with the deterministic + embedding scorer
    (scorer.score_job) — no LLM. CV data drives scoring for accuracy.
    Returns list of jobs enriched with relevance_score, remote_status,
    company_type, skills, and application_email.

    cv_data: optional pre-loaded CV dict. When None, falls back to load_cv_data()
             (JSON file / session user). Pass explicitly from the scraper so the
             multi-user DB CV is used instead of the empty JSON-file path.
    profile_vec: optional embedding of the user's profile. When provided along
             with a per-job vector (job["_vec"]), the semantic component is
             blended in; otherwise scoring is deterministic-only.
    """
    min_score = config.get("scoring", {}).get("min_relevance_score", 65)

    # Load CV once for the whole batch (caller may supply it directly)
    if cv_data is None:
        cv_data = load_cv_data() or {}
    if not cv_data.get("skills"):
        logger.warning("No CV uploaded — scoring on titles/location/domain only. Upload CV at /cv for better accuracy.")

    analyzed = []
    total = len(jobs)

    for i, job in enumerate(jobs):
        passed, gate_reason = quality_gate(job)
        if not passed:
            logger.debug("Quality gate rejected %s @ %s: %s",
                         job.get("role"), job.get("company"), gate_reason)
            if progress_callback:
                progress_callback(i + 1, total, job.get("role", ""), 0)
            continue

        text = " ".join([
            job.get("role", ""),
            job.get("job_description", ""),
            job.get("location", ""),
        ])

        # Deterministic + (optional) semantic scoring — no LLM, no quota.
        score, breakdown = scorer.score_job(
            job, cv_data, preferences,
            job_vec=job.get("_vec"), profile_vec=profile_vec,
        )
        job["relevance_score"] = score
        job["score_breakdown"] = json.dumps(breakdown)
        job["remote_status"] = detect_remote_status(text)
        job["company_type"] = detect_company_type(text)

        # Extract skills and generate email for all jobs
        job["skills"] = extract_skills(job.get("job_description", ""))
        job["application_email"] = generate_application_email(job, preferences)

        # Extract experience range
        exp_text = " ".join([job.get("role", ""), job.get("job_description", "")])
        exp_min, exp_max = extract_experience_years(exp_text)
        job["experience_min"] = exp_min
        job["experience_max"] = exp_max

        # Parse salary to annual INR
        salary_min, salary_max = parse_salary_to_annual_inr(
            job.get("salary", ""), job.get("salary_currency")
        )
        job["salary_min"] = salary_min
        job["salary_max"] = salary_max

        # Extract company info from JD
        company_info = extract_company_info(job.get("job_description", ""))
        job["company_size"] = company_info.get("company_size")
        job["company_funding_stage"] = company_info.get("company_funding_stage")
        job["company_glassdoor_rating"] = company_info.get("company_glassdoor_rating")

        analyzed.append(job)

        if progress_callback:
            progress_callback(i + 1, total, job.get("role", ""), job["relevance_score"])

    # Filter by minimum score
    qualified = [j for j in analyzed if j["relevance_score"] >= min_score]

    # Sort by relevance score descending
    qualified.sort(key=lambda x: x["relevance_score"], reverse=True)

    logger.info(
        "Analysis complete: %d/%d jobs passed minimum score of %d",
        len(qualified), total, min_score,
    )

    return qualified, analyzed


# =============================================================================
# NLP query parsing for conversational search
# =============================================================================

# City names for regex fallback (canonical → trigger words)
_NLP_CITY_TRIGGERS = {
    "Bengaluru": ["bangalore", "bengaluru", "blr"],
    "Mumbai": ["mumbai", "bombay"],
    "Delhi / NCR": ["delhi", "ncr", "noida", "gurgaon", "gurugram"],
    "Hyderabad": ["hyderabad"],
    "Chennai": ["chennai"],
    "Pune": ["pune"],
    "Kolkata": ["kolkata", "calcutta"],
    "Ahmedabad": ["ahmedabad"],
    "Jaipur": ["jaipur"],
    "Kochi": ["kochi", "cochin"],
    "Chandigarh": ["chandigarh"],
    "Indore": ["indore"],
    "Coimbatore": ["coimbatore"],
    "Singapore": ["singapore"],
    "Dubai / UAE": ["dubai", "uae"],
    "London": ["london"],
    "US - Remote": ["usa", "united states"],
    "Remote": ["remote"],
}


def _regex_parse_nlp_query(text):
    """Regex-based fallback for parsing natural language job queries."""
    filters = {}
    remaining = text.lower()

    # Remote / WFH / Hybrid / On-site
    if re.search(r'\b(remote|wfh|work\s*from\s*home)\b', remaining):
        filters["remote"] = "remote"
        remaining = re.sub(r'\b(remote|wfh|work\s*from\s*home)\b', '', remaining)
    elif re.search(r'\bhybrid\b', remaining):
        filters["remote"] = "hybrid"
        remaining = re.sub(r'\bhybrid\b', '', remaining)
    elif re.search(r'\b(on[\s-]?site|office)\b', remaining):
        filters["remote"] = "on-site"
        remaining = re.sub(r'\b(on[\s-]?site|office)\b', '', remaining)

    # Location (check city triggers)
    for canonical, triggers in _NLP_CITY_TRIGGERS.items():
        for trigger in triggers:
            pattern = r'\b' + re.escape(trigger) + r'\b'
            if re.search(pattern, remaining):
                filters["location"] = canonical
                remaining = re.sub(pattern, '', remaining)
                break
        if "location" in filters:
            break

    # Salary: "above/more than/over/minimum X lakhs/lpa/L"
    sal_min_match = re.search(
        r'\b(?:above|over|more\s*than|minimum|min|>=?)\s*(\d+)\s*(?:lakhs?|lpa|l|lakh)\b',
        remaining,
    )
    if sal_min_match:
        filters["salary_min"] = sal_min_match.group(1)
        remaining = remaining[:sal_min_match.start()] + remaining[sal_min_match.end():]

    # Salary: "below/under/less than/maximum X lakhs"
    sal_max_match = re.search(
        r'\b(?:below|under|less\s*than|maximum|max|<=?)\s*(\d+)\s*(?:lakhs?|lpa|l|lakh)\b',
        remaining,
    )
    if sal_max_match:
        filters["salary_max"] = sal_max_match.group(1)
        remaining = remaining[:sal_max_match.start()] + remaining[sal_max_match.end():]

    # Salary range: "X-Y lakhs"
    sal_range_match = re.search(
        r'\b(\d+)\s*[-to]+\s*(\d+)\s*(?:lakhs?|lpa|l|lakh)\b', remaining,
    )
    if sal_range_match and "salary_min" not in filters:
        filters["salary_min"] = sal_range_match.group(1)
        filters["salary_max"] = sal_range_match.group(2)
        remaining = remaining[:sal_range_match.start()] + remaining[sal_range_match.end():]

    # Experience: "X-Y years" or "X+ years"
    exp_match = re.search(r'\b(\d+)\s*[-to]+\s*(\d+)\s*(?:years?|yrs?)\b', remaining)
    if exp_match:
        lo, hi = int(exp_match.group(1)), int(exp_match.group(2))
        if lo <= 3 and hi <= 3:
            filters["experience"] = "0-3"
        elif lo <= 7 and hi <= 7:
            filters["experience"] = "3-7"
        elif lo <= 12 and hi <= 12:
            filters["experience"] = "7-12"
        else:
            filters["experience"] = "12+"
        remaining = remaining[:exp_match.start()] + remaining[exp_match.end():]
    else:
        exp_plus_match = re.search(r'\b(\d+)\+?\s*(?:years?|yrs?)\b', remaining)
        if exp_plus_match:
            yrs = int(exp_plus_match.group(1))
            if yrs <= 3:
                filters["experience"] = "0-3"
            elif yrs <= 7:
                filters["experience"] = "3-7"
            elif yrs <= 12:
                filters["experience"] = "7-12"
            else:
                filters["experience"] = "12+"
            remaining = remaining[:exp_plus_match.start()] + remaining[exp_plus_match.end():]

    # Seniority keywords → experience
    if "experience" not in filters:
        if re.search(r'\b(entry[\s-]?level|fresher|junior)\b', remaining):
            filters["experience"] = "0-3"
            remaining = re.sub(r'\b(entry[\s-]?level|fresher|junior)\b', '', remaining)
        elif re.search(r'\b(senior|lead|principal|staff)\b', remaining):
            filters["experience"] = "7-12"
            remaining = re.sub(r'\b(senior|lead|principal|staff)\b', '', remaining)

    # Company type
    if re.search(r'\bstartup\b', remaining):
        filters["company_type"] = "startup"
        remaining = re.sub(r'\bstartup\b', '', remaining)
    elif re.search(r'\b(corporate|mnc|enterprise)\b', remaining):
        filters["company_type"] = "corporate"
        remaining = re.sub(r'\b(corporate|mnc|enterprise)\b', '', remaining)

    # Sort preference
    if re.search(r'\b(newest|latest|recent)\b', remaining):
        filters["sort"] = "date_desc"
        remaining = re.sub(r'\b(newest|latest|recent)\b', '', remaining)
    elif re.search(r'\b(highest\s*score|best\s*match)\b', remaining):
        filters["sort"] = "score_desc"
        remaining = re.sub(r'\b(highest\s*score|best\s*match)\b', '', remaining)

    # Application status
    if re.search(r"\b(haven'?t applied|not applied|unapplied|new)\b", remaining):
        filters["applied"] = "none"
        remaining = re.sub(r"\b(haven'?t applied|not applied|unapplied)\b", '', remaining)

    # Clean up remaining text as search query
    # Remove filler words
    remaining = re.sub(
        r'\b(show|me|find|get|search|for|in|with|at|the|a|an|and|or|jobs?|roles?|positions?|openings?|opportunities?|i|want|need|looking)\b',
        '', remaining,
    )
    remaining = re.sub(r'\s+', ' ', remaining).strip()

    if remaining:
        filters["search"] = remaining

    return filters


_NLP_VALID_KEYS = {
    "search", "location", "remote", "salary_min", "salary_max",
    "experience", "company_type", "sort", "portal", "applied",
    "min_score",
}

_NLP_EXTRACTION_PROMPT = """Extract structured job search filters from this natural language query.

Query: "{text}"

Extract any of these fields that are mentioned or implied:
- search: job title or role keywords (e.g. "product manager", "software engineer")
- location: city name (use canonical Indian city names like Bengaluru, Mumbai, Delhi / NCR, Hyderabad, Chennai, Pune)
- remote: one of "remote", "hybrid", or "on-site"
- salary_min: minimum salary in lakhs (number only, e.g. 20 for "above 20 lakhs")
- salary_max: maximum salary in lakhs (number only)
- experience: one of "0-3", "3-7", "7-12", "12+"
- company_type: one of "startup" or "corporate"
- sort: one of "score_desc", "date_desc", "date_asc", "company_asc"
- applied: one of "none" (not applied), "applied", "saved", "interview"

Only include fields that are clearly mentioned or strongly implied. Do not guess.

Respond ONLY with valid JSON. Example:
{{"search": "product manager", "location": "Bengaluru", "remote": "remote", "salary_min": "20"}}"""


def _sanitize_nlp_filters(raw_filters):
    """Keep only known keys with non-empty string values."""
    return {
        k: str(v) for k, v in raw_filters.items()
        if k in _NLP_VALID_KEYS and v is not None and str(v).strip()
    }


def _openrouter_parse_nlp_query(text):
    """
    Use OpenRouter (meta-llama/llama-3.1-8b-instruct:free) to parse a
    natural language query into structured job search filters.
    Returns a dict of filters, or None if OpenRouter is unavailable/fails.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        from openai import OpenAI
    except ImportError:
        logger.info("openai package not installed, skipping OpenRouter NLP")
        return None

    try:
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )

        prompt = _NLP_EXTRACTION_PROMPT.format(text=text)
        response = client.chat.completions.create(
            model="meta-llama/llama-3.1-8b-instruct:free",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        content = response.choices[0].message.content.strip()

        # Extract JSON from response
        json_match = re.search(r'\{[^{}]*\}', content)
        if json_match:
            filters = json.loads(json_match.group())
            filters = _sanitize_nlp_filters(filters)
            logger.info("NLP query parsed via OpenRouter: %s → %s", text, filters)
            return filters

        logger.warning("OpenRouter NLP parse returned non-JSON: %s", content[:200])
        return None
    except Exception as e:
        logger.warning("OpenRouter NLP parse failed: %s", e)
        return None


def parse_nlp_query(text, config=None):
    """
    Parse a natural language job search query into structured filters.
    Priority: OpenRouter → Ollama → regex.

    Returns dict with keys: search, location, remote, min_score, experience,
    salary_min, salary_max, company_type, sort, portal, applied
    """
    if not text or not text.strip():
        return {}

    config = config or {}

    # --- Try OpenRouter first ---
    openrouter_result = _openrouter_parse_nlp_query(text)
    if openrouter_result is not None:
        return openrouter_result

    # --- Try Ollama ---
    use_ollama = config.get("scoring", {}).get("use_ollama", True)
    if use_ollama:
        try:
            import ollama as ollama_client

            model = config.get("scoring", {}).get("ollama_model", "mistral")

            prompt = _NLP_EXTRACTION_PROMPT.format(text=text)

            response = ollama_client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.1},
            )
            content = response["message"]["content"].strip()

            # Extract JSON from response
            json_match = re.search(r'\{[^{}]*\}', content)
            if json_match:
                filters = json.loads(json_match.group())
                filters = _sanitize_nlp_filters(filters)
                logger.info("NLP query parsed via Ollama: %s → %s", text, filters)
                return filters

            logger.warning("Ollama NLP parse returned non-JSON, falling back to regex")
        except ImportError:
            logger.info("ollama package not installed, using regex fallback for NLP")
        except ConnectionError:
            logger.info("Ollama not running, using regex fallback for NLP")
        except Exception as e:
            logger.warning("Ollama NLP parse failed: %s, using regex fallback", e)

    # --- Regex fallback ---
    filters = _regex_parse_nlp_query(text)
    logger.info("NLP query parsed via regex: %s → %s", text, filters)
    return filters


# =============================================================================
# CV Upload and Matching
# =============================================================================

from datetime import datetime as _datetime

CV_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cv_data.json")

# Shared skill pattern dict: {regex_pattern: display_label}
# Used for both CV parsing and JD extraction — consistent names ensure matching works.
# PM/product skills are listed first so they get priority when truncating to max_skills.
_SKILL_PATTERNS = {
    # --- PM / Product Management ---
    r"Product [Ss]trategy": "Product Strategy",
    r"(?:Product\s+)?Roadmap": "Roadmap",
    r"Stakeholder [Mm]anagement": "Stakeholder Management",
    r"User [Rr]esearch": "User Research",
    r"A/B [Tt]esting": "A/B Testing",
    r"Data [Aa]nalysis": "Data Analysis",
    r"Product Owner": "Product Owner",
    r"Prioriti[sz]ation": "Prioritization",
    r"\bPRD\b": "PRD",
    r"Go[\s-]to[\s-][Mm]arket|\bGTM\b": "Go-to-Market",
    r"Wireframe": "Wireframing",
    r"Prototyp": "Prototyping",
    r"\bOKR\b": "OKR",
    r"\bKPI\b": "KPI",
    r"Agile": "Agile",
    r"Scrum": "Scrum",
    r"Kanban": "Kanban",
    r"Cross[\s-]functional": "Cross-functional",
    r"Leadership": "Leadership",
    r"Mentoring": "Mentoring",
    r"Metrics": "Metrics",
    r"Analytics": "Analytics",
    r"\bUX\b": "UX",
    r"\bUI\b": "UI",
    r"Figma": "Figma",
    r"Jira": "Jira",
    r"Confluence": "Confluence",
    r"\bB2B\b": "B2B",
    r"\bB2C\b": "B2C",
    r"\bSaaS\b": "SaaS",
    r"Strategy": "Strategy",
    r"Growth": "Growth",
    r"Retention": "Retention",
    r"Conversion": "Conversion",
    r"Mobile": "Mobile",
    r"\biOS\b": "iOS",
    r"Android": "Android",
    # --- Data / Analytics tools ---
    r"SQL": "SQL",
    r"Python": "Python",
    r"Excel": "Excel",
    r"Tableau": "Tableau",
    r"Power BI": "Power BI",
    r"Data Science": "Data Science",
    # --- Domain / Finance ---
    r"Fintech": "Fintech",
    r"Payments": "Payments",
    r"\bUPI\b": "UPI",
    r"Lending": "Lending",
    # Note: bare "Credit" / "Cloud" removed — they false-match on
    # "Credit Electronic Developer" / "Oracle Cloud ERP" and inflate
    # scores for unrelated roles. The narrower patterns below
    # ("Credit Analysis", explicit ERP names) carry the real signal.
    r"Banking": "Banking",
    r"Risk [Mm]anagement": "Risk Management",
    r"Compliance": "Compliance",
    r"\bP&L\b": "P&L",
    r"Revenue": "Revenue",
    # --- Cloud / Infrastructure ---
    r"\bAPI\b": "API",
    r"\bREST\b": "REST",
    r"Microservices": "Microservices",
    r"\bAWS\b": "AWS",
    r"\bGCP\b": "GCP",
    r"Azure": "Azure",
    r"Kubernetes": "Kubernetes",
    r"Docker": "Docker",
    r"CI/CD": "CI/CD",
    r"\bGit\b": "Git",
    r"GitHub": "GitHub",
    # --- Languages / Frameworks ---
    r"Machine Learning": "Machine Learning",
    r"Deep Learning": "Deep Learning",
    r"\bAI\b": "AI",
    r"\bNLP\b": "NLP",
    r"React": "React",
    r"JavaScript": "JavaScript",
    r"TypeScript": "TypeScript",
    r"Node\.?[Jj][Ss]": "Node.js",
    r"Java\b": "Java",
    r"\bGo\b": "Go",
    # --- Databases ---
    r"MongoDB": "MongoDB",
    r"PostgreSQL": "PostgreSQL",
    r"Redis": "Redis",
    r"Kafka": "Kafka",
    r"Spark": "Spark",
    r"Hadoop": "Hadoop",

    # ─── Finance / Accounting / Audit ──────────────────────────────
    # ERPs + finance tools
    r"SAP\s+FICO|\bSAP\b": "SAP",
    r"Tally\s*ERP\s*9?|\bTally\b": "Tally ERP",
    r"Oracle\s+Fusion(?:\s+Cloud)?(?:\s+ERP)?|Oracle\s+ERP": "Oracle Fusion ERP",
    r"PeopleSoft": "PeopleSoft",
    r"QuickBooks": "QuickBooks",
    r"Bloomberg(?:\s+Terminal)?": "Bloomberg Terminal",
    r"Bloomberg\s+Market\s+Concepts|\bBMC\b": "Bloomberg BMC",
    r"Bloomberg\s+Finance\s+Fundamentals|\bBFF\b": "Bloomberg BFF",
    r"Advanced\s+Excel|VLOOKUP|XLOOKUP|Pivot\s+Table": "Advanced Excel",
    # Core finance / accounting concepts
    r"Financial\s+Reporting": "Financial Reporting",
    r"Financial\s+Statement[s]?\s+Analysis|Financial\s+Analysis": "Financial Analysis",
    r"Account\s+Reconciliation|Reconciliation[s]?": "Account Reconciliation",
    r"Ratio\s+Analysis": "Ratio Analysis",
    r"Time\s+Value\s+of\s+Money|\bTVM\b": "Time Value of Money (TVM)",
    r"Cost\s+Accounting": "Cost Accounting",
    r"Variance\s+Analysis": "Variance Analysis",
    r"Budget(?:ing|\s+Preparation)?": "Budgeting",
    r"Fixed\s+Asset\s+Accounting": "Fixed Asset Accounting",
    r"Ledger\s+Scrutiny|Ledger\b": "Ledger Scrutiny",
    r"Voucher\s+Verification|Voucher": "Voucher Verification",
    r"Month[\s-]End\s+Close|Month[\s-]End\s+Accounting": "Month-End Close",
    r"Audit(?:\s+Documentation|\s+Support)?": "Audit",
    r"Internal\s+Audit": "Internal Audit",
    r"Tax(?:ation)?": "Taxation",
    r"\bGST\b": "GST",
    r"\bTDS\b": "TDS",
    r"\bIFRS\b": "IFRS",
    r"\bGAAP\b": "GAAP",
    r"\bUSGAAP\b|US\s+GAAP": "US GAAP",
    r"\bIND[\s-]?AS\b": "Ind AS",
    r"Treasury": "Treasury",
    r"Working\s+Capital": "Working Capital Management",
    r"Cash\s+Flow": "Cash Flow Analysis",
    r"Credit\s+Analysis": "Credit Analysis",
    r"Equity\s+Research": "Equity Research",
    r"Investment\s+Banking|\bIB\b": "Investment Banking",
    r"M&A|Mergers\s+&\s+Acquisitions": "M&A",
    r"Valuation": "Valuation",
    r"\bDCF\b": "DCF Modeling",
    r"Financial\s+Modeling|Financial\s+Modelling": "Financial Modeling",
    r"\bFP&A\b|FP\s*&\s*A": "FP&A",
    r"Joint\s+Venture": "Joint Venture Accounting",
    # Soft skills / business
    r"Report\s+Preparation|Report\s+Writing": "Report Preparation",
    r"Business\s+Communication": "Business Communication",
    r"Attention\s+to\s+Detail": "Attention to Detail",
    r"Time\s+Management": "Time Management",
    r"Documentation": "Documentation",
    # Domain
    r"\bNBFC\b": "NBFC",
    r"Insurance": "Insurance",
    r"Capital\s+Markets?": "Capital Markets",
    r"Wealth\s+Management": "Wealth Management",
    r"Asset\s+Management": "Asset Management",
    r"Petroleum|Oil\s*&\s*Gas": "Oil & Gas",

    # ─── Marketing ─────────────────────────────────────────────────
    r"\bSEO\b": "SEO",
    r"\bSEM\b": "SEM",
    r"\bPPC\b": "PPC",
    r"Google\s+Ads|AdWords": "Google Ads",
    r"Google\s+Analytics|\bGA4\b": "Google Analytics",
    r"Content\s+Marketing": "Content Marketing",
    r"Email\s+Marketing": "Email Marketing",
    r"Social\s+Media\s+Marketing|Social\s+Media": "Social Media",
    r"HubSpot": "HubSpot",
    r"Marketo": "Marketo",
    r"Marketing\s+Automation": "Marketing Automation",
    r"Brand\s+(?:Strategy|Management)|Branding": "Branding",
    r"Copywriting": "Copywriting",
    r"Campaign\s+Management": "Campaign Management",
    r"Conversion\s+Rate\s+Optimi[sz]ation|\bCRO\b": "Conversion Optimization",
    r"Demand\s+Gen(?:eration)?": "Demand Generation",
    r"Performance\s+Marketing": "Performance Marketing",

    # ─── Sales / Business Development ──────────────────────────────
    r"Salesforce": "Salesforce",
    r"\bCRM\b": "CRM",
    r"Account\s+Management": "Account Management",
    r"Enterprise\s+Sales": "Enterprise Sales",
    r"Lead\s+Generation": "Lead Generation",
    r"Negotiation": "Negotiation",
    r"Pipeline\s+Management|Sales\s+Pipeline": "Pipeline Management",
    r"Solution\s+Selling": "Solution Selling",
    r"Business\s+Development": "Business Development",
    r"Cold\s+(?:Calling|Outreach|Emailing)": "Cold Outreach",
    r"Quota\s+(?:Attainment|Carrying)?|Sales\s+Quota": "Quota Attainment",
    r"Key\s+Account": "Key Account Management",

    # ─── HR / People ───────────────────────────────────────────────
    r"Recruit(?:ing|ment)": "Recruiting",
    r"Talent\s+Acquisition": "Talent Acquisition",
    r"Talent\s+Management": "Talent Management",
    r"Employee\s+Relations": "Employee Relations",
    r"Performance\s+Management": "Performance Management",
    r"\bHRIS\b": "HRIS",
    r"\bHRBP\b": "HRBP",
    r"Workday": "Workday",
    r"Compensation\s*(?:&|and)?\s*Benefits|\bC&B\b": "Compensation & Benefits",
    r"Onboarding": "Onboarding",
    r"Learning\s*(?:&|and)\s*Development|\bL&D\b": "Learning & Development",
    r"Payroll": "Payroll",
    r"Succession\s+Planning": "Succession Planning",

    # ─── Design / UX ───────────────────────────────────────────────
    r"\bSketch\b": "Sketch",
    r"Adobe\s+XD": "Adobe XD",
    r"InVision": "InVision",
    r"Design\s+System": "Design Systems",
    r"Usability\s+Testing": "Usability Testing",
    r"Interaction\s+Design": "Interaction Design",
    r"Visual\s+Design": "Visual Design",
    r"Accessibility|\bWCAG\b": "Accessibility",
    r"Photoshop": "Photoshop",
    r"Illustrator": "Illustrator",
    r"User\s+Experience": "User Experience",
    r"Information\s+Architecture": "Information Architecture",
    r"Design\s+Thinking": "Design Thinking",

    # ─── Data Science / ML tools ───────────────────────────────────
    r"TensorFlow": "TensorFlow",
    r"PyTorch": "PyTorch",
    r"scikit[\s-]?learn|sklearn": "scikit-learn",
    r"\bpandas\b": "pandas",
    r"\bNumPy\b": "NumPy",
    r"Statistic(?:s|al)": "Statistics",
    r"Data\s+Visuali[sz]ation": "Data Visualization",
    r"Predictive\s+Model(?:ing|ling)?": "Predictive Modeling",
    r"Computer\s+Vision": "Computer Vision",
    r"\bLLMs?\b": "LLMs",
    r"Recommendation\s+System": "Recommendation Systems",
    r"Time\s+Series": "Time Series",

    # ─── DevOps / Infra tools ──────────────────────────────────────
    r"Terraform": "Terraform",
    r"Jenkins": "Jenkins",
    r"Prometheus": "Prometheus",
    r"Grafana": "Grafana",
    r"Ansible": "Ansible",
    r"\bLinux\b": "Linux",
    r"Observability": "Observability",
    r"\bSRE\b|Site\s+Reliability": "SRE",
    r"Infrastructure\s+as\s+Code|\bIaC\b": "Infrastructure as Code",
    r"\bHelm\b": "Helm",

    # ─── Engineering / Manufacturing ───────────────────────────────
    r"SolidWorks": "SolidWorks",
    r"AutoCAD": "AutoCAD",
    r"\bCAD\b": "CAD",
    r"Lean\s+Manufacturing": "Lean Manufacturing",
    r"Six\s+Sigma": "Six Sigma",
    r"Supply\s+Chain": "Supply Chain",
    r"Quality\s+(?:Control|Assurance)": "Quality Assurance",
    r"Production\s+Planning": "Production Planning",
    r"\bCNC\b": "CNC",
    r"\bGD&T\b": "GD&T",
    r"Process\s+Improvement": "Process Improvement",
    r"Manufacturing": "Manufacturing",
    r"\bMATLAB\b": "MATLAB",
    r"\bANSYS\b": "ANSYS",

    # ─── Operations / General ──────────────────────────────────────
    r"Project\s+Management|\bPMP\b": "Project Management",
    r"Operations\s+Management": "Operations Management",
    r"Vendor\s+Management": "Vendor Management",
    r"Customer\s+Success": "Customer Success",
    r"Customer\s+(?:Support|Service)": "Customer Support",
    r"Process\s+Optimi[sz]ation": "Process Optimization",
    r"Communication\s+Skills": "Communication",
    r"Problem[\s-]Solving": "Problem Solving",
}

# Backward-compatible alias used by parse_cv_text()
_CV_SKILL_PATTERNS = _SKILL_PATTERNS


# Heuristics for inferring user preferences directly from CV text.
# Kept conservative — we'd rather miss a guess than mis-fill the form.

# Job-title fragments. The base nouns (Manager, Engineer, …) are matched
# with optional seniority/specialty prefixes so we catch "Senior Product
# Manager" as "Senior Product Manager" rather than just "Manager".
_ROLE_BASES = [
    # Product / Engineering / Design
    "Product Manager", "Program Manager", "Project Manager",
    "Product Owner", "Product Lead", "Product Designer",
    "Software Engineer", "Software Developer", "Backend Engineer",
    "Frontend Engineer", "Full[-\\s]Stack Engineer", "Mobile Engineer",
    "Data Engineer", "Data Scientist", "Data Analyst",
    "Machine Learning Engineer", "ML Engineer", "AI Engineer",
    "Engineering Manager", "Technical Lead", "Tech Lead",
    "DevOps Engineer", "Platform Engineer", "SRE", "Site Reliability Engineer",
    "Designer", "UX Designer", "UI Designer",

    # Business / Strategy / Marketing
    "Business Analyst", "Business Development Manager",
    "Marketing Manager", "Growth Manager", "Brand Manager",
    "Operations Manager", "Customer Success Manager",
    "Strategy Consultant", "Management Consultant",

    # Finance / Accounting / Audit  ← previously absent, breaking finance CVs
    "Accountant", "Senior Accountant", "Junior Accountant",
    "Staff Accountant", "Accounting Assistant", "Accounting Analyst",
    "Accounts Executive", "Accounts Officer", "Accounts Manager",
    "Finance Analyst", "Financial Analyst", "Senior Financial Analyst",
    "Finance Manager", "Finance Executive", "Finance Associate",
    "Financial Controller", "Controller", "CFO", "Chief Financial Officer",
    "Auditor", "Internal Auditor", "External Auditor", "Statutory Auditor",
    "Audit Associate", "Audit Manager", "Audit Trainee",
    "Tax Analyst", "Tax Associate", "Tax Manager", "Tax Consultant",
    "Treasury Analyst", "Treasury Manager",
    "Investment Banker", "Investment Analyst",
    "Equity Research Analyst", "Research Analyst", "Credit Analyst",
    "Risk Analyst", "Risk Manager", "Compliance Analyst", "Compliance Manager",
    "FP&A Analyst", "Budget Analyst", "Cost Analyst",
    "SAP FICO Consultant", "Oracle Fusion Consultant", "ERP Consultant",
    "Banking Analyst", "Banking Associate", "Relationship Manager",

    # Entry-level / trainee tracks that finance / business candidates apply to
    "Summer Trainee", "Summer Intern", "Management Trainee",
    "Graduate Trainee", "Finance Trainee", "Accounting Trainee",
    "Trainee", "Intern", "Associate",

    # General leadership
    "Director", "VP", "Head", "Founder", "Founding Engineer",
    "Consultant", "Research Scientist",

    # HR / People
    "HR Manager", "HR Business Partner", "HRBP", "Human Resources Manager",
    "Talent Acquisition", "Talent Acquisition Specialist", "Recruiter",
    "Technical Recruiter", "HR Generalist", "HR Executive", "People Partner",
    "Compensation Analyst", "Learning and Development Manager",

    # Sales / Business Development
    "Sales Manager", "Sales Executive", "Account Executive", "Account Manager",
    "Key Account Manager", "Sales Development Representative", "SDR",
    "Business Development Executive", "Inside Sales", "Sales Director",
    "Regional Sales Manager", "Pre-Sales Consultant", "Solutions Consultant",

    # Marketing (beyond the existing manager roles)
    "Digital Marketing Manager", "Performance Marketing Manager",
    "Content Marketing Manager", "Product Marketing Manager", "SEO Specialist",
    "Social Media Manager", "Marketing Executive", "Content Writer",
    "Marketing Analyst", "Demand Generation Manager",

    # Design (beyond UX/UI)
    "Graphic Designer", "Visual Designer", "Interaction Designer",
    "Design Lead", "UX Researcher", "Motion Designer",

    # Mechanical / Manufacturing / Civil / Electrical
    "Mechanical Engineer", "Manufacturing Engineer", "Production Engineer",
    "Design Engineer", "Quality Engineer", "Process Engineer",
    "Maintenance Engineer", "Industrial Engineer", "Civil Engineer",
    "Electrical Engineer", "Production Manager", "Plant Manager",
    "Quality Manager", "Supply Chain Manager", "Procurement Manager",
    "Logistics Manager", "Operations Executive",

    # Healthcare / Life sciences
    "Registered Nurse", "Staff Nurse", "Pharmacist", "Clinical Research Associate",
    "Medical Officer", "Healthcare Administrator", "Lab Technician",

    # Legal / Admin / Support / Content
    "Legal Counsel", "Legal Associate", "Paralegal", "Company Secretary",
    "Content Strategist", "Technical Writer", "Customer Support Executive",
    "Customer Success Manager", "Administrative Manager", "Executive Assistant",
]
_SENIORITIES = ["Senior", "Sr\\.?", "Staff", "Principal", "Lead", "Chief", "Junior", "Jr\\.?", "Associate"]
_ROLE_REGEX = re.compile(
    r"(?<![A-Za-z])((?:" + "|".join(_SENIORITIES) + r")\s+)?(" + "|".join(_ROLE_BASES) + r")(?![A-Za-z])",
    re.IGNORECASE,
)

# Cities we'll detect verbatim. Map normalised (lowercase) → display form.
_CV_CITY_MAP = {
    # India
    "pune": "Pune", "mumbai": "Mumbai", "bombay": "Mumbai",
    "bangalore": "Bangalore", "bengaluru": "Bangalore", "bengalūru": "Bangalore",
    "delhi": "Delhi", "new delhi": "Delhi", "ncr": "Delhi / NCR",
    "gurgaon": "Delhi / NCR", "gurugram": "Delhi / NCR", "noida": "Delhi / NCR",
    "hyderabad": "Hyderabad", "chennai": "Chennai", "kolkata": "Kolkata",
    "ahmedabad": "Ahmedabad", "jaipur": "Jaipur", "chandigarh": "Chandigarh",
    "kochi": "Kochi", "indore": "Indore", "coimbatore": "Coimbatore",
    "trivandrum": "Thiruvananthapuram", "thiruvananthapuram": "Thiruvananthapuram",
    # Hubs further afield
    "singapore": "Singapore", "dubai": "Dubai / UAE", "abu dhabi": "Dubai / UAE",
    "london": "London", "berlin": "Berlin", "amsterdam": "Amsterdam",
    "san francisco": "San Francisco", "new york": "New York", "nyc": "New York",
    "sydney": "Sydney", "melbourne": "Melbourne", "toronto": "Toronto",
    # Catch-alls
    "remote": "Remote", "anywhere": "Remote", "work from home": "Remote", "wfh": "Remote",
}


def _suggest_job_titles(text, limit=5):
    """Extract up to N plausible job titles from CV text."""
    if not text:
        return []
    seen = set()
    out = []
    for m in _ROLE_REGEX.finditer(text):
        prefix = (m.group(1) or "").strip()
        base = m.group(2).strip()
        # Normalise whitespace + casing
        title = " ".join(filter(None, [prefix, base])).strip()
        title = re.sub(r"\s+", " ", title)
        # Title-case while preserving "VP", "ML", "AI", etc.
        parts = []
        for p in title.split():
            parts.append(p if p.isupper() and len(p) <= 3 else p.capitalize())
        title = " ".join(parts)
        if title.lower() in seen:
            continue
        seen.add(title.lower())
        out.append(title)
        if len(out) >= limit:
            break
    return out


def _suggest_locations(text, limit=5):
    """Extract canonical city/location preferences from CV text."""
    if not text:
        return []
    text_lower = text.lower()
    seen = set()
    out = []
    for needle, canonical in _CV_CITY_MAP.items():
        if needle in text_lower and canonical not in seen:
            seen.add(canonical)
            out.append(canonical)
            if len(out) >= limit:
                break
    return out


def _extract_skills_section(text):
    """
    Return the raw text of a CV's Skills / Competencies section, if present.
    Handles both inline ("Skills: A, B, C") and block ("SKILLS\nA, B, C")
    layouts, stopping at the next major section heading or blank line.
    """
    if not text:
        return ""
    header = re.compile(
        r'^\s*(?:technical\s+|core\s+|key\s+)?'
        r'(?:skills|competencies|areas of expertise|skill set)\s*[:\-]?\s*(.*)$',
        re.IGNORECASE)
    section_break = re.compile(
        r'^\s*(experience|education|projects?|summary|profile|work\b|employment|'
        r'certifications?|achievements?|awards?|languages?|interests?|references?)\b',
        re.IGNORECASE)
    out, capturing = [], False
    for ln in text.splitlines():
        if not capturing:
            m = header.match(ln)
            if m:
                capturing = True
                if m.group(1).strip():
                    out.append(m.group(1))
            continue
        if not ln.strip():
            if out:
                break
            continue
        if section_break.match(ln):
            break
        out.append(ln)
    return " ".join(out)


def parse_cv_text(text):
    """
    Parse raw CV text and extract structured data.

    Args:
        text: Raw text content of the CV

    Returns:
        dict with keys: skills, suggested_job_titles, suggested_locations,
        raw_text, uploaded_at.
    """
    base = {
        "skills": [],
        "suggested_job_titles": [],
        "suggested_locations": [],
        "raw_text": text or "",
        "uploaded_at": _datetime.now().isoformat(),
    }
    if not text or not text.strip():
        return base

    found_skills = []
    for pattern, display in _CV_SKILL_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            if display not in found_skills:
                found_skills.append(display)

    # Generic fallback: pull explicit skills from a "Skills" section so CVs
    # whose terminology isn't in the dictionary still surface real skills.
    # Critical for non-tech backgrounds (HR, sales, design, manufacturing…)
    # where the pattern dictionary alone under-extracts.
    _lower = {s.lower() for s in found_skills}
    for phrase in re.split(r'[,•|/;\n]+', _extract_skills_section(text)):
        p = phrase.strip(" .\t-•·")
        if 2 <= len(p) <= 32 and not p.replace(" ", "").isdigit() and p.lower() not in _lower:
            disp = p if (p.isupper() and len(p) <= 5) else p.title() if p.islower() else p
            found_skills.append(disp)
            _lower.add(p.lower())

    base["skills"] = found_skills[:30]
    base["suggested_job_titles"] = _suggest_job_titles(text)
    base["suggested_locations"] = _suggest_locations(text)
    return base


def _current_session_user_id():
    """
    Return the Flask session user's DB id when running inside a request,
    else None. Used to scope load_cv_data / save_cv_data to the current user.
    """
    try:
        from flask import session as _session, has_request_context
    except Exception:
        return None
    try:
        if not has_request_context():
            return None
        uid = (_session.get("user") or {}).get("id") if _session else None
        return int(uid) if uid else None
    except Exception:
        return None


def load_cv_data(user_id: int = None):
    """
    Load CV data.
    - When a user is in scope (explicit user_id or active Flask session),
      return THAT user's CV from the per-user table — and NOTHING ELSE.
      Falling through to the legacy JSON file would silently leak the
      original developer's CV to every brand-new user, which is exactly
      what happened in early testing.
    - Outside a user context (CLI / scraper), fall back to the legacy
      cv_data.json so single-user workflows keep working.
    """
    resolved = user_id if user_id is not None else _current_session_user_id()
    if resolved:
        try:
            from database import get_user_cv_data as _gucd
            return _gucd(resolved)  # may be None — that's a valid answer
        except Exception:
            return None
    # No user context at all → legacy global JSON
    if not os.path.exists(CV_DATA_PATH):
        return None
    try:
        with open(CV_DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_cv_data(cv_data, user_id: int = None):
    """Save CV data.

    Requires a user_id whenever called from a Flask request context — we
    refuse to silently fall through to the legacy global cv_data.json
    because that would overwrite the owner's CV with another user's data
    (and conversely, leak it to every CLI/scraper code path that reads
    cv_data.json as a fallback).

    The legacy JSON write path is only retained for true CLI usage
    (no Flask app loaded) and bootstrap scripts.
    """
    if user_id is None:
        user_id = _current_session_user_id()
    if user_id:
        from database import save_user_cv_data as _sucd
        _sucd(user_id, cv_data)
        return

    # If Flask is loaded but no session, refuse to clobber the global file.
    try:
        from flask import has_request_context as _hrc
        if _hrc():
            raise RuntimeError(
                "save_cv_data called from a Flask request with no user_id. "
                "Pass current_user_id() explicitly to avoid clobbering the "
                "legacy global cv_data.json."
            )
    except ImportError:
        pass

    with open(CV_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(cv_data, f, indent=2)


def cv_score(job, cv_data, preferences=None, job_vec=None, profile_vec=None):
    """
    Score a job 0-100 against the user's CV + preferences.

    Thin wrapper over the unified scorer.score_job (deterministic + optional
    embedding semantic component). When job_vec and profile_vec are both
    provided the semantic component is blended in; otherwise the score is
    deterministic-only. Returns int 0-100.
    """
    if not cv_data:
        return 0
    return scorer.score_job(job, cv_data, preferences or {},
                            job_vec=job_vec, profile_vec=profile_vec)[0]


def _preference_boost(job, jd_text_lower, preferences):
    """Compute the additive boost from user preferences. See cv_score()."""
    boost = 0
    role_lower = (job.get("role") or "").lower()
    loc_lower  = (job.get("location") or "").lower()
    remote_lower = (job.get("remote_status") or "").lower()

    # +25 if any preferred job title appears in the role
    pref_titles = [t.strip().lower() for t in (preferences.get("job_titles") or []) if t and t.strip()]
    if pref_titles and any(t in role_lower for t in pref_titles):
        boost += 25

    # +6 for a location match
    pref_locs = [l.strip().lower() for l in (preferences.get("locations") or []) if l and l.strip()]
    if pref_locs:
        if any(l in loc_lower for l in pref_locs):
            boost += 6
        elif "remote" in pref_locs and "remote" in remote_lower:
            boost += 6

    # +2 per matched transferable skill in JD, capped at 12
    pref_skills = [s.strip().lower() for s in (preferences.get("transferable_skills") or []) if s and s.strip()]
    if pref_skills and jd_text_lower:
        matches = sum(1 for s in pref_skills if s and s in jd_text_lower)
        boost += min(matches * 2, 12)

    return boost


# ── ATS readiness score ──────────────────────────────────────────────────
# Heuristic, no external services. Scores how well an uploaded CV is structured
# to pass automated screening and match the user's target roles.
_ATS_ACTION_VERBS = {
    "led", "built", "launched", "shipped", "drove", "managed", "owned", "created",
    "designed", "developed", "implemented", "delivered", "improved", "increased",
    "reduced", "optimized", "optimised", "scaled", "grew", "negotiated", "analyzed",
    "analysed", "automated", "streamlined", "spearheaded", "established", "executed",
    "coordinated", "mentored", "architected", "initiated", "accelerated", "generated",
    "saved", "boosted", "achieved", "transformed", "directed", "founded",
}

_ATS_SECTION_HINTS = {
    "experience": ("experience", "work history", "employment", "professional experience"),
    "education":  ("education", "academic", "qualification"),
    "skills":     ("skills", "technical skills", "competencies", "core competencies"),
    "summary":    ("summary", "profile", "objective", "about me"),
}


def compute_ats_score(cv_data, preferences=None, market_skills=None):
    """
    Heuristic ATS-readiness score (0-100) for an uploaded CV.

    Derived entirely from the parsed CV text + the user's skills, plus — when
    available — the in-demand skills for the user's target roles (for the
    keyword-coverage component). No external API; safe to run on Vercel.

    Returns: {score, band, breakdown:[{label, points, max, status}], suggestions:[...]}.
    """
    preferences = preferences or {}
    cv_data = cv_data or {}
    raw = cv_data.get("raw_text") or ""
    text = raw.lower()
    skills = cv_data.get("skills") or []
    breakdown = []
    suggestions = []

    def add(label, pts, mx, ok, tip=None):
        breakdown.append({
            "label": label, "points": int(pts), "max": mx,
            "status": "pass" if ok else ("warn" if pts > 0 else "fail"),
        })
        if tip and not ok:
            suggestions.append(tip)

    # 1. Contact details (10)
    has_email = bool(re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', raw))
    has_phone = bool(re.search(r'(\+?\d[\d\s().-]{7,}\d)', raw))
    c_pts = (5 if has_email else 0) + (5 if has_phone else 0)
    add("Contact details (email + phone)", c_pts, 10, c_pts == 10,
        "Add a clear email and phone number near the top — parsers look for them first.")

    # 2. Standard sections (20)
    sec_pts = 0
    missing = []
    for key, hints in _ATS_SECTION_HINTS.items():
        if any(h in text for h in hints):
            sec_pts += 5
        else:
            missing.append(key)
    add("Standard sections present", sec_pts, 20, not missing,
        ("Add clear headings for: " + ", ".join(missing) + ".") if missing else None)

    # 3. Skills depth (15)
    ns = len(skills)
    sk_pts = 15 if ns >= 12 else 11 if ns >= 8 else 7 if ns >= 5 else max(0, ns)
    add(f"Skills listed ({ns})", sk_pts, 15, ns >= 8,
        "List 8–12 concrete, role-relevant skills in a dedicated Skills section.")

    # 4. Quantified achievements (15)
    lines_with_numbers = sum(
        1 for ln in raw.splitlines()
        if re.search(r'\d', ln) and len(ln.strip()) > 20
    )
    q_pts = 15 if lines_with_numbers >= 8 else 10 if lines_with_numbers >= 4 else 5 if lines_with_numbers >= 1 else 0
    add(f"Quantified achievements ({lines_with_numbers} lines with metrics)", q_pts, 15, lines_with_numbers >= 4,
        "Quantify impact in your bullets — %, revenue, time saved, users, scale.")

    # 5. Strong action verbs (10)
    found_verbs = {v for v in _ATS_ACTION_VERBS if re.search(r'\b' + re.escape(v) + r'\b', text)}
    nv = len(found_verbs)
    v_pts = 10 if nv >= 8 else 7 if nv >= 5 else 4 if nv >= 2 else 0
    add(f"Strong action verbs ({nv})", v_pts, 10, nv >= 5,
        "Start bullets with strong verbs (Led, Built, Launched, Reduced…).")

    # 6. Length (10)
    words = len(re.findall(r'\b\w+\b', raw))
    if 350 <= words <= 1000:
        l_pts, l_ok = 10, True
    elif 250 <= words <= 1400:
        l_pts, l_ok = 6, False
    else:
        l_pts, l_ok = 3, False
    add(f"Length (~{words} words)", l_pts, 10, l_ok,
        "Aim for ~1–2 pages (≈350–1000 words) — too short reads thin, too long won't be read.")

    # 7. Keyword match to target roles (20)
    market = market_skills or []
    if market:
        skl = {s.lower() for s in skills}
        topn = market[:15]
        present, missing_kw = 0, []
        for m in topn:
            name = (m.get("skill") if isinstance(m, dict) else m) or ""
            if not name:
                continue
            if name.lower() in skl or name.lower() in text:
                present += 1
            else:
                missing_kw.append(name)
        frac = present / max(len(topn), 1)
        cov_pts = round(frac * 20)
        cov_ok = frac >= 0.5
        tip = ("Add in-demand keywords for your target roles where you have real experience: "
               + ", ".join(missing_kw[:6]) + ".") if (not cov_ok and missing_kw) else None
        add(f"Keyword match to target roles ({present}/{len(topn)})", cov_pts, 20, cov_ok, tip)
    else:
        # No market data — give a neutral score rather than penalising.
        add("Keyword match to target roles", 12, 20, True)

    score = max(0, min(100, sum(b["points"] for b in breakdown)))
    band = ("Strong" if score >= 80 else "Good" if score >= 60
            else "Needs work" if score >= 40 else "Weak")
    return {"score": score, "band": band, "breakdown": breakdown, "suggestions": suggestions[:6]}


# Curated tips for common missing skills
SKILL_TIPS = {
    "python": "Take a free Python for Data Analysis course on Kaggle (2-3 days). Focus on pandas.",
    "sql": "If you have any SQL experience, emphasize it explicitly in your CV with specific examples.",
    "figma": "Complete Figma basics on YouTube (1 day). Add 'basic Figma' to your skills section.",
    "kafka": "Frame any messaging or event-driven systems experience as equivalent. Add a note in your cover letter.",
    "kubernetes": "Note any cloud infrastructure or DevOps exposure from your work experience.",
    "docker": "Mention any containerization or DevOps exposure. A 2-hour intro tutorial covers basics.",
    "machine learning": "Highlight any analytics or predictive modelling work from your experience.",
    "react": "Note your familiarity with web product decisions if you've worked with frontend teams.",
    "javascript": "Familiarity (not proficiency) is often sufficient. Mention decisions around JS-heavy features.",
    "aws": "Highlight any cloud migration or AWS-based projects from your work history.",
    "a/b testing": "Emphasize any data-driven experiments or hypothesis testing you've done.",
    "user research": "Frame any customer interviews, NPS analysis, or journey mapping work you've done.",
    "agile": "Make it explicit with specific examples of sprints, stand-ups, or retrospectives.",
    "data analysis": "Quantify your analytics work — datasets analyzed, reports built, decisions influenced.",
    "tableau": "Free Tableau Public is available. Even basic dashboards count — add to skills.",
    "jira": "Mention any project tracking tools you've used (Jira, Asana, Trello, ServiceNow, etc.).",
}


def compute_gap_analysis(job, cv_data):
    """
    Compute the gap between a job's requirements and the applicant's CV.

    Args:
        job: dict with role, job_description fields
        cv_data: dict from parse_cv_text(), or None

    Returns:
        dict: {cv_score, matched_skills, missing_skills, action_steps}
    """
    if not cv_data:
        return {
            "cv_score": 0,
            "matched_skills": [],
            "missing_skills": [],
            "action_steps": ["Upload your CV on the CV page to see personalized gap analysis."],
        }

    cv_skills_lower = {s.lower(): s for s in cv_data.get("skills", [])}
    jd_text = " ".join([job.get("role", ""), job.get("job_description", "")])
    jd_skills = extract_skills(jd_text, max_skills=20)

    if not jd_skills:
        return {
            "cv_score": cv_score(job, cv_data),
            "matched_skills": [],
            "missing_skills": [],
            "action_steps": ["No specific skills detected in job description."],
        }

    matched = []
    missing = []
    for skill in jd_skills:
        if skill.lower() in cv_skills_lower:
            matched.append(skill)
        else:
            missing.append(skill)

    score = int(len(matched) / len(jd_skills) * 100) if jd_skills else 0

    # Generate action steps for top 3 missing skills
    action_steps = []
    for skill in missing[:3]:
        tip = SKILL_TIPS.get(skill.lower())
        if tip:
            action_steps.append(f"**{skill}**: {tip}")
        else:
            action_steps.append(f"**{skill}**: Research this skill and add relevant experience from your background.")

    if not missing:
        action_steps = ["Great match! Highlight your strongest matching skills in the cover letter."]

    return {
        "cv_score": min(score, 100),
        "matched_skills": matched,
        "missing_skills": missing,
        "action_steps": action_steps,
    }
