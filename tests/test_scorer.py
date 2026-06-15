from scorer import deterministic_score

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
