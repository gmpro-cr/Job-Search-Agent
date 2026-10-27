"""Tests for contact_scraper.py."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from contact_scraper import extract_contacts_from_text, enrich_jobs_with_contacts


def test_extract_email_from_jd():
    text = "Contact us at hr@razorpay.com or careers@company.io for questions."
    emails, linkedin_urls = extract_contacts_from_text(text)
    assert "hr@razorpay.com" in emails
    assert "careers@company.io" in emails


def test_extract_linkedin_from_jd():
    text = "Connect with our recruiter at linkedin.com/in/jane-recruiter for more info."
    emails, linkedin_urls = extract_contacts_from_text(text)
    assert any("jane-recruiter" in url for url in linkedin_urls)


def test_extract_no_contacts():
    text = "We are hiring a senior product manager. Apply on our website."
    emails, linkedin_urls = extract_contacts_from_text(text)
    assert emails == []
    assert linkedin_urls == []


def test_enrich_returns_dict():
    jobs = [{"job_id": "abc123", "company": "TestCo", "job_description": "Contact hr@testco.com"}]
    result = enrich_jobs_with_contacts(jobs)
    assert isinstance(result, dict)
    assert "abc123" in result
    assert result["abc123"]["poster_email"] == "hr@testco.com"


def test_enrich_labels_job_post_source():
    jobs = [{"job_id": "j1", "company": "TestCo", "job_description": "Share your CV at priya@testco.com"}]
    result = enrich_jobs_with_contacts(jobs)
    assert result["j1"]["poster_email"] == "priya@testco.com"
    assert result["j1"]["contact_source"] == "job_post"


def test_enrich_linkedin_only_has_no_email_source():
    jobs = [{"job_id": "j2", "company": "TestCo",
             "job_description": "Reach our recruiter at linkedin.com/in/jane-recruiter"}]
    result = enrich_jobs_with_contacts(jobs)
    assert result["j2"]["poster_email"] == ""
    assert result["j2"]["contact_source"] == ""
    assert "jane-recruiter" in result["j2"]["poster_linkedin"]


def test_recruiting_inboxes_keeps_only_company_hiring_inboxes():
    from contact_scraper import recruiting_inboxes
    found = ["careers@acme.com", "hr.india@acme.com", "press@acme.com",
             "support@acme.com", "careers@vendor.com", "talent@jobs.acme.com"]
    kept = recruiting_inboxes(found, "www.acme.com")
    assert kept == ["careers@acme.com", "hr.india@acme.com", "talent@jobs.acme.com"]


def test_recruiting_inboxes_needs_a_domain():
    from contact_scraper import recruiting_inboxes
    assert recruiting_inboxes(["careers@acme.com"], "") == []


def test_linkedin_full_jd_ignores_non_job_urls():
    from contact_scraper import fetch_linkedin_full_jd
    assert fetch_linkedin_full_jd("https://www.linkedin.com/in/someone") == ""
    assert fetch_linkedin_full_jd("") == ""
