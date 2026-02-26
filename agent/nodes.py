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
