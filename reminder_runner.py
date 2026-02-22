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


def run_reminders(preferences: dict) -> None:
    """
    For each enabled reminder in reminders.json:
    1. Query DB for matching jobs
    2. Send email if results found
    3. Update last_sent timestamp

    preferences must contain gmail_address and gmail_app_password.
    """
    from database import get_jobs_for_reminder
    from email_notifier import send_job_email

    reminders = load_reminders()
    if not reminders:
        logger.info("No reminders configured, skipping")
        return

    updated = False
    for reminder in reminders:
        if not reminder.get("enabled", True):
            continue

        keyword = (reminder.get("keyword") or "").strip()
        if not keyword:
            logger.warning("Reminder '%s' has no keyword, skipping", reminder.get("name"))
            continue

        min_score = max(0, min(100, int(reminder.get("min_score", 65))))
        max_jobs = max(1, min(50, int(reminder.get("max_jobs", 20))))
        recipient = (reminder.get("email") or "").strip()
        name = reminder.get("name", "Job Alert")

        if not recipient:
            logger.warning("Reminder '%s' has no email, skipping", name)
            continue

        jobs = get_jobs_for_reminder(keyword, min_score, max_jobs)
        if not jobs:
            logger.info("Reminder '%s': no jobs found (keyword=%s, min_score=%d)", name, keyword, min_score)
            continue

        # Build a slim preferences dict for the email builder
        alert_prefs = dict(preferences)
        alert_prefs["job_titles"] = [keyword]

        success = send_job_email(recipient, jobs, alert_prefs)
        if success:
            reminder["last_sent"] = datetime.now().isoformat()
            updated = True
            logger.info("Reminder '%s': sent %d jobs to %s", name, len(jobs), recipient)
        else:
            logger.error("Reminder '%s': email send failed to %s", name, recipient)

    if updated:
        save_reminders(reminders)
