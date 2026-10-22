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


def test_irrelevant_gate_ignores_jd_body():
    """Regression: the gate scanned the whole JD, so a bank PM role was
    hard-zeroed by "lunches prepared by in-house chefs" in the perks list."""
    s, bd = deterministic_score(
        _job("Senior Product Manager (UK Cards)",
             "Own the cards roadmap. Perks: complimentary lunches prepared by "
             "in-house chefs. Experience in banking required."), CV, PREFS)
    assert not bd.get("irrelevant")
    assert s > 0 and bd["bfsi"] > 0


def test_irrelevant_gate_ignores_foreign_language_false_friend():
    """"Chef de projet" is French for project manager, not a cook."""
    s, bd = deterministic_score(
        _job("Product owner", "Minimum 5 ans dans un poste similaire (Chef de projet)."),
        CV, PREFS)
    assert not bd.get("irrelevant")


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


# --- Scoring profiles ---------------------------------------------------------
from scorer import resolve_scoring_profile, scoring_stamp, SCORER_VERSION  # noqa: E402

PM_PREFS = {"job_titles": ["Project Manager"], "locations": ["Pune", "Remote"],
            "industries": [], "transferable_skills": ["Stakeholder Management"]}
PM_CV = {
    "skills": ["Agile", "Scrum", "JIRA", "Stakeholder Management", "PMP"],
    "raw_text": ("Project Manager with 9 years delivering Agile/Scrum projects. "
                 "PMP and CSM certified. Sprint planning, release management, "
                 "RAID log, resource planning, MS Project. Delivered a core "
                 "banking migration for a bank client."),
}
BFSI_AI_CV = {
    "skills": ["Product Strategy", "LLM", "Credit Risk"],
    "raw_text": ("Product manager, 9 years in credit risk, underwriting and "
                 "lending at an NBFC. Shipped LLM products with RAG and prompt "
                 "engineering; generative AI evals. Agile, Jira."),
}


def test_default_profile_when_cv_gives_no_signal():
    assert resolve_scoring_profile(CV, PREFS) == "bfsi_ai"
    assert resolve_scoring_profile({}, {}) == "bfsi_ai"


def test_detects_project_delivery_cv():
    """A banking client on a PM CV must not pull it into the BFSI profile."""
    assert resolve_scoring_profile(PM_CV, PM_PREFS) == "project_delivery"


def test_detects_bfsi_ai_cv_despite_agile_mentions():
    assert resolve_scoring_profile(BFSI_AI_CV, PREFS) == "bfsi_ai"


def test_explicit_profile_overrides_detection():
    assert resolve_scoring_profile(
        PM_CV, dict(PM_PREFS, scoring_profile="bfsi_ai")) == "bfsi_ai"
    assert resolve_scoring_profile(
        BFSI_AI_CV, dict(PREFS, scoring_profile="project_delivery")) == "project_delivery"
    # Unknown / "auto" fall back to detection.
    assert resolve_scoring_profile(
        PM_CV, dict(PM_PREFS, scoring_profile="auto")) == "project_delivery"


def test_project_manager_role_scores_well_on_delivery_profile():
    """The regression this fixes: a typical IT project-manager JD capped at ~50
    under the BFSI/AI bands, below a routine's min_score, so Purva's routine
    emailed nothing."""
    job = _job("Senior Project Manager",
               "Experience in Agile/Scrum delivery required. PMP preferred. "
               "Own sprint planning, RAID log and stakeholder management.",
               loc="Pune")
    s, bd = deterministic_score(job, PM_CV, PM_PREFS)
    assert bd["profile"] == "project_delivery"
    assert bd["delivery"] == 20 and bd["governance"] > 0
    assert bd["no_domain_penalty"] == 0
    assert "bfsi" not in bd and "ai" not in bd
    assert s >= 70
    bfsi_s, _ = deterministic_score(job, PM_CV, dict(PM_PREFS, scoring_profile="bfsi_ai"))
    assert bfsi_s < 55 <= s


def test_non_it_project_manager_is_demoted():
    """A construction PM role carries no delivery-method signal."""
    s, bd = deterministic_score(
        _job("Project Manager", "Manage site engineers and civil contractors.",
             loc="Pune"), PM_CV, PM_PREFS)
    assert bd["delivery"] == 0 and bd["governance"] == 0
    assert bd["no_domain_penalty"] == -15
    assert s < 55


def test_delivery_band_only_credits_methods_on_the_cv():
    """cv_scoped: a Kanban-only JD earns nothing for a CV that never used Kanban."""
    s, bd = deterministic_score(
        _job("Project Manager", "Kanban experience required."), PM_CV, PM_PREFS)
    assert bd["delivery"] == 0


def test_cv_scoped_band_falls_back_to_full_table_for_thin_cv():
    thin = {"skills": ["x"], "raw_text": ""}
    s, bd = deterministic_score(
        _job("Project Manager", "Kanban experience required."),
        thin, dict(PM_PREFS, scoring_profile="project_delivery"))
    assert bd["delivery"] == 20


def test_breakdown_describes_profile_bands():
    _, bd = deterministic_score(_job("Product Manager"), CV, PREFS)
    assert bd["profile"] == "bfsi_ai"
    assert [b[0] for b in bd["bands"]] == ["bfsi", "ai"]
    assert bd["penalty_label"] == "No BFSI/AI"


def test_scoring_stamp_tracks_version_and_profile():
    assert scoring_stamp(PM_CV, PM_PREFS) == f"{SCORER_VERSION}:project_delivery"
    assert scoring_stamp(CV, PREFS) == f"{SCORER_VERSION}:bfsi_ai"


# --- Fit rules (docs/CANDIDATE_CONTEXT.md) -------------------------------------

FIT_CV = {"raw_text": "AVP Relationship Manager. 9 years in corporate credit.",
          "skills": ["Credit Appraisal", "Stakeholder Management"]}
FIT_PREFS = {"job_titles": ["Product Manager", "Relationship Manager"],
             "locations": ["Pune", "Remote"], "scoring_profile": "bfsi_ai"}


def _fit(role, jd="", loc="Pune", remote="hybrid", **kw):
    job = dict(_job(role, jd, loc=loc, remote=remote), **kw)
    _s, bd = deterministic_score(job, FIT_CV, FIT_PREFS)
    return {a[0]: a[2] for a in bd["adjustments"]}, bd


def test_credit_moat_and_ai_x_bfsi_boost():
    adj, bd = _fit("Product Manager",
                   "Build LLM credit underwriting. Experience in lending required. "
                   "Working capital and DSCR.")
    assert adj["moat"] == 10                      # 3+ moat terms, capped
    assert adj["ai_x_bfsi"] == 5
    assert bd["fit"] == 15


def test_target_company_default_list_and_override():
    adj, _ = _fit("Product Manager", "Fintech lending.", company="Perfios")
    assert adj.get("target_company") == 10
    prefs = dict(FIT_PREFS, target_companies=["Acme"])
    _s, bd = deterministic_score(dict(_job("Product Manager"), company="Perfios"), FIT_CV, prefs)
    assert "target_company" not in {a[0] for a in bd["adjustments"]}


def test_too_senior_title_product_track_only():
    adj, bd = _fit("Director of Product")
    assert adj["seniority"] == -12 and bd["track"] == "product"
    adj, _ = _fit("VP Product")
    assert adj["seniority"] == -12
    # VP / AVP are ordinary banking grades.
    adj, bd = _fit("AVP - Relationship Manager")
    assert "seniority" not in adj and bd["track"] == "banking"


def test_experience_floor_uses_cv_years():
    adj, _ = _fit("Product Manager", experience_min=13)
    assert adj["exp_floor"] == -10
    adj, _ = _fit("Product Manager", experience_min=10)
    assert "exp_floor" not in adj


def test_experience_floor_off_without_candidate_years():
    job = dict(_job("Product Manager"), experience_min=20)
    _s, bd = deterministic_score(job, {"skills": []}, FIT_PREFS)
    assert "exp_floor" not in {a[0] for a in bd["adjustments"]}


def test_pm_tenure_requirement_penalised():
    adj, _ = _fit("Product Manager", "7+ years of product management experience.")
    assert adj["pm_tenure"] == -8
    adj, _ = _fit("Product Manager", "2+ years of product management experience.")
    assert "pm_tenure" not in adj


def test_engineering_role_gets_no_boosts():
    adj, _ = _fit("Machine Learning Engineer",
                  "Credit scoring. Experience in lending required. LLM.", company="Lendingkart")
    assert adj == {"engineering": -20}


def test_retail_mortgage_mismatch():
    adj, _ = _fit("Credit Risk Analyst - Home Loans")
    assert adj["domain_mismatch"] == -10
    adj, _ = _fit("Product Manager", "Mortgage underwriting experience required.")
    assert adj["domain_mismatch"] == -10
    adj, _ = _fit("Product Manager", "We also offer mortgages to customers.")
    assert "domain_mismatch" not in adj


def test_bank_employer_on_product_track_only():
    adj, _ = _fit("Product Manager", company="HDFC Bank")
    assert adj["bank_employer"] == -8
    adj, _ = _fit("Relationship Manager", company="HDFC Bank")
    assert "bank_employer" not in adj


def test_relocation_penalty():
    adj, _ = _fit("Product Manager", loc="Delhi", remote="on-site")
    assert adj["relocation"] == -10
    adj, _ = _fit("Product Manager", loc="Delhi", remote="remote")
    assert "relocation" not in adj
    adj, _ = _fit("Product Manager", loc="Pune", remote="on-site")
    assert "relocation" not in adj


def test_red_flags():
    adj, _ = _fit("Product Manager", poster_email="hr.jobs@gmail.com")
    assert adj["red_flag"] == -15
    adj, _ = _fit("Product Manager", "A registration fee is applicable.")
    assert adj["red_flag"] == -15


def test_fit_is_clamped():
    adj, bd = _fit("Head of Machine Learning Engineer", "15+ years of product management.",
                   loc="Delhi", remote="on-site", experience_min=15,
                   poster_email="x@gmail.com")
    assert sum(adj.values()) < -30 and bd["fit"] == -30


def test_project_delivery_profile_skips_candidate_rules():
    prefs = dict(FIT_PREFS, scoring_profile="project_delivery")
    job = dict(_job("Director, Project Management", loc="Delhi", remote="on-site"),
               company="HDFC Bank", experience_min=20)
    _s, bd = deterministic_score(job, FIT_CV, prefs)
    assert bd["adjustments"] == []


def test_score_job_appends_outcome_lines():
    class Model:
        def adjust(self, job, bd):
            return [["conversion", "Your outcomes", 7, "like x"]]
    job = _job("Product Manager", "Fintech lending.")
    base, _ = score_job(job, FIT_CV, FIT_PREFS)
    s, bd = score_job(job, FIT_CV, FIT_PREFS, outcome_model=Model())
    assert s == min(100, base + 7) and bd["learned"] == 7
    assert bd["adjustments"][-1][0] == "conversion"


def test_scoring_stamp_includes_outcome_fingerprint():
    from scorer import scoring_stamp
    class Model:
        fingerprint = "abc123"
    base = scoring_stamp(FIT_CV, FIT_PREFS)
    assert scoring_stamp(FIT_CV, FIT_PREFS, Model()) == base + ":abc123"
    Model.fingerprint = ""
    assert scoring_stamp(FIT_CV, FIT_PREFS, Model()) == base
