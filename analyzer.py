"""
analyzer.py - Job analysis and scoring using Ollama (mistral) with keyword-based fallback.
Scores each job 0-100 based on relevance to user preferences.
"""

import logging
import os
import re
import json

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


def keyword_score(job, preferences):
    """
    Score a job 0-100 using keyword matching.
    This is the fallback scorer when Ollama is unavailable.

    Scoring breakdown:
      - Title match:           0-30  (exact match in role title is heavily rewarded)
      - Location match:        0-10
      - Remote bonus:          0-10
      - Industry match:        0-20  (fintech/banking/lending keywords)
      - PM keywords:           0-20  (product management terms in description)
      - Growth signals:        0-10
      - Transferable skills:   0-15  (banking/finance skills mentioned in JD)
      - Penalty:               -20   (irrelevant domain detected)
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
            return max(0, score - 20)

    # Title match (0-30) — strongest signal
    user_titles = [t.lower().strip() for t in preferences.get("job_titles", [])]
    best_title_score = 0
    for title in user_titles:
        if title in role_lower:
            # Exact phrase match in role title
            best_title_score = max(best_title_score, 30)
        else:
            # Partial: check how many words from the preferred title appear in the role
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
    user_locations = [loc.lower().strip() for loc in preferences.get("locations", [])]
    job_loc = job.get("location", "").lower()
    for loc in user_locations:
        if loc in job_loc or job_loc in loc:
            score += 10
            break

    # Remote work bonus (0-10)
    for kw, pts in REMOTE_KEYWORDS.items():
        if kw in text:
            score += pts
            break

    # Industry/domain relevance (0-20) — accumulate multiple matches
    industry_score = 0
    for kw, pts in FINTECH_KEYWORDS.items():
        if kw in text:
            industry_score += pts
    score += min(industry_score, 20)

    # PM keywords in description/title (0-20) — accumulate
    pm_score = 0
    for kw, pts in PM_KEYWORDS.items():
        if kw in text:
            pm_score += pts
    score += min(pm_score, 20)

    # Career growth (0-10)
    growth_score = 0
    for kw, pts in GROWTH_KEYWORDS.items():
        if kw in text:
            growth_score += pts
    score += min(growth_score, 10)

    # Transferable skills from banking/finance (0-15)
    transferable = preferences.get("transferable_skills", [])
    if transferable:
        ts_score = 0
        for skill in transferable:
            if skill.lower() in text:
                ts_score += 5
        score += min(ts_score, 15)

    return min(score, 100)


# =============================================================================
# Ollama-based scoring
# =============================================================================

def ollama_score(job, preferences, config):
    """
    Use Ollama (mistral) to score a job and generate analysis.
    Returns (score, analysis_text) or None if Ollama fails.
    """
    try:
        import ollama as ollama_client
    except ImportError:
        logger.warning("ollama package not installed, falling back to keyword scoring")
        return None

    model = config.get("scoring", {}).get("ollama_model", "mistral")
    timeout = config.get("scoring", {}).get("ollama_timeout", 60)

    transferable = preferences.get("transferable_skills", [])
    transferable_text = f"\n- Transferable skills from banking: {', '.join(transferable)}" if transferable else ""

    prompt = f"""Analyze this job posting and score it 0-100 for a candidate with the following profile:
- Career transitioner from banking/financial services to Product Management
- Looking for roles: {', '.join(preferences.get('job_titles', ['Product Manager']))}
- Preferred locations: {', '.join(preferences.get('locations', ['Remote']))}
- Industries of interest: {', '.join(preferences.get('industries', ['Fintech']))}{transferable_text}

Job Details:
- Title: {job.get('role', 'Unknown')}
- Company: {job.get('company', 'Unknown')}
- Location: {job.get('location', 'Unknown')}
- Salary: {job.get('salary', 'Not specified')}
- Description: {job.get('job_description', 'No description available')[:500]}

Score based on:
1. Role match with preferred titles (0-25 points)
2. Location match (0-15 points)
3. Remote/hybrid flexibility (0-15 points)
4. Domain relevance - banking/fintech background advantage (0-15 points)
5. Career growth potential for PM transition (0-15 points)
6. Company type suitability - startup vs corporate (0-15 points)

Respond ONLY with valid JSON in this exact format:
{{"score": <number 0-100>, "remote_status": "<remote|hybrid|on-site>", "company_type": "<startup|corporate>", "reason": "<one sentence explanation>"}}"""

    try:
        response = ollama_client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.1},
        )
        content = response["message"]["content"].strip()

        # Extract JSON from response
        json_match = re.search(r'\{[^}]+\}', content)
        if json_match:
            result = json.loads(json_match.group())
            return result
        else:
            logger.warning("Ollama returned non-JSON response: %s", content[:200])
            return None
    except ConnectionError:
        logger.warning("Ollama not running. Falling back to keyword scoring.")
        return None
    except Exception as e:
        logger.warning("Ollama scoring failed: %s. Falling back to keyword scoring.", e)
        return None


# =============================================================================
# Main analysis pipeline
# =============================================================================

def generate_application_email(job, preferences):
    """Generate a short personalized application email draft."""
    role = job.get("role", "the role")
    company = job.get("company", "your company")
    description = job.get("job_description", "")

    # Extract a few skills from the description
    skills = extract_skills(description, max_skills=3)
    skills_text = ", ".join(skills) if skills else "product strategy and data-driven decision making"

    email = (
        f"Dear Hiring Team at {company},\n\n"
        f"I am writing to express my interest in the {role} position. "
        f"With my background in banking and financial services, I bring a strong foundation in "
        f"analytical thinking, stakeholder management, and customer-centric problem solving. "
        f"My experience with {skills_text} aligns well with this role's requirements. "
        f"I am excited about the opportunity to leverage my domain expertise "
        f"to drive product impact at {company}.\n\n"
        f"I would welcome the chance to discuss how my skills can contribute to your team.\n\n"
        f"Best regards"
    )
    return email


def generate_tailored_points(job, preferences, config):
    """
    Generate tailored resume/cover-letter bullet points for a specific job.
    Uses Ollama if available, otherwise keyword-based fallback.
    """
    role = job.get("role", "the role")
    company = job.get("company", "the company")
    description = job.get("job_description", "")
    transferable = preferences.get("transferable_skills", [])
    skills = extract_skills(description, max_skills=5)

    # Try Ollama first
    use_ollama = config.get("scoring", {}).get("use_ollama", True)
    if use_ollama:
        try:
            import ollama as ollama_client
            model = config.get("scoring", {}).get("ollama_model", "mistral")

            prompt = f"""Generate 4-5 tailored resume bullet points for a banking professional applying to this role.

Role: {role} at {company}
Key skills needed: {', '.join(skills) if skills else 'product management'}
Candidate's transferable skills: {', '.join(transferable) if transferable else 'stakeholder management, data analysis, risk management'}
Job Description: {description[:600]}

Write bullet points that:
1. Map banking experience to the role requirements
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
            # Extract JSON array
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                points = json.loads(json_match.group())
                if isinstance(points, list) and len(points) > 0:
                    return points
        except Exception:
            pass  # Fall through to keyword-based

    # Keyword-based fallback
    points = []
    skill_map = {
        "stakeholder management": f"Led cross-functional stakeholder alignment across 5+ departments at previous banking role, directly applicable to {role} coordination needs",
        "risk management": f"Built risk assessment frameworks processing 1000+ decisions monthly, transferable to product risk evaluation at {company}",
        "data analysis": f"Analyzed large-scale financial datasets to drive business decisions, relevant to data-driven product management at {company}",
        "regulatory compliance": f"Navigated complex regulatory requirements in banking, an advantage for {company}'s compliance-sensitive product decisions",
        "p&l ownership": f"Managed P&L for banking products with revenue impact, directly applicable to product ownership metrics at {company}",
        "process optimization": f"Optimized banking workflows reducing processing time by 30%, bringing operational efficiency mindset to {role}",
        "cross-functional leadership": f"Led cross-functional teams of 10+ in banking transformation projects, relevant to product team collaboration at {company}",
        "client relationship management": f"Managed relationships with 50+ enterprise banking clients, bringing customer-centric approach to product decisions at {company}",
    }
    for skill in transferable:
        key = skill.lower()
        if key in skill_map:
            points.append(skill_map[key])
    if not points:
        points = [
            f"Leverage 10+ years of banking domain expertise to bring unique financial services perspective to {role} at {company}",
            f"Apply analytical rigor from financial services background to data-driven product decisions at {company}",
            f"Bring enterprise stakeholder management experience to cross-functional product leadership at {company}",
            f"Translate deep understanding of customer financial needs into user-centric product strategy for {company}",
        ]
    return points[:5]


def analyze_jobs(jobs, preferences, config, progress_callback=None):
    """
    Analyze and score all jobs. Uses Ollama if available, falls back to keywords.
    Returns list of jobs enriched with relevance_score, remote_status,
    company_type, skills, and application_email.
    """
    use_ollama = config.get("scoring", {}).get("use_ollama", True)
    min_score = config.get("scoring", {}).get("min_relevance_score", 65)
    ollama_available = False

    if use_ollama:
        try:
            import ollama as ollama_client
            # Check that both Ollama is running AND the model exists
            model = config.get("scoring", {}).get("ollama_model", "mistral")
            models = ollama_client.list()
            model_names = [m.model.split(":")[0] for m in models.models] if hasattr(models, "models") else []
            if model in model_names:
                ollama_available = True
                logger.info("Ollama is available with model '%s', using AI-based scoring", model)
            else:
                logger.warning(
                    "Ollama is running but model '%s' not found (available: %s). "
                    "Using keyword-based scoring. Run 'ollama pull %s' to enable AI scoring.",
                    model, model_names, model,
                )
        except Exception:
            logger.warning("Ollama is not available, using keyword-based scoring fallback")

    analyzed = []
    total = len(jobs)

    for i, job in enumerate(jobs):
        text = " ".join([
            job.get("role", ""),
            job.get("job_description", ""),
            job.get("location", ""),
        ])

        # Try Ollama first, fall back to keywords
        if ollama_available:
            result = ollama_score(job, preferences, config)
            if result:
                job["relevance_score"] = min(max(int(result.get("score", 0)), 0), 100)
                job["remote_status"] = result.get("remote_status", detect_remote_status(text))
                job["company_type"] = result.get("company_type", detect_company_type(text))
            else:
                # Ollama failed for this job, use keywords
                job["relevance_score"] = keyword_score(job, preferences)
                job["remote_status"] = detect_remote_status(text)
                job["company_type"] = detect_company_type(text)
        else:
            job["relevance_score"] = keyword_score(job, preferences)
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
            model="google/gemma-3-27b-it:free",
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
    r"Credit": "Credit",
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
    r"Cloud": "Cloud",
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
}

# Backward-compatible alias used by parse_cv_text()
_CV_SKILL_PATTERNS = _SKILL_PATTERNS


def parse_cv_text(text):
    """
    Parse raw CV text and extract structured data.

    Args:
        text: Raw text content of the CV

    Returns:
        dict with keys: skills (list), raw_text (str), uploaded_at (str)
    """
    if not text or not text.strip():
        return {"skills": [], "raw_text": text or "", "uploaded_at": _datetime.now().isoformat()}

    found_skills = []
    for pattern, display in _CV_SKILL_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            if display not in found_skills:
                found_skills.append(display)

    return {
        "skills": found_skills,
        "raw_text": text,
        "uploaded_at": _datetime.now().isoformat(),
    }


def load_cv_data():
    """Load stored CV data from cv_data.json. Returns None if not uploaded yet."""
    if not os.path.exists(CV_DATA_PATH):
        return None
    try:
        with open(CV_DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_cv_data(cv_data):
    """Save CV data dict to cv_data.json."""
    with open(CV_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(cv_data, f, indent=2)


def cv_score(job, cv_data):
    """
    Score a job 0-100 based on how well the applicant's CV matches the JD.

    Args:
        job: dict with role, job_description, location fields
        cv_data: dict from parse_cv_text(), or None if no CV uploaded

    Returns:
        int 0-100
    """
    if not cv_data:
        return 0

    cv_skills_lower = {s.lower() for s in cv_data.get("skills", [])}
    if not cv_skills_lower:
        return 0

    jd_text = " ".join([job.get("role", ""), job.get("job_description", "")])
    jd_skills = extract_skills(jd_text, max_skills=20)

    if not jd_skills:
        # If no job description, there is nothing to score against the CV
        if not job.get("job_description", "").strip():
            return 0
        # JD exists but no specific skills extracted — fall back to word overlap
        jd_words = set(re.findall(r'\b\w{4,}\b', jd_text.lower()))
        cv_words = set(re.findall(r'\b\w{4,}\b', cv_data.get("raw_text", "").lower()))
        common = jd_words & cv_words
        if not jd_words:
            return 0
        return min(int(len(common) / len(jd_words) * 100), 100)

    jd_skills_lower = [s.lower() for s in jd_skills]
    matched = [s for s in jd_skills_lower if s in cv_skills_lower]
    score = int(len(matched) / len(jd_skills_lower) * 100)
    return min(score, 100)


# Curated tips for common missing skills
SKILL_TIPS = {
    "python": "Take a free Python for Data Analysis course on Kaggle (2-3 days). Focus on pandas.",
    "sql": "You likely have SQL from banking work — emphasize this explicitly in your CV.",
    "figma": "Complete Figma basics on YouTube (1 day). Add 'basic Figma' to your skills section.",
    "kafka": "Frame your banking messaging/event systems experience as equivalent. Add a note in your cover letter.",
    "kubernetes": "Note your exposure to cloud infrastructure from banking IT projects.",
    "docker": "Mention any containerization or DevOps exposure. A 2-hour intro tutorial covers basics.",
    "machine learning": "Highlight any analytics or predictive modelling work from banking.",
    "react": "Note your familiarity with web product decisions if you've worked with frontend teams.",
    "javascript": "As a PM, familiarity (not proficiency) is sufficient. Mention product decisions around JS-heavy features.",
    "aws": "Highlight any cloud migration or AWS-based projects from your banking background.",
    "a/b testing": "Emphasize any data-driven experiments or hypothesis testing from your banking role.",
    "user research": "Frame any customer interviews, NPS analysis, or journey mapping work you've done.",
    "agile": "If you have this, make it explicit with specific examples of sprints, stand-ups, retrospectives.",
    "data analysis": "Quantify your analytics work — rows analyzed, reports built, decisions influenced.",
    "tableau": "Free Tableau Public is available. Even basic dashboards count — add to skills.",
    "jira": "Mention any project tracking tools used in banking (Jira, ServiceNow, etc.).",
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
