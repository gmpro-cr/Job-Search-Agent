"""Curated synonym/alias clusters for deterministic scoring. Editable by hand.

Each cluster is a set of equivalent terms (lowercase). Membership is symmetric:
any term in a cluster expands to the whole cluster.
"""

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
