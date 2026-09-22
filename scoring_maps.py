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
    {"program manager", "programme manager", "project manager"},
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

_DOMAIN_TABLES = {"bfsi": BFSI_TERMS, "ai": AI_TERMS}

_PATTERN_CACHE = {}


_SORTED_CACHE = {}


def domain_terms(table):
    """Return the flat term set for a domain band ('bfsi' or 'ai')."""
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
