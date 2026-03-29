"""
agent/nodes.py — LangGraph node functions for the job outreach agent.
Each function takes AgentState and returns a partial AgentState update.
"""

import logging
from agent.llm import call_llm_json
from agent.state import AgentState
import os as _os

logger = logging.getLogger(__name__)

_PROMPT_PATH = _os.path.join(
    _os.path.dirname(__file__), "..", "autoresearch", "scoring_prompt.md"
)

def _load_scoring_prompt(role, company, jd, cv_skills, cv_summary):
    """Load scoring prompt template from file and interpolate variables."""
    try:
        with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
            template = f.read()
        return template.format(
            role=role, company=company, jd=jd,
            cv_skills=cv_skills, cv_summary=cv_summary
        )
    except Exception:
        # Fallback to hardcoded if file missing
        return f"""Score this job against the candidate's CV on a scale of 0-100.

JOB:
Role: {role}
Company: {company}
Description: {jd}

CANDIDATE CV SUMMARY:
Skills: {cv_skills}
Background: {cv_summary}

Return ONLY valid JSON in this exact format:
{{"score": <integer 0-100>, "reason": "<one sentence why this is or isn't a good fit>"}}\""""

# Score threshold — jobs below this won't be processed
DEFAULT_SCORE_THRESHOLD = 50

# Max jobs fetched per agent run (configurable via preferences)
DEFAULT_JOB_CAP = 200


def fetch_fresh_jobs(state: AgentState) -> dict:
    """
    Fetch jobs from DB that were scraped in the last 24 hours.
    Cap is configurable via preferences (default 200).
    """
    from database import get_connection
    from datetime import datetime, timedelta

    cap = int(state.get("preferences", {}).get("agent_job_cap", DEFAULT_JOB_CAP))
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT * FROM job_listings
           WHERE date_found >= ?
             AND (hidden = 0 OR hidden IS NULL)
           ORDER BY relevance_score DESC
           LIMIT ?""",
        (cutoff, cap)
    )
    jobs = [dict(r) for r in cursor.fetchall()]
    conn.close()
    logger.info("fetch_fresh_jobs: found %d jobs from last 24h (cap=%d)", len(jobs), cap)
    return {"jobs": jobs}


def llm_score_and_filter(state: AgentState) -> dict:
    """
    Score each job against the user's CV using the LLM.
    Attaches llm_score (0-100) and llm_reason to each job.
    Filters to jobs above DEFAULT_SCORE_THRESHOLD.
    """
    from analyzer import load_cv_data

    cv_data = load_cv_data()
    if not cv_data:
        logger.warning("No CV uploaded — skipping LLM scoring")
        return {"scored_jobs": [], "shortlisted": []}

    # Build a compact CV summary for the prompt (avoid token bloat)
    cv_skills = ", ".join((cv_data.get("skills") or [])[:20])
    cv_summary = (cv_data.get("raw_text") or "")[:600]

    scored = []
    threshold = state.get("preferences", {}).get("agent_score_threshold", DEFAULT_SCORE_THRESHOLD)

    for job in state["jobs"]:
        jd = (job.get("job_description") or "")[:800]
        role = job.get("role", "")
        company = job.get("company", "")

        prompt = _load_scoring_prompt(role, company, jd, cv_skills, cv_summary)

        try:
            result = call_llm_json(prompt)
        except Exception as e:
            logger.warning("LLM scoring failed for '%s' @ %s: %s", role, company, e)
            result = {}
        try:
            score = int(result.get("score", 0))
        except (ValueError, TypeError):
            score = 0
        reason = result.get("reason", "")

        job = dict(job)
        job["llm_score"] = score
        job["llm_reason"] = reason
        scored.append(job)
        logger.debug("Scored '%s' @ %s: %d", role, company, score)

    shortlisted = [j for j in scored if j["llm_score"] >= threshold]
    logger.info("llm_score_and_filter: %d scored, %d above threshold %d",
                len(scored), len(shortlisted), threshold)
    return {"scored_jobs": scored, "shortlisted": shortlisted}


def deduplicate(state: AgentState) -> dict:
    """
    Remove jobs already present in outreach_queue (any status).
    Ensures each job is shown to the user exactly once.
    """
    from database import is_job_processed

    fresh = [j for j in state["shortlisted"] if not is_job_processed(j["job_id"])]
    skipped = len(state["shortlisted"]) - len(fresh)
    if skipped:
        logger.info("deduplicate: removed %d already-processed jobs", skipped)
    return {"shortlisted": fresh}


def find_hiring_managers(state: AgentState) -> dict:
    """
    For each shortlisted job, resolve a hiring manager name + email.
    Priority:
      1. poster_email / poster_name already scraped from the job portal
      2. Apollo API lookup (if api_key configured)
    Jobs without a contact are still kept — draft will say "Hi there".
    """
    import os
    import requests as _req

    api_key = state.get("preferences", {}).get("apollo_api_key", "")
    if not api_key:
        api_key = os.getenv("APOLLO_API_KEY", "")

    results = []
    for job in state["shortlisted"]:
        job = dict(job)
        company = job.get("company", "")

        # ── Priority 1: use contact info scraped from the portal ──────────────
        scraped_email = (job.get("poster_email") or "").strip()
        scraped_name = (job.get("poster_name") or "").strip()
        scraped_linkedin = (job.get("poster_linkedin") or "").strip()
        if scraped_email:
            job["hiring_manager"] = scraped_name
            job["hm_email"] = scraped_email
            job["hm_linkedin"] = scraped_linkedin
            logger.debug("Using scraped contact for %s: %s <%s>", company, scraped_name, scraped_email)
            results.append(job)
            continue

        # Even without email, keep the LinkedIn profile if available
        job["hm_linkedin"] = scraped_linkedin

        # ── Priority 2: Apollo API ────────────────────────────────────────────
        if not api_key or not company:
            job["hiring_manager"] = scraped_name
            job["hm_email"] = ""
            results.append(job)
            continue

        try:
            resp = _req.post(
                "https://api.apollo.io/v1/people/search",
                headers={"Content-Type": "application/json", "Cache-Control": "no-cache"},
                json={
                    "api_key": api_key,
                    "q_organization_name": company,
                    "person_titles": ["hiring manager", "talent acquisition",
                                      "recruiter", "hr manager", "people operations"],
                    "per_page": 1,
                },
                timeout=15,
            )
            data = resp.json()
            people = data.get("people", [])
            if people:
                person = people[0]
                name = f"{person.get('first_name','')} {person.get('last_name','')}".strip()
                email = person.get("email", "")
                linkedin = person.get("linkedin_url", "")
                job["hiring_manager"] = name
                job["hm_email"] = email
                job["hm_linkedin"] = job.get("hm_linkedin") or linkedin
                logger.info("Apollo: found %s <%s> at %s", name, email, company)
            else:
                job["hiring_manager"] = scraped_name
                job["hm_email"] = ""
                logger.debug("Apollo: no contact found for %s", company)
        except Exception as e:
            logger.warning("Apollo lookup failed for %s: %s", company, e)
            job["hiring_manager"] = scraped_name
            job["hm_email"] = ""

        results.append(job)

    return {"with_contacts": results}


def draft_outreach(state: AgentState) -> dict:
    """
    For each job with contact info, draft a cold email (~150 words)
    and a LinkedIn message (~50 words) using the LLM.
    """
    from analyzer import load_cv_data

    cv_data = load_cv_data() or {}
    cv_skills = ", ".join((cv_data.get("skills") or [])[:15])
    cv_summary = (cv_data.get("raw_text") or "")[:500]

    drafted = []
    for job in state["with_contacts"]:
        job = dict(job)
        role = job.get("role", "the role")
        company = job.get("company", "your company")
        jd = (job.get("job_description") or "")[:600]
        hm_name = (job.get("hiring_manager") or "").strip()
        _generic_words = {"hiring", "manager", "recruiter", "hr", "talent", "acquisition", "people", "team"}
        _first_word = hm_name.split()[0].lower() if hm_name else ""
        hm_first = hm_name.split()[0] if (hm_name and _first_word not in _generic_words) else "there"
        score_reason = job.get("llm_reason", "")

        prompt = f"""Write a personalized cold email and LinkedIn message for this job application.

ROLE: {role} at {company}
JOB DESCRIPTION EXCERPT: {jd}
FIT REASON: {score_reason}

CANDIDATE BACKGROUND:
Skills: {cv_skills}
Summary: {cv_summary}

Rules:
- Cold email: 120-150 words, professional but warm, mention specific role + company
- LinkedIn message: 40-55 words, conversational, reference the role
- Address the person as "{hm_first}"
- Do NOT use phrases like "I hope this email finds you well"
- Do NOT include subject line in the email body

Return ONLY valid JSON:
{{
  "email": "<full cold email body, no subject line>",
  "linkedin": "<linkedin message>"
}}"""

        try:
            result = call_llm_json(prompt)
        except Exception as e:
            logger.warning("LLM drafting failed for '%s' @ %s: %s", role, company, e)
            result = {}
        job["email_draft"] = result.get("email", f"Hi {hm_first},\n\nI wanted to reach out about the {role} position at {company}. My background in {cv_skills[:80]} makes me a strong fit.\n\nWould love to connect.\n\nBest regards")
        job["linkedin_draft"] = result.get("linkedin", f"Hi {hm_first}, I noticed the {role} opening at {company} and would love to connect. My background in {cv_skills[:60]} aligns well with what you're looking for.")
        drafted.append(job)
        logger.info("Drafted outreach for '%s' @ %s", role, company)

    return {"drafted": drafted}


def queue_for_approval(state: AgentState) -> dict:
    """
    Save each drafted job to outreach_queue with status=pending.
    Generates a unique approval_token per job.
    """
    import uuid
    from database import insert_outreach_draft

    count = 0
    for job in state["drafted"]:
        token = uuid.uuid4().hex
        insert_outreach_draft(
            job_id=job["job_id"],
            company=job.get("company", ""),
            role=job.get("role", ""),
            hiring_manager=job.get("hiring_manager", ""),
            hm_email=job.get("hm_email", ""),
            hm_linkedin=job.get("hm_linkedin", ""),
            email_draft=job.get("email_draft", ""),
            linkedin_draft=job.get("linkedin_draft", ""),
            approval_token=token,
            llm_score=job.get("llm_score", 0),
            llm_reason=job.get("llm_reason", ""),
            apply_url=job.get("apply_url", ""),
        )
        job["approval_token"] = token
        count += 1

    logger.info("queue_for_approval: queued %d drafts", count)
    return {"queued_count": count, "drafted": state["drafted"]}


def send_approval_digest(state: AgentState) -> dict:
    """
    Send a single approval email to the user listing all drafted outreach.
    Each job has APPROVE and SKIP links using the approval_token.
    """
    import html as _html
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    if not state["drafted"]:
        logger.info("send_approval_digest: nothing to send")
        return {}

    prefs = state.get("preferences", {})
    gmail_address = prefs.get("gmail_address", "")
    gmail_password = prefs.get("gmail_app_password", "")

    if not gmail_address or not gmail_password:
        logger.warning("Gmail not configured — skipping approval digest")
        return {}

    # Collect recipients: all enabled reminder emails + fallback to gmail_address
    import json as _json
    import os as _os
    recipients = []
    try:
        reminders_path = _os.path.join(
            _os.path.dirname(__file__), "..", "reminders.json"
        )
        with open(reminders_path) as f:
            reminders = _json.load(f)
        for r in reminders:
            email = (r.get("email") or "").strip()
            if email and r.get("enabled", True) and email not in recipients:
                recipients.append(email)
    except Exception as e:
        logger.warning("Could not load reminders for digest recipients: %s", e)
    if not recipients:
        recipients = [prefs.get("email_recipient", gmail_address) or gmail_address]

    # Determine base URL for approve/skip links
    host = prefs.get("agent_host", "http://localhost:5001")

    # Build HTML email
    jobs_html = ""
    for i, job in enumerate(state["drafted"], 1):
        token = job.get("approval_token", "")
        approve_url = f"{host}/api/approve/{token}"
        skip_url = f"{host}/api/skip/{token}"
        apply_url = (job.get("apply_url") or "").strip()
        hm_email = (job.get("hm_email") or "").strip()
        hm_linkedin = (job.get("hm_linkedin") or "").strip()
        hm_name = (job.get("hiring_manager") or "").strip()
        email_draft = _html.escape(job.get("email_draft", "")).replace("\n", "<br>")
        linkedin_draft = _html.escape(job.get("linkedin_draft", "")).replace("\n", "<br>")

        job_link_html = (
            f'<a href="{_html.escape(apply_url)}" style="color:#2563eb; font-size:12px;">View Job Posting ↗</a>'
            if apply_url else ""
        )
        contact_line = ""
        if hm_email:
            contact_line = f'📧 {_html.escape(hm_email)}'
            if hm_linkedin:
                contact_line += f' · <a href="{_html.escape(hm_linkedin)}" style="color:#0a66c2;">LinkedIn Profile ↗</a>'
        elif hm_linkedin:
            contact_line = f'<a href="{_html.escape(hm_linkedin)}" style="color:#0a66c2; font-weight:600;">🔗 LinkedIn Profile — use message draft below ↗</a>'
        else:
            role_q = _html.escape(job.get("role", "").replace(" ", "%20"))
            company_q = _html.escape(job.get("company", "").replace(" ", "%20"))
            li_search = f"https://www.linkedin.com/search/results/people/?keywords={role_q}%20{company_q}%20recruiter"
            contact_line = f'<a href="{li_search}" style="color:#0a66c2;">🔍 Search Hiring Manager on LinkedIn ↗</a>'

        jobs_html += f"""
        <div style="border:1px solid #e4e4e7; border-radius:8px; padding:16px; margin-bottom:20px; font-family:sans-serif;">
          <h3 style="margin:0 0 4px 0; color:#111;">{i}. {_html.escape(job.get('role',''))} @ {_html.escape(job.get('company',''))}
            <span style="font-size:13px; color:#555; font-weight:normal;">[Score: {job.get('llm_score',0)}]</span>
          </h3>
          {'<p style="margin:0 0 2px 0; color:#374151; font-size:13px;">Contact: ' + _html.escape(hm_name) + '</p>' if hm_name else ''}
          <p style="margin:0 0 4px 0; font-size:13px;">{contact_line}</p>
          <p style="margin:0 0 4px 0; color:#555; font-size:12px;">{_html.escape(job.get('llm_reason',''))}</p>
          <p style="margin:0 0 12px 0;">{job_link_html}</p>

          <p style="font-size:12px; font-weight:600; color:#374151; margin-bottom:4px;">COLD EMAIL DRAFT:</p>
          <div style="background:#f9fafb; border-radius:6px; padding:12px; font-size:13px; color:#374151; margin-bottom:12px;">{email_draft}</div>

          <p style="font-size:12px; font-weight:600; color:#374151; margin-bottom:4px;">LINKEDIN MESSAGE DRAFT:</p>
          <div style="background:#f0f9ff; border-radius:6px; padding:12px; font-size:13px; color:#374151; margin-bottom:16px;">{linkedin_draft}</div>

          <a href="{approve_url}" style="background:#18181b; color:#fff; padding:8px 18px; border-radius:6px; text-decoration:none; font-size:13px; font-weight:600; margin-right:8px;">✅ APPROVE &amp; SEND</a>
          <a href="{skip_url}" style="background:#f1f5f9; color:#475569; padding:8px 18px; border-radius:6px; text-decoration:none; font-size:13px; font-weight:600;">❌ SKIP</a>
        </div>"""

    html = f"""
    <html><body style="font-family:sans-serif; max-width:700px; margin:0 auto; padding:20px;">
      <h2 style="color:#111;">🤖 Job Agent — {len(state['drafted'])} outreach draft{'s' if len(state['drafted'])>1 else ''} ready</h2>
      <p style="color:#555;">Review and approve the cold emails below. Clicking Approve will send the email immediately.</p>
      {jobs_html}
      <p style="color:#999; font-size:12px; margin-top:24px;">LinkedIn message drafts are saved in your <a href="{host}/outbox">Outbox</a> — copy-paste them manually.</p>
    </body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🤖 Job Agent — {len(state['drafted'])} outreach drafts ready for approval"
        msg["From"] = gmail_address
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(gmail_address, gmail_password)
            smtp.sendmail(gmail_address, recipients, msg.as_string())

        logger.info("send_approval_digest: sent to %s (%d drafts)", recipients, len(state["drafted"]))
    except Exception as e:
        logger.error("send_approval_digest failed: %s", e)

    return {}
