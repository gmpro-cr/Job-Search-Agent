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


def _current_session_user_id():
    try:
        from flask import session as _session, has_request_context
    except Exception:
        return None
    try:
        if not has_request_context():
            return None
        uid = (_session.get("user") or {}).get("id") if _session else None
        return int(uid) if uid else None
    except Exception:
        return None


def load_reminders(user_id: int = None) -> list:
    """
    Load reminders.
    - With a user in scope, return ONLY that user's reminders from the DB.
      Never leak the legacy global file into a per-user lookup.
    - Outside a user context (CLI / scheduler), fall back to the legacy
      reminders.json.
    """
    resolved = user_id if user_id is not None else _current_session_user_id()
    if resolved:
        try:
            from database import get_user_reminders as _gur
            return _gur(resolved) or []
        except Exception as e:
            logger.warning("DB reminder load failed for user %s: %s", resolved, e)
            return []
    if not os.path.exists(REMINDERS_PATH):
        return []
    try:
        with open(REMINDERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load reminders.json: %s", e)
        return []


def save_reminders(reminders: list, user_id: int = None) -> None:
    """
    Persist reminders. With a user_id (or active Flask session), writes that
    user's reminders to DB. Otherwise falls back to the legacy JSON file.
    """
    if user_id is None:
        user_id = _current_session_user_id()
    if user_id:
        from database import save_user_reminders as _sur
        _sur(user_id, reminders or [])
        return
    with open(REMINDERS_PATH, "w", encoding="utf-8") as f:
        json.dump(reminders, f, indent=2)


def load_all_reminders_global() -> list:
    """
    Cross-user reminder list — used by the post-scrape `run_reminders` worker
    which fires emails for every user. Reads from DB (all users) and merges in
    any legacy file-based reminders not yet migrated.
    """
    out: list = []
    try:
        from database import get_connection
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            """
            SELECT ur.reminder_json, u.email
            FROM user_reminders ur
            JOIN users u ON u.id = ur.user_id
            """
        )
        for row in c.fetchall():
            try:
                r = json.loads(row["reminder_json"])
                r.setdefault("owner_email", row["email"])
                out.append(r)
            except (ValueError, TypeError):
                continue
        conn.close()
    except Exception as e:
        logger.warning("Cross-user reminder load failed: %s", e)
    if os.path.exists(REMINDERS_PATH):
        try:
            with open(REMINDERS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                out.extend(data)
        except (json.JSONDecodeError, OSError):
            pass
    return out


def _owner_preferences(reminder: dict) -> dict:
    """
    Best-effort fetch of the reminder owner's saved preferences, used to apply
    the same intent boosts (title / location / transferable skills) that the
    website's per-user scoring uses. Without this, cv_score() runs with no
    preferences and the title-match boost never fires, so genuinely relevant
    jobs score well below the threshold and the email comes back empty.

    Falls back to seeding the reminder's own keyword as a job title so the
    title-match boost still applies even when the user has no saved prefs.
    """
    email = (reminder.get("owner_email") or reminder.get("email") or "").strip()
    prefs = None
    if email:
        try:
            from database import get_user_by_email, get_user_preferences
            u = get_user_by_email(email)
            if u:
                prefs = get_user_preferences(u["id"])
        except Exception as e:
            logger.warning("Could not load prefs for reminder owner %s: %s", email, e)
    prefs = dict(prefs or {})
    keyword = (reminder.get("keyword") or "").strip()
    if keyword:
        titles = list(prefs.get("job_titles") or [])
        if keyword not in titles:
            titles.append(keyword)
        prefs["job_titles"] = titles
    return prefs


def _job_fingerprint(job: dict) -> str:
    """Content-based fingerprint: company + role (first 60 chars).
    Used as a secondary dedup key so the same listing doesn't get emailed
    again if its job_id changes between scrapes (company/role text drifts)."""
    company = (job.get("company") or "").lower().strip()
    role = (job.get("role") or "").lower().strip()[:60]
    return f"{company}|{role}"


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

    # Score each job against this reminder's CV, applying the owner's preference
    # boosts so email scoring matches what the website shows (see _owner_preferences).
    prefs = _owner_preferences(reminder)
    scored = [(job, cv_score(job, cv_data, prefs)) for job in candidates]

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

    # Cross-user read so the post-scrape worker fires reminders for all users,
    # falling back to the legacy file for unmigrated reminders.
    reminders = load_all_reminders_global()
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

        recipient = (reminder.get("email") or "").strip()
        name = reminder.get("name", "Reminder")

        if not recipient:
            logger.warning("Reminder '%s' has no email, skipping", name)
            continue

        # ── Job-alert reminder ────────────────────────────────────────────
        # Wrapped so a single reminder's failure (scoring, DB, SMTP) can't
        # abort the whole batch and silently stop everyone's daily mail.
        try:
            keyword = (reminder.get("keyword") or "").strip()
            if not keyword:
                logger.warning("Reminder '%s' has no keyword, skipping", name)
                continue

            min_score = max(0, min(100, int(reminder.get("min_score", 65))))

            jobs = score_jobs_for_cv_reminder(reminder)
            if not jobs:
                logger.info("Reminder '%s': no jobs found (keyword=%s, min_score=%d)", name, keyword, min_score)
                continue

            # Filter out jobs already sent to this reminder.
            # Use both job_id AND a content fingerprint (company+role) so that
            # if the same listing is re-scraped with a slightly different name
            # (generating a new job_id), it's still suppressed.
            sent_ids = set(reminder.get("sent_job_ids", []))
            sent_fps = set(reminder.get("sent_fingerprints", []))
            new_jobs = [
                j for j in jobs
                if j.get("job_id") not in sent_ids
                and _job_fingerprint(j) not in sent_fps
            ]
            if not new_jobs:
                logger.info("Reminder '%s': all %d jobs already sent previously", name, len(jobs))
                continue

            # Build a slim preferences dict for the email builder
            alert_prefs = dict(preferences)
            alert_prefs["job_titles"] = [keyword]

            success, send_err = send_job_email(recipient, new_jobs, alert_prefs)
            if success:
                reminder["last_sent"] = datetime.now().isoformat()
                # Track sent job IDs and content fingerprints; cap at 500 each.
                all_sent = reminder.get("sent_job_ids", []) + [j["job_id"] for j in new_jobs if j.get("job_id")]
                reminder["sent_job_ids"] = all_sent[-500:]
                all_fps = reminder.get("sent_fingerprints", []) + [_job_fingerprint(j) for j in new_jobs]
                reminder["sent_fingerprints"] = all_fps[-500:]
                updated = True
                logger.info("Reminder '%s': sent %d new jobs to %s", name, len(new_jobs), recipient)
            else:
                logger.error("Reminder '%s': email send failed to %s: %s", name, recipient, send_err)
        except Exception as e:
            logger.error("Reminder '%s': processing failed, skipping: %s", name, e)
            continue

    if updated:
        _persist_reminders_after_run(reminders)


def _persist_reminders_after_run(reminders: list) -> None:
    """
    After run_reminders has potentially mutated each reminder dict
    (last_sent / sent_job_ids), persist them back to the right owner.
    Reminders with a known owner_email go to per-user DB; the rest fall
    back to the legacy file.
    """
    try:
        from database import get_user_by_email, save_user_reminders
    except Exception:
        get_user_by_email = None
        save_user_reminders = None

    by_user: dict = {}
    legacy: list = []
    for r in reminders:
        owner = (r.get("owner_email") or "").strip().lower()
        if owner and get_user_by_email:
            user = get_user_by_email(owner)
            if user:
                by_user.setdefault(user["id"], []).append(r)
                continue
        legacy.append(r)

    if save_user_reminders:
        for uid, rems in by_user.items():
            try:
                save_user_reminders(uid, rems)
            except Exception as e:
                logger.error("Failed to save reminders for user %s: %s", uid, e)

    if legacy and os.path.exists(REMINDERS_PATH):
        try:
            with open(REMINDERS_PATH, "w", encoding="utf-8") as f:
                json.dump(legacy, f, indent=2)
        except OSError as e:
            logger.error("Failed to write legacy reminders.json: %s", e)
