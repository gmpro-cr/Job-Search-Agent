"""
reminder_runner.py - Fire custom email reminders after each scrape.

Loads reminders.json, queries the DB for each enabled reminder,
sends an email using the existing send_job_email(), and updates last_sent.
"""

import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REMINDERS_PATH = os.path.join(_BASE_DIR, "reminders.json")


def load_reminders() -> list:
    """Load reminders from reminders.json. Returns [] if file missing or invalid."""
    if not os.path.exists(REMINDERS_PATH):
        return []
    try:
        with open(REMINDERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load reminders.json: %s", e)
        return []


def save_reminders(reminders: list) -> None:
    """Persist reminders list to reminders.json."""
    with open(REMINDERS_PATH, "w", encoding="utf-8") as f:
        json.dump(reminders, f, indent=2)


def score_jobs_for_cv_reminder(reminder: dict) -> list:
    """
    Fetch and score jobs for a reminder that has cv_data.

    - Fetches up to 200 candidate jobs matching the keyword (ignoring min_score)
    - Scores each against the reminder's CV using cv_score()
    - Filters by min_score, sorts by CV match % descending, returns top max_jobs

    Returns a list of job dicts (same shape as get_jobs_for_reminder returns).
    Falls back to relevance_score query if cv_data is absent.
    """
    from database import get_jobs_for_reminder
    from analyzer import cv_score

    keyword = (reminder.get("keyword") or "").strip()
    min_score = max(0, min(100, int(reminder.get("min_score", 65))))
    max_jobs = max(1, min(50, int(reminder.get("max_jobs", 20))))
    cv_data = reminder.get("cv_data")

    since = reminder.get("last_sent")

    if not cv_data:
        # Legacy fallback: filter by AI relevance score
        return get_jobs_for_reminder(keyword, min_score, max_jobs, since=since)

    # Fetch broad candidate set (skip score filter, use large limit)
    candidates = get_jobs_for_reminder(keyword, min_score=0, max_jobs=200, since=since)
    if not candidates:
        return []

    # Score each job against this reminder's CV
    scored = [(job, cv_score(job, cv_data)) for job in candidates]

    # Filter by min_score, sort descending, cap at max_jobs
    filtered = sorted(
        [(j, s) for j, s in scored if s >= min_score],
        key=lambda x: -x[1],
    )[:max_jobs]

    # Inject cv_score so the email can display the CV-match score instead of relevance_score
    result = []
    for j, s in filtered:
        job = dict(j)
        job["cv_score"] = s
        result.append(job)
    return result


def run_reminders(preferences: dict) -> None:
    """
    For each enabled reminder in reminders.json:
    1. Query DB for matching jobs
    2. Send email if results found
    3. Update last_sent timestamp

    preferences must contain gmail_address and gmail_app_password.
    """
    from email_notifier import send_job_email

    reminders = load_reminders()
    if not reminders:
        logger.info("No reminders configured, skipping")
        return

    gmail_address = preferences.get("gmail_address", "").strip()
    gmail_app_password = preferences.get("gmail_app_password", "").strip()
    if not gmail_address or not gmail_app_password:
        logger.info("Gmail credentials not configured - skipping reminders")
        return

    updated = False
    for reminder in reminders:
        if not reminder.get("enabled", True):
            continue

        rem_type = (reminder.get("type") or "jobs").lower()
        recipient = (reminder.get("email") or "").strip()
        name = reminder.get("name", "Reminder")

        if not recipient:
            logger.warning("Reminder '%s' has no email, skipping", name)
            continue

        # ── PRD reminder ──────────────────────────────────────────────────
        if rem_type == "prd":
            prd_hour = int(reminder.get("prd_hour") or 8)
            now_hour = datetime.now().hour
            # Only fire within the same scheduled hour window
            if now_hour != prd_hour:
                logger.info("Reminder '%s' (PRD): not the scheduled hour (%d != %d)", name, now_hour, prd_hour)
                continue
            # Guard: skip if already sent today
            last_sent = reminder.get("last_sent") or ""
            if last_sent.startswith(datetime.now().strftime("%Y-%m-%d")):
                logger.info("Reminder '%s' (PRD): already sent today", name)
                continue
            try:
                from prd_generator import generate_daily_prd, build_prd_email_html
                import smtplib
                from email.mime.multipart import MIMEMultipart
                from email.mime.text import MIMEText

                prd = generate_daily_prd()
                html_body = build_prd_email_html(prd)
                subject = f"📋 Daily PRD: {prd['product']['name']} ({prd['date']})"

                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"] = gmail_address
                msg["To"] = recipient
                msg.attach(MIMEText(html_body, "html"))

                with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
                    server.ehlo(); server.starttls(); server.ehlo()
                    server.login(gmail_address, gmail_app_password)
                    server.sendmail(gmail_address, [recipient], msg.as_string())

                reminder["last_sent"] = datetime.now().isoformat()
                updated = True
                logger.info("Reminder '%s' (PRD): sent '%s' to %s", name, prd["product"]["name"], recipient)
            except Exception as e:
                logger.error("Reminder '%s' (PRD): send failed: %s", name, e)
            continue

        # ── Job-alert reminder (default) ──────────────────────────────────
        keyword = (reminder.get("keyword") or "").strip()
        if not keyword:
            logger.warning("Reminder '%s' has no keyword, skipping", name)
            continue

        min_score = max(0, min(100, int(reminder.get("min_score", 65))))

        jobs = score_jobs_for_cv_reminder(reminder)
        if not jobs:
            logger.info("Reminder '%s': no jobs found (keyword=%s, min_score=%d)", name, keyword, min_score)
            continue

        # Filter out jobs already sent to this reminder
        sent_ids = set(reminder.get("sent_job_ids", []))
        new_jobs = [j for j in jobs if j.get("job_id") not in sent_ids]
        if not new_jobs:
            logger.info("Reminder '%s': all %d jobs already sent previously", name, len(jobs))
            continue

        # Build a slim preferences dict for the email builder
        alert_prefs = dict(preferences)
        alert_prefs["job_titles"] = [keyword]

        success = send_job_email(recipient, new_jobs, alert_prefs)
        if success:
            reminder["last_sent"] = datetime.now().isoformat()
            # Track sent job IDs; keep only the last 500 to prevent unbounded growth
            all_sent = reminder.get("sent_job_ids", []) + [j["job_id"] for j in new_jobs if j.get("job_id")]
            reminder["sent_job_ids"] = all_sent[-500:]
            updated = True
            logger.info("Reminder '%s': sent %d new jobs to %s", name, len(new_jobs), recipient)
        else:
            logger.error("Reminder '%s': email send failed to %s", name, recipient)

    if updated:
        save_reminders(reminders)
