from scoring_maps import canonical_terms, expand_terms


def test_title_synonyms_expand():
    assert "product manager" in expand_terms("PM", "title")
    assert "product manager" in expand_terms("Product Owner", "title")


def test_industry_synonyms_expand():
    assert "fintech" in expand_terms("NBFC", "industry")
    assert "lending" in expand_terms("fintech", "industry")


def test_unknown_term_returns_itself():
    assert expand_terms("astronaut", "title") == {"astronaut"}


def test_canonical_terms_combines():
    out = canonical_terms(["PM", "Credit Analyst"], "title")
    assert "product manager" in out and "underwriting" in out
