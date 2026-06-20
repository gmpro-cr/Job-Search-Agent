"""Tests for CV parsing and scoring functions in analyzer.py."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from analyzer import parse_cv_text, cv_score, compute_gap_analysis


SAMPLE_CV = """
John Doe | Product Manager
Skills: SQL, Python, Agile, Stakeholder Management, Data Analysis, Roadmap Planning
Experience: 8 years in banking and financial services
Led product teams at HDFC Bank, worked with Jira, Tableau
"""

SAMPLE_JD_JOB = {
    "role": "Senior Product Manager",
    "job_description": "We need Python, SQL, Agile, Figma, Kafka experience. "
                       "Roadmap planning and stakeholder management required.",
    "location": "Bangalore",
}


def test_parse_cv_text_extracts_skills():
    result = parse_cv_text(SAMPLE_CV)
    assert "skills" in result
    assert len(result["skills"]) > 0
    # SQL and Python should be detected
    skill_names_lower = [s.lower() for s in result["skills"]]
    assert "sql" in skill_names_lower
    assert "python" in skill_names_lower


def test_parse_cv_text_returns_raw_text():
    result = parse_cv_text(SAMPLE_CV)
    assert result["raw_text"] == SAMPLE_CV


def test_parse_cv_text_empty_string():
    result = parse_cv_text("")
    assert result["skills"] == []


def test_cv_score_returns_0_without_cv():
    score = cv_score(SAMPLE_JD_JOB, None)
    assert score == 0


def test_cv_score_returns_0_to_100():
    cv_data = parse_cv_text(SAMPLE_CV)
    score = cv_score(SAMPLE_JD_JOB, cv_data)
    assert 0 <= score <= 100


def test_cv_score_higher_when_more_skills_match():
    # CV with many matching skills
    rich_cv = parse_cv_text("Skills: Python, SQL, Agile, Figma, Kafka, Roadmap, Stakeholder Management")
    poor_cv = parse_cv_text("Skills: Cooking, Gardening, Photography")

    rich_score = cv_score(SAMPLE_JD_JOB, rich_cv)
    poor_score = cv_score(SAMPLE_JD_JOB, poor_cv)
    assert rich_score > poor_score


def test_compute_gap_analysis_structure():
    cv_data = parse_cv_text(SAMPLE_CV)
    result = compute_gap_analysis(SAMPLE_JD_JOB, cv_data)

    assert "cv_score" in result
    assert "matched_skills" in result
    assert "missing_skills" in result
    assert "action_steps" in result
    assert isinstance(result["matched_skills"], list)
    assert isinstance(result["missing_skills"], list)
    assert isinstance(result["action_steps"], list)


def test_compute_gap_analysis_no_cv():
    result = compute_gap_analysis(SAMPLE_JD_JOB, None)
    assert result["cv_score"] == 0
    assert "Upload your CV" in result["action_steps"][0]


def test_analyze_jobs_no_llm_and_scores():
    """analyze_jobs scores via scorer (no LLM), gates irrelevant roles to 0."""
    from analyzer import analyze_jobs
    prefs = {"job_titles": ["Product Manager"], "locations": ["Pune"],
             "industries": ["Fintech"]}
    cv = {"skills": ["Product Strategy", "Stakeholder Management"]}
    jobs = [
        {"role": "Product Manager", "company": "FinCo",
         "job_description": ("Own the fintech product strategy and roadmap for our "
                             "lending platform. Work cross-functionally with engineering "
                             "and design teams to ship features, run experiments, and "
                             "drive stakeholder management across the credit organisation. "
                             "You will define the product vision, prioritise the backlog, "
                             "analyse user data, and partner with risk and operations to "
                             "launch new lending products to market on a quarterly basis."),
         "apply_url": "https://x.test/1", "location": "Pune", "remote_status": "hybrid"},
        {"role": "Registered Nurse", "company": "Hospital",
         "job_description": ("Provide ICU nursing care for critically ill patients, "
                             "administer medication, monitor vitals, and coordinate with "
                             "physicians on treatment plans across day and night shifts. "
                             "Maintain accurate patient records, operate ventilators and "
                             "monitoring equipment, respond to emergencies, support families, "
                             "and follow strict infection-control and clinical safety "
                             "protocols throughout every shift on the critical care ward."),
         "apply_url": "https://x.test/2", "location": "Pune", "remote_status": ""},
    ]
    config = {"scoring": {"min_relevance_score": 40}}
    qualified, analyzed = analyze_jobs(jobs, prefs, config, cv_data=cv)
    assert all("relevance_score" in j for j in analyzed)
    assert any(j["role"] == "Product Manager" for j in qualified)
    assert all(j["role"] != "Registered Nurse" for j in qualified)


def test_ats_score_is_keyword_driven_and_honest():
    """ATS score is dominated by keyword coverage vs target roles, and never
    fabricates a keyword score when market data is missing."""
    from analyzer import compute_ats_score
    cv = {"raw_text": ("Jane Doe jane@x.com +91 9999999999\nEXPERIENCE Led team grew "
                       "revenue 40% reduced churn 18% launched scaled built\nEDUCATION MBA\nSKILLS"),
          "skills": ["Product Strategy", "SQL", "Agile", "Roadmapping", "A/B Testing",
                     "Stakeholder Management", "Analytics", "PRD"]}
    market = [{"skill": s} for s in ["Product Strategy", "SQL", "Agile", "Roadmapping",
                                     "A/B Testing", "Stakeholder Management"]]
    high = compute_ats_score(cv, {}, market)              # strong coverage
    low_market = [{"skill": s} for s in ["Kubernetes", "Rust", "Solidity", "CUDA",
                                         "Verilog", "COBOL"]]
    low = compute_ats_score(cv, {}, low_market)           # poor coverage
    assert high["score"] > low["score"] + 15, (high["score"], low["score"])
    # keyword component is the largest weight (40)
    kw = next(b for b in high["breakdown"] if "Keyword match" in b["label"])
    assert kw["max"] == 40
    # no market -> keyword component not counted (honest), still returns a score
    no_market = compute_ats_score(cv, {}, [])
    nkw = next(b for b in no_market["breakdown"] if "Keyword match" in b["label"])
    assert nkw["counted"] is False and 0 <= no_market["score"] <= 100
