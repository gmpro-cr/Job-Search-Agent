import numpy as np
from scorer import deterministic_score, score_job

PREFS = {"job_titles": ["Product Manager"], "locations": ["Pune"],
         "industries": ["Fintech"], "transferable_skills": ["Stakeholder Management"]}
CV = {"skills": ["Product Strategy", "A/B Testing", "Stakeholder Management"]}


def _job(role, jd="", loc="Pune", remote="hybrid"):
    return {"role": role, "job_description": jd, "location": loc, "remote_status": remote}


def test_exact_title_strong_score():
    s, bd = deterministic_score(
        _job("Product Manager", "We need product strategy and A/B testing."), CV, PREFS)
    assert bd["title"] >= 40 and s >= 70


def test_synonym_title_matches():
    s, bd = deterministic_score(_job("Product Owner"), CV, PREFS)
    assert bd["title"] >= 30


def test_industry_synonym_bonus():
    s, bd = deterministic_score(_job("Product Manager", "Lending and NBFC platform"), CV, PREFS)
    assert bd["domain"] > 0


def test_seniority_gate_penalizes_director():
    s, bd = deterministic_score(_job("Director of Product", "15+ years required"), CV, PREFS)
    assert bd["seniority_penalty"] < 0


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


def test_missing_vectors_is_deterministic_only():
    s, bd = score_job(_job("Product Manager"), CV, PREFS, job_vec=None, profile_vec=None)
    assert bd["semantic"] == 0 and s == round(0.55 * bd["deterministic"])


def test_irrelevant_gate_overrides_semantic():
    jv = pv = np.array([1.0, 0.0], dtype=np.float32)
    s, bd = score_job(_job("Registered Nurse", "ICU"), CV, PREFS, job_vec=jv, profile_vec=pv)
    assert s == 0   # gate wins despite perfect semantic similarity
