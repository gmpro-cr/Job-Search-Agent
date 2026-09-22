from scoring_maps import canonical_terms, domain_terms, expand_terms, term_in_text


def test_title_synonyms_expand():
    assert "product manager" in expand_terms("PM", "title")
    assert "product manager" in expand_terms("Product Owner", "title")


def test_bfsi_table_covers_sector_vocabulary():
    """BFSI terms moved out of _INDUSTRY_CLUSTERS into their own scored band."""
    bfsi = domain_terms("bfsi")
    for term in ("fintech", "nbfc", "lending", "credit risk", "underwriting", "rbi"):
        assert term in bfsi


def test_ai_table_covers_llm_vocabulary():
    ai = domain_terms("ai")
    for term in ("llm", "rag", "generative ai", "prompt engineering", "evals"):
        assert term in ai


def test_word_boundary_match_avoids_substring_hits():
    """Regression: bare "ai"/"ml" must not match inside other words."""
    assert not term_in_text("ai", "email marketing for retail, available now")
    assert not term_in_text("ml", "we use html and xml templates")
    assert term_in_text("ai", "we build AI products")
    assert term_in_text("ml", "an ML pipeline")


def test_punctuated_terms_still_match():
    """Terms with non-word edges (a.i., ai/ml) fall back to plain matching."""
    assert term_in_text("ai/ml", "strong ai/ml background")


def test_unknown_term_returns_itself():
    assert expand_terms("astronaut", "title") == {"astronaut"}


def test_canonical_terms_combines():
    out = canonical_terms(["PM", "Credit Analyst"], "title")
    assert "product manager" in out and "underwriting" in out
