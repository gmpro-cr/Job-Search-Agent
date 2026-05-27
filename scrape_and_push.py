#!/usr/bin/env python3
"""
scrape_and_push.py - Standalone scraper that runs on GitHub Actions.

Scrapes all job portals, analyzes/scores jobs, sends email + Telegram
notifications, then writes results to data/latest_scrape.json for the
local app to import on startup.

Required env vars:
    GMAIL_ADDRESS         - sender Gmail address
    GMAIL_APP_PASSWORD    - Gmail App Password (not your Google password)
    EMAIL_RECIPIENT       - recipient email address

Optional env vars:
    TELEGRAM_BOT_TOKEN    - Telegram bot token
    TELEGRAM_CHAT_ID      - Telegram chat ID
    TELEGRAM_MIN_SCORE    - minimum score for Telegram alerts (default: 65)
    OPENROUTER_API_KEY    - for AI-based scoring
"""

import json
import logging
import os
import sys
from datetime import date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

from main import load_config, load_preferences, DEFAULT_PREFS, apply_env_overrides
from scrapers import scrape_all_portals
from analyzer import analyze_jobs
from database import generate_job_id, init_db, insert_jobs_bulk, delete_old_jobs
from email_notifier import send_job_email
from telegram_notifier import send_telegram_alert, send_telegram_batch_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(BASE_DIR, "data")
SCRAPE_OUTPUT = os.path.join(DATA_DIR, "latest_scrape.json")


def _get_owner_targets():
    """
    Read job_titles + locations from the owner's preferences row in the DB.
    Uses OWNER_EMAIL env var to identify the owner. Falls back to ([], [])
    so the caller uses the JSON preferences file instead.
    """
    owner_email = os.environ.get("OWNER_EMAIL", "").strip().lower()
    if not owner_email:
        return [], []
    try:
        from database import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT up.prefs_json FROM user_preferences up "
            "JOIN users u ON u.id = up.user_id "
            "WHERE lower(u.email) = ?",
            (owner_email,),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return [], []
        prefs = json.loads(row["prefs_json"])
        titles = [t.strip() for t in (prefs.get("job_titles") or []) if t.strip()]
        locs   = [l.strip() for l in (prefs.get("locations")   or []) if l.strip()]
        return titles, locs
    except Exception as e:
        logger.warning("Could not load owner targets from DB: %s", e)
        return [], []


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    config = load_config()
    preferences = apply_env_overrides(load_preferences() or DEFAULT_PREFS.copy())

    # Single-owner mode: use only the owner's saved preferences.
    # Falls back to JSON preferences file if DB lookup fails.
    owner_titles, owner_locs = _get_owner_targets()
    default_titles = preferences.get("job_titles") or DEFAULT_PREFS["job_titles"]
    default_locs   = preferences.get("locations")  or DEFAULT_PREFS["locations"]

    # Owner prefs take priority; fall back to JSON/defaults if owner hasn't set prefs yet.
    job_titles = owner_titles or default_titles
    locations  = owner_locs  or default_locs

    top_n = preferences.get("top_jobs_per_digest", 5)
    logger.info(
        "Scraper targets — titles=%d, locations=%d (owner: %s)",
        len(job_titles), len(locations),
        os.environ.get("OWNER_EMAIL", "unset"),
    )

    # --- Phase 1: Scrape ---
    logger.info("Scraping %d titles across %d locations...", len(job_titles), len(locations))
    all_jobs, portal_results = scrape_all_portals(job_titles, locations, config)

    for portal, result in portal_results.items():
        logger.info("  %s: %s (%d jobs)", portal, result.get("status"), result.get("count", 0))

    if not all_jobs:
        logger.warning("No jobs scraped. Exiting.")
        return

    logger.info("Total raw jobs scraped: %d", len(all_jobs))

    # --- Phase 2: Analyze ---
    logger.info("Analyzing and scoring jobs...")
    qualified_jobs, all_analyzed = analyze_jobs(all_jobs, preferences, config)
    logger.info("Analyzed %d jobs, %d qualified", len(all_analyzed), len(qualified_jobs))

    # --- Phase 3: Generate IDs ---
    for job in all_analyzed:
        job["job_id"] = generate_job_id(
            job.get("portal", "unknown"),
            job.get("company", ""),
            job.get("role", ""),
            job.get("location", ""),
        )

    # --- Phase 4: Write JSON for local app to import ---
    serializable_fields = [
        "job_id", "portal", "company", "role", "salary", "salary_currency",
        "location", "job_description", "apply_url", "relevance_score",
        "remote_status", "company_type", "date_posted",
        "experience_min", "experience_max", "salary_min", "salary_max",
        "company_size", "company_funding_stage", "company_glassdoor_rating",
    ]
    payload_jobs = []
    for job in all_analyzed:
        clean = {k: job[k] for k in serializable_fields if k in job and job[k] is not None}
        payload_jobs.append(clean)

    with open(SCRAPE_OUTPUT, "w") as f:
        json.dump(payload_jobs, f)
    logger.info("Wrote %d jobs to %s", len(payload_jobs), SCRAPE_OUTPUT)

    # --- Phase 5: Telegram alerts ---
    tg_token = preferences.get("telegram_bot_token", "").strip()
    tg_chat = preferences.get("telegram_chat_id", "").strip()
    tg_min = int(preferences.get("telegram_min_score", 65))
    if tg_token and tg_chat:
        alert_count = 0
        for job in qualified_jobs:
            if job.get("relevance_score", 0) >= tg_min:
                send_telegram_alert(job, tg_token, tg_chat)
                alert_count += 1
        send_telegram_batch_summary(
            len(all_jobs), len(qualified_jobs), len(payload_jobs), tg_token, tg_chat
        )
        logger.info("Sent %d Telegram alerts", alert_count)
    else:
        logger.info("Telegram not configured — skipping alerts")

    # --- Phase 6: Email digest ---
    recipient = preferences.get("email", "").strip()
    gmail_addr = preferences.get("gmail_address", "").strip()
    gmail_pass = preferences.get("gmail_app_password", "").strip()
    if recipient and gmail_addr and gmail_pass:
        digest_jobs = qualified_jobs[:top_n]
        try:
            send_job_email(recipient, digest_jobs, preferences)
            logger.info("Email digest sent to %s (%d jobs)", recipient, len(digest_jobs))
        except Exception as e:
            logger.error("Failed to send email: %s", e)
    else:
        logger.warning("Email credentials not set — skipping email notification")

    # --- Phase 7: Reminders ---
    init_db()
    insert_jobs_bulk(all_analyzed)
    # 7-day retention: drop everything older than a week, with dependent
    # user_job_state + outreach_queue rows. Runs at the end of every
    # scrape so storage stays bounded.
    try:
        purged = delete_old_jobs(days=7)
        logger.info(
            "Retention sweep: removed %d jobs, %d user_states, %d outreach drafts",
            purged["jobs"], purged["user_state"], purged["outreach"],
        )
    except Exception as e:
        logger.warning("Retention sweep failed: %s", e)
    from reminder_runner import run_reminders
    run_reminders(preferences)

    # --- Phase 8: Auto-discover hiring managers ---
    try:
        from hiring_managers_search import (
            get_new_hiring_managers,
            load_hm_contacts_dict,
            save_hm_contacts_dict,
            load_all_hm_contacts,
        )
        _role_kws = job_titles[:5]
        _location = (locations[0] if locations else "India")
        _existing = load_all_hm_contacts()
        new_hms = get_new_hiring_managers(
            sent_contacts=_existing,
            role_keywords=_role_kws,
            location=_location,
            target=5,
        )
        if new_hms:
            today = date.today().isoformat()
            hm_data = load_hm_contacts_dict()
            bucket = hm_data.setdefault("scraper_auto", [])
            for c in new_hms:
                c.setdefault("date_sent", today)
                bucket.append(c)
            save_hm_contacts_dict(hm_data)
            logger.info("Auto-HM discovery: added %d new contacts", len(new_hms))
        else:
            logger.info("Auto-HM discovery: no new contacts found")
    except Exception as e:
        logger.warning("Auto-HM discovery skipped: %s", e)

    # --- Phase 9: AI agent — contact enrichment + outreach drafts ---
    # Picks up freshly inserted jobs, looks up hiring manager contacts
    # (Apollo API if APOLLO_API_KEY is set, else falls back to portal-scraped
    # poster info), drafts cold emails, and queues them in outreach_queue.
    # Wrapped in try/except so a missing LLM key never breaks the scrape.
    try:
        from agent.graph import run_agent_pipeline
        agent_result = run_agent_pipeline(preferences, config)
        logger.info(
            "Agent pipeline complete — %d drafts queued",
            agent_result.get("queued_count", 0),
        )
        if agent_result.get("errors"):
            for err in agent_result["errors"]:
                logger.warning("Agent error: %s", err)
    except Exception as e:
        logger.warning("Agent pipeline skipped: %s", e)

    logger.info("Done.")


if __name__ == "__main__":
    main()
