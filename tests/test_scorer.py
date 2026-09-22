import numpy as np
from scorer import deterministic_score, score_job

PREFS = {"job_titles": ["Product Manager"], "locations": ["Pune"],
         "industries": ["Fintech"], "transferable_skills": ["Stakeholder Management"]}
CV = {"skills": ["Product Strategy", "A/B Testing", "Stakeholder Management"]}


def _job(role, jd="", loc="Pune", remote="hybrid"):
    return {"role": role, "job_description": jd, "location": loc, "remote_status": remote}


def test_exact_title_strong_score():
    """Title band tops out at 30 under the BFSI/AI weighting."""
    s, bd = deterministic_score(
        _job("Product Manager", "We need product strategy and A/B testing."), CV, PREFS)
    assert bd["title"] == 30


def test_strong_title_plus_domain_clears_threshold():
    """A title match alone no longer clears the 55-point digest cutoff — it
    needs a BFSI or AI signal too. That is the point of the reweighting."""
    no_domain, _ = deterministic_score(
        _job("Product Manager", "We need product strategy and A/B testing."), CV, PREFS)
    with_domain, _ = deterministic_score(
        _job("Product Manager",
             "We need product strategy and A/B testing. "
             "Experience in credit risk and lending required."), CV, PREFS)
    assert no_domain < 55 <= with_domain


def test_synonym_title_matches():
    s, bd = deterministic_score(_job("Product Owner"), CV, PREFS)
    assert bd["title"] >= 30


def test_generic_industry_bonus():
    """Preferred sectors outside BFSI/AI still score the small generic band."""
    prefs = dict(PREFS, industries=["SaaS"])
    s, bd = deterministic_score(
        _job("Product Manager", "B2B SaaS enterprise software platform"), CV, prefs)
    assert bd["domain"] == 5


def test_bfsi_not_double_counted_in_generic_band():
    """Fintech scores the BFSI band, not BFSI + generic domain."""
    prefs = dict(PREFS, industries=["Fintech"])
    s, bd = deterministic_score(
        _job("Product Manager", "We are a fintech company."), CV, prefs)
    assert bd["bfsi"] > 0 and bd["domain"] == 0


def test_negation_blocks_false_title_match():
    s, bd = deterministic_score(
        _job("Sales Rep", "No product management experience required"), CV, PREFS)
    assert bd["title"] == 0


def test_irrelevant_domain_gate():
    s, bd = deterministic_score(_job("Registered Nurse", "ICU nursing"), CV, PREFS)
    assert bd.get("irrelevant") is True and s == 0


def test_blend_uses_both_components():
    jv = np.array([1.0, 0.0], dtype=np.float32)
    pv = np.array([1.0, 0.0], dtype=np.float32)   # cosine 1.0 -> semantic 100
    s, bd = score_job(_job("Product Manager", "product strategy"), CV, PREFS,
                      job_vec=jv, profile_vec=pv)
    assert bd["semantic"] == 100 and bd["deterministic"] > 0
    assert bd["blended"] == s and 0 <= s <= 100
    # Semantic lifts but never sinks the deterministic score.
    assert s >= bd["deterministic"]


def test_low_semantic_never_sinks_deterministic():
    jv = np.array([1.0, 0.0], dtype=np.float32)
    pv = np.array([0.0, 1.0], dtype=np.float32)   # cosine 0 -> semantic 0
    s, bd = score_job(_job("Product Manager", "product strategy"), CV, PREFS,
                      job_vec=jv, profile_vec=pv)
    assert s == bd["deterministic"]   # no lift, but no penalty either


def test_missing_vectors_is_deterministic_only():
    # No vectors -> deterministic score at full scale (not deflated by weight).
    s, bd = score_job(_job("Product Manager"), CV, PREFS, job_vec=None, profile_vec=None)
    assert bd["semantic"] == 0 and s == bd["deterministic"]


def test_irrelevant_gate_overrides_semantic():
    jv = pv = np.array([1.0, 0.0], dtype=np.float32)
    s, bd = score_job(_job("Registered Nurse", "ICU"), CV, PREFS, job_vec=jv, profile_vec=pv)
    assert s == 0   # gate wins despite perfect semantic similarity


# --- BFSI / AI background bands ----------------------------------------------

def test_bfsi_required_scores_full_band():
    s, bd = deterministic_score(
        _job("Product Manager", "Must have experience in credit risk and lending."), CV, PREFS)
    assert bd["bfsi"] == 20
    assert bd["no_domain_penalty"] == 0


def test_bfsi_sector_only_scores_half_band():
    s, bd = deterministic_score(
        _job("Product Manager", "We are an NBFC building a loan product."), CV, PREFS)
    assert bd["bfsi"] == 10


def test_ai_required_scores_full_band():
    s, bd = deterministic_score(
        _job("Product Manager", "Knowledge of LLM evaluation and RAG is required."), CV, PREFS)
    assert bd["ai"] == 20


def test_ai_sector_only_scores_half_band():
    s, bd = deterministic_score(
        _job("Product Manager", "We are a generative AI startup."), CV, PREFS)
    assert bd["ai"] == 10


def test_both_bands_stack():
    s, bd = deterministic_score(
        _job("Product Manager",
             "Experience in lending required. Familiar with LLM and prompt engineering."),
        CV, PREFS)
    assert bd["bfsi"] == 20 and bd["ai"] == 20
    assert s >= 80


def test_no_domain_signal_is_penalised_not_zeroed():
    s, bd = deterministic_score(
        _job("Product Manager", "Own the roadmap for our travel booking app."), CV, PREFS)
    assert bd["no_domain_penalty"] == -15
    assert s > 0          # demoted, not gated


def test_substring_false_positive_does_not_score_ai():
    """Regression: bare "ai"/"ml" used to substring-match email/retail/html."""
    s, bd = deterministic_score(
        _job("Product Manager", "Email marketing for retail. Available now. HTML templates."),
        CV, PREFS)
    assert bd["ai"] == 0


def test_negation_blocks_band_credit():
    s, bd = deterministic_score(
        _job("Product Manager", "No banking experience required for this role."), CV, PREFS)
    assert bd["bfsi"] == 0


def test_bands_expose_evidence_terms():
    s, bd = deterministic_score(
        _job("Product Manager", "Experience in underwriting. Familiar with RAG pipelines."),
        CV, PREFS)
    assert bd.get("bfsi_match") and bd.get("ai_match")


def test_seniority_no_longer_scored():
    """Seniority grading was removed; senior titles are no longer demoted."""
    s, bd = deterministic_score(
        _job("Director of Product", "15+ years required in banking"), CV, PREFS)
    assert "seniority_penalty" not in bd
    assert "experience" not in bd
