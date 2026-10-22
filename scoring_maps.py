"""Curated synonym/alias clusters for deterministic scoring. Editable by hand.

Each cluster is a set of equivalent terms (lowercase). Membership is symmetric:
any term in a cluster expands to the whole cluster.

Domain tables (BFSI, AI) are flat term sets rather than equivalence clusters:
they are matched wholesale against a JD to decide whether the posting carries
that background signal at all. See docs/plans/2026-09-23-bfsi-ai-scoring-design.md.
"""
import re

_TITLE_CLUSTERS = [
    {"product manager", "pm", "product owner", "apm", "associate product manager",
     "senior product manager", "group product manager", "product lead"},
    {"credit analyst", "credit risk", "underwriting", "credit appraisal",
     "credit manager", "risk analyst"},
    {"business analyst", "ba", "business analysis"},
    {"program manager", "programme manager", "project manager",
     "delivery manager", "project lead"},
    {"data analyst", "analytics", "business intelligence"},
]
_SKILL_CLUSTERS = [
    {"a/b testing", "experimentation", "ab testing", "split testing"},
    {"gtm", "go-to-market", "go to market"},
    {"sql", "queries", "data analysis"},
    {"stakeholder management", "stakeholder", "cross-functional"},
]
# Generic industry clusters. BFSI and AI/ML are deliberately NOT here — they
# have dedicated bands in scorer.py and are scored separately and higher.
_INDUSTRY_CLUSTERS = [
    {"saas", "b2b saas", "enterprise software"},
    {"ecommerce", "e-commerce", "retail", "marketplace"},
    {"edtech", "healthtech", "logistics", "travel", "gaming"},
]
_TABLES = {"title": _TITLE_CLUSTERS, "skill": _SKILL_CLUSTERS, "industry": _INDUSTRY_CLUSTERS}

# --- Domain background tables -------------------------------------------------
# Banking, Financial Services & Insurance. Covers sector names, product lines
# and the regulatory/operational vocabulary that only an insider uses.
BFSI_TERMS = {
    "bfsi", "bank", "banking", "core banking", "neobank", "fintech", "nbfc",
    "lending", "loan", "loans", "credit", "credit risk", "credit appraisal",
    "underwriting", "collections", "npa", "delinquency", "portfolio management",
    "treasury", "payments", "upi", "cards", "insurance", "insurtech", "wealth",
    "wealth management", "broking", "mutual fund", "capital markets", "trading",
    "financial services", "kyc", "aml", "basel", "rbi", "sme lending",
    "working capital", "trade finance", "risk management",
}

# AI / ML, weighted towards the applied LLM-product vocabulary on the CV
# rather than research-lab terminology.
AI_TERMS = {
    "ai", "a.i.", "ml", "ai/ml", "artificial intelligence", "machine learning",
    "deep learning", "llm", "llms", "large language model", "genai",
    "generative ai", "gen ai", "rag", "retrieval augmented generation",
    "prompt engineering", "prompt design", "evals", "model evaluation",
    "nlp", "natural language processing", "foundation model", "agentic",
    "ai agents", "fine-tuning", "fine tuning", "embeddings", "vector search",
    "vector database", "computer vision", "mlops", "recommendation engine",
}

# Project / delivery management: how the work is run. Used by the
# "project_delivery" scoring profile for candidates whose differentiator is
# delivery craft rather than a business domain.
DELIVERY_TERMS = {
    "agile", "scrum", "kanban", "scaled agile", "scaled agile framework",
    "sprint", "sprint planning", "backlog grooming", "backlog refinement",
    "retrospectives", "waterfall", "sdlc", "release management",
    "release planning", "jira", "confluence", "azure devops", "ms project",
    "microsoft project", "scrum master", "agile coach", "agile transformation",
    "project delivery", "delivery management", "program delivery",
}

# Project governance and certification vocabulary: the controls side of the job.
GOVERNANCE_TERMS = {
    "pmp", "prince2", "csm", "psm", "capm", "pmi-acp", "safe agilist", "pmo",
    "project governance", "risk register", "raid", "raid log",
    "budget management", "budgeting", "cost control", "resource planning",
    "resource management", "capacity planning", "scope management",
    "change control", "change management", "project plan", "project planning",
    "project scheduling", "gantt", "milestones", "status reporting",
    "steering committee", "statement of work", "vendor management",
    "earned value", "dependency management", "critical path",
}

_DOMAIN_TABLES = {"bfsi": BFSI_TERMS, "ai": AI_TERMS,
                  "delivery": DELIVERY_TERMS, "governance": GOVERNANCE_TERMS}

# --- Fit-rule vocabularies (scorer._fit_adjustments) ---------------------------
# See docs/CANDIDATE_CONTEXT.md and docs/plans/2026-10-21-conversion-scoring-design.md.

# Indian corporate-credit depth: the workflows only an insider has run. A JD
# that names these is asking for exactly the candidate's moat, which BFSI_TERMS
# ("bank", "payments", ...) cannot tell apart from a generic fintech mention.
CREDIT_MOAT_TERMS = {
    "credit appraisal", "credit appraisal memo", "cam", "mpbf", "dscr",
    "drawing power", "sma-0", "npa", "asset reconstruction", "covenant",
    "covenant monitoring", "funds transfer pricing", "ftp pricing",
    "working capital", "underwriting", "credit underwriting",
    "credit decisioning", "risk decisioning", "decisioning", "loan origination",
    "collections", "kyc", "aml", "co-lending", "credit bureau", "credit policy",
    "credit scoring", "msme lending", "sme lending", "supply chain finance",
    "treds", "invoice discounting", "trade finance",
}

# Conversational / voice AI vocabulary; with a BFSI signal it is the Vaani lane.
VOICE_AI_TERMS = {
    "conversational ai", "voice ai", "voice agent", "voice bot", "voicebot",
    "voice banking", "speech-to-text", "speech to text", "text-to-speech",
    "speech recognition", "ivr", "chatbot", "virtual assistant",
}

# Retail-mortgage / consumer-risk work: banking, but the wrong side of it.
DOMAIN_MISMATCH_TERMS = {
    "mortgage", "mortgages", "home loan", "home loans", "housing finance",
    "consumer risk", "retail risk", "retail credit risk", "consumer credit risk",
}

# Titles on the banking track (relationship / credit roles). Everything else
# a bfsi_ai candidate scores is treated as the product track.
BANKING_TRACK_TITLE_RE = re.compile(
    r"\b(relationship manager|credit manager|credit analyst|credit officer|"
    r"underwriter|credit risk manager|credit risk analyst|rm)\b", re.I)

# Very senior titles. VP / AVP are ordinary banking grades (the candidate is an
# AVP), so they only count against the product track.
SENIOR_TITLE_RE = re.compile(r"\b(director|head|chief|cxo|cpo|cto)\b", re.I)
SENIOR_PRODUCT_TITLE_RE = re.compile(
    r"\b(vp|vice president|svp|group product manager|principal product manager)\b", re.I)

# Hands-on engineering / research roles: deep infra or ML engineering.
ENGINEERING_TITLE_RE = re.compile(
    r"\b(software|backend|back-end|frontend|front-end|full[- ]stack|data|ml|"
    r"machine learning|ai|mlops|devops|platform|cloud|research|deep learning|nlp)"
    r"\s+(engineer|developer|scientist|architect)\b|\bdata scientist\b|\bsde\b",
    re.I)

# Asks for N years of formal product-management tenure, either phrasing.
PM_TENURE_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b[^.]{0,40}?product management"
    r"|product management[^.]{0,30}?(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", re.I)

# Recruiters on personal mail, and fee language. "No fee" disclaimers are
# listed because in practice they come from the same consultancies.
PERSONAL_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "yahoo.co.in", "hotmail.com",
                          "outlook.com", "rediffmail.com", "live.com"}
FEE_RED_FLAG_RE = re.compile(
    r"\b(registration|processing|training|security|joining)\s+(fee|fees|deposit|charges)\b"
    r"|\bpay\s+(?:a\s+|the\s+)?fee\b|\bno\s+(?:registration\s+)?fee\b"
    r"|\bwe do not charge\b|\bfree of cost\b", re.I)

# Warm-outreach short list for the BFSI + AI product track, used when the
# candidate has not set preferences["target_companies"]. Lowercase.
DEFAULT_TARGET_COMPANIES = (
    "credgenics", "perfios", "yubi", "bureau.id", "signzy", "setu",
    "lendingkart", "moneyview", "money view", "kreditbee", "cred", "experian",
    "crif", "zest ai", "upstart", "stripe", "plaid", "scale ai", "salesforce",
    "fis",
)

# Every fit rule scorer._fit_adjustments knows. Profiles opt in by name.
ALL_FIT_RULES = frozenset({
    "seniority", "exp_floor", "pm_tenure", "domain_mismatch", "engineering",
    "bank_employer", "relocation", "red_flag", "moat", "ai_x_bfsi", "voice_ai",
    "target_company",
})

# --- Scoring profiles ----------------------------------------------------------
# A profile names the two differentiator bands scored for a candidate. Each
# band is (key, label, domain table, cv_scoped). cv_scoped bands only credit
# terms that also appear in the candidate's own CV, so the band rewards the
# methods the candidate has actually used rather than any methodology at all
# (agile appears in nearly every IT project-manager JD).
SCORING_PROFILES = {
    "bfsi_ai": {
        "label": "BFSI + AI product",
        "bands": (("bfsi", "BFSI Fit", "bfsi", False),
                  ("ai", "AI Fit", "ai", False)),
        "penalty_label": "No BFSI/AI",
        # Fit rules from docs/CANDIDATE_CONTEXT.md: a banking domain expert
        # moving into AI product, plus a separate banking (RM / credit) track.
        "fit_rules": ALL_FIT_RULES,
        "default_targets": DEFAULT_TARGET_COMPANIES,
    },
    "project_delivery": {
        "label": "Project & delivery management",
        "bands": (("delivery", "Delivery Methods", "delivery", True),
                  ("governance", "PM Governance", "governance", True)),
        "penalty_label": "No delivery signal",
        # Only the candidate-neutral rules; target companies apply only when
        # the candidate sets preferences["target_companies"].
        "fit_rules": frozenset({"red_flag", "target_company"}),
        "default_targets": (),
    },
}
DEFAULT_PROFILE = "bfsi_ai"

# Terms too generic to tell profiles apart when reading a CV: a project
# manager's CV says "risk management" and "portfolio management" about
# projects, and IT-services CVs name banking clients.
_DETECT_EXCLUDE = {
    "risk management", "portfolio management", "change management", "credit",
    "cards", "trading", "wealth", "loan", "loans", "bank", "banking",
    "payments", "ai", "ml", "a.i.", "evals", "rag", "budgeting", "milestones",
    "project plan", "project planning", "jira", "confluence",
}


def profile_signal_terms(profile):
    """All terms whose presence in a CV points at `profile`, minus the
    ambiguous ones in _DETECT_EXCLUDE."""
    spec = SCORING_PROFILES.get(profile)
    if not spec:
        return set()
    out = set()
    for _key, _label, table, _scoped in spec["bands"]:
        out |= _DOMAIN_TABLES.get(table, set())
    return out - _DETECT_EXCLUDE

_PATTERN_CACHE = {}


_SORTED_CACHE = {}


def domain_terms(table):
    """Return the flat term set for a domain band (a _DOMAIN_TABLES key)."""
    return _DOMAIN_TABLES.get(table, set())


def domain_terms_ranked(table):
    """Domain terms ordered longest-first, cached.

    Set iteration order is arbitrary, which made the reported evidence term
    non-deterministic. Longest-first is both stable and more informative:
    "credit risk" is reported in preference to "credit".
    """
    cached = _SORTED_CACHE.get(table)
    if cached is None:
        cached = tuple(sorted(_DOMAIN_TABLES.get(table, set()),
                              key=lambda t: (-len(t), t)))
        _SORTED_CACHE[table] = cached
    return cached


def term_pattern(term):
    """Compiled word-boundary regex for `term`, cached.

    Word boundaries matter: the old scorer tested domain hits with a plain
    substring `in`, so the bare token "ai" matched "email", "retail" and
    "available", scoring an AI hit on almost every JD. Terms containing
    non-word edge characters (e.g. "a.i.", "ai/ml") fall back to a plain
    escaped match, since \\b would not behave as intended around them.
    """
    t = (term or "").lower().strip()
    if not t:
        return None
    cached = _PATTERN_CACHE.get(t)
    if cached is not None:
        return cached
    body = re.escape(t)
    prefix = r"\b" if t[0].isalnum() else ""
    suffix = r"\b" if t[-1].isalnum() else ""
    pat = re.compile(prefix + body + suffix, re.I)
    _PATTERN_CACHE[t] = pat
    return pat


def term_in_text(term, text):
    """True when `term` occurs in `text` on word boundaries."""
    if not text:
        return False
    pat = term_pattern(term)
    return bool(pat and pat.search(text))


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
