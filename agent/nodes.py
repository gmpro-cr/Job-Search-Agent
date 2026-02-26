"""
agent/nodes.py — LangGraph node functions for the job outreach agent.
Each function takes AgentState and returns a partial AgentState update.
"""

import logging
from agent.llm import call_llm_json
from agent.state import AgentState

logger = logging.getLogger(__name__)

# Score threshold — jobs below this won't be processed
DEFAULT_SCORE_THRESHOLD = 60


def fetch_fresh_jobs(state: AgentState) -> dict:
    """
    Fetch jobs from DB that were scraped in the last 24 hours.
    Caps at 50 to avoid hammering the LLM on large DB runs.
    """
    from database import get_connection
    from datetime import datetime, timedelta

    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT * FROM job_listings
           WHERE date_found >= ?
             AND (hidden = 0 OR hidden IS NULL)
           ORDER BY relevance_score DESC
           LIMIT 50""",
        (cutoff,)
    )
    jobs = [dict(r) for r in cursor.fetchall()]
    conn.close()
    logger.info("fetch_fresh_jobs: found %d jobs from last 24h", len(jobs))
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

        prompt = f"""Score this job against the candidate's CV on a scale of 0-100.

JOB:
Role: {role}
Company: {company}
Description: {jd}

CANDIDATE CV SUMMARY:
Skills: {cv_skills}
Background: {cv_summary}

Return ONLY valid JSON in this exact format:
{{"score": <integer 0-100>, "reason": "<one sentence why this is or isn't a good fit>"}}"""

        result = call_llm_json(prompt)
        score = int(result.get("score", 0))
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
    For each shortlisted job, find the hiring manager's email via Apollo API.
    Attaches hiring_manager (name) and hm_email to each job dict.
    Jobs without a contact are still kept — email draft will note "Dear Hiring Manager".
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

        if not api_key or not company:
            job["hiring_manager"] = ""
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
                job["hiring_manager"] = name
                job["hm_email"] = email
                logger.info("Apollo: found %s <%s> at %s", name, email, company)
            else:
                job["hiring_manager"] = ""
                job["hm_email"] = ""
                logger.debug("Apollo: no contact found for %s", company)
        except Exception as e:
            logger.warning("Apollo lookup failed for %s: %s", company, e)
            job["hiring_manager"] = ""
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
        hm_name = job.get("hiring_manager") or "Hiring Manager"
        hm_first = hm_name.split()[0] if hm_name else "there"
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

        result = call_llm_json(prompt)
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
            email_draft=job.get("email_draft", ""),
            linkedin_draft=job.get("linkedin_draft", ""),
            approval_token=token,
            llm_score=job.get("llm_score", 0),
            llm_reason=job.get("llm_reason", ""),
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
    recipient = prefs.get("email_recipient", gmail_address)

    if not gmail_address or not gmail_password:
        logger.warning("Gmail not configured — skipping approval digest")
        return {}

    # Determine base URL for approve/skip links
    host = prefs.get("agent_host", "http://localhost:5001")

    # Build HTML email
    jobs_html = ""
    for i, job in enumerate(state["drafted"], 1):
        token = job.get("approval_token", "")
        approve_url = f"{host}/api/approve/{token}"
        skip_url = f"{host}/api/skip/{token}"
        hm_email = job.get("hm_email", "No email found")
        email_draft = job.get("email_draft", "").replace("\n", "<br>")
        linkedin_draft = job.get("linkedin_draft", "").replace("\n", "<br>")

        jobs_html += f"""
        <div style="border:1px solid #e4e4e7; border-radius:8px; padding:16px; margin-bottom:20px; font-family:sans-serif;">
          <h3 style="margin:0 0 4px 0; color:#111;">{i}. {_html.escape(job.get('role',''))} @ {_html.escape(job.get('company',''))}
            <span style="font-size:13px; color:#6366f1; font-weight:normal;">[LLM Score: {job.get('llm_score',0)}]</span>
          </h3>
          <p style="margin:0 0 12px 0; color:#555; font-size:13px;">To: {_html.escape(hm_email)} · {_html.escape(job.get('llm_reason',''))}</p>

          <p style="font-size:12px; font-weight:600; color:#374151; margin-bottom:4px;">COLD EMAIL DRAFT:</p>
          <div style="background:#f9fafb; border-radius:6px; padding:12px; font-size:13px; color:#374151; margin-bottom:12px;">{email_draft}</div>

          <p style="font-size:12px; font-weight:600; color:#374151; margin-bottom:4px;">LINKEDIN MESSAGE DRAFT:</p>
          <div style="background:#f0f9ff; border-radius:6px; padding:12px; font-size:13px; color:#374151; margin-bottom:16px;">{linkedin_draft}</div>

          <a href="{approve_url}" style="background:#6366f1; color:#fff; padding:8px 18px; border-radius:6px; text-decoration:none; font-size:13px; font-weight:600; margin-right:8px;">✅ APPROVE &amp; SEND</a>
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
        msg["To"] = recipient
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(gmail_address, gmail_password)
            smtp.sendmail(gmail_address, recipient, msg.as_string())

        logger.info("send_approval_digest: sent to %s (%d drafts)", recipient, len(state["drafted"]))
    except Exception as e:
        logger.error("send_approval_digest failed: %s", e)

    return {}
