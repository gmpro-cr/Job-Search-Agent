"""Run saved routines after a scrape and email each routine's shortlist.

A "routine" (stored per-user in user_reminders) is a saved search: a name, a
recipient, comma-separated keywords + optional locations, a minimum score, and a
max count. After each scrape `run_routines()` emails every enabled routine's
fresh matches to its recipient and stamps `last_sent` so the next run only
surfaces newer listings.

Decisions (see docs/plans): one email PER routine; run for ALL users that have
routines. Gmail credentials are app-level (from env), same as the cron digest.
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def run_routines(send_fn=None, now=None, user_id=None, ignore_last_sent=False):
    """Email per-routine shortlists for saved routines.

    user_id: scope to one user (e.g. an on-demand "Send now"); None = all users.
    ignore_last_sent: when True, don't restrict to jobs found after last_sent —
        used by the manual button so it always sends the current fresh matches
        (the recency cap inside get_jobs_for_reminder still applies).
    send_fn(recipient, jobs, preferences, subject=None) -> bool is injectable for
    testing; defaults to email_notifier.send_job_email. Returns a summary dict."""
    from email_notifier import send_job_email
    from database import (get_all_user_reminders, get_user_reminders,
                          get_jobs_for_reminder, save_user_reminders)
    from main import apply_env_overrides

    send_fn = send_fn or send_job_email
    now = now or datetime.now().isoformat()
    summary = {"users": 0, "routines": 0, "emails_sent": 0, "jobs_sent": 0}

    # Gmail creds are app-level; without them nothing can be sent.
    send_prefs = apply_env_overrides({})
    if not (send_prefs.get("gmail_address") and send_prefs.get("gmail_app_password")):
        logger.warning("Gmail credentials not configured — skipping routine emails")
        return summary

    if user_id is not None:
        items = [(user_id, get_user_reminders(user_id))]
    else:
        items = get_all_user_reminders()

    for user_id, reminders in items:
        summary["users"] += 1
        changed = False
        for r in reminders:
            if not r.get("enabled", True):
                continue
            recipient = (r.get("email") or "").strip()
            keyword = (r.get("keyword") or "").strip()
            if not recipient or not keyword:
                continue
            summary["routines"] += 1
            try:
                jobs = get_jobs_for_reminder(
                    keyword,
                    int(r.get("min_score", 50) or 50),
                    int(r.get("max_jobs", 10) or 10),
                    since=None if ignore_last_sent else r.get("last_sent"),
                    location=r.get("location"),
                )
            except Exception as e:
                logger.warning("Routine %s query failed: %s", r.get("id"), e)
                continue
            if not jobs:
                continue
            name = (r.get("name") or "Saved routine").strip()
            plural = "" if len(jobs) == 1 else "es"
            subject = f"{name}: {len(jobs)} new match{plural}"
            try:
                ok = send_fn(recipient, jobs, send_prefs, subject=subject)
            except Exception as e:
                logger.warning("Routine %s email failed: %s", r.get("id"), e)
                ok = False
            if ok:
                r["last_sent"] = now
                changed = True
                summary["emails_sent"] += 1
                summary["jobs_sent"] += len(jobs)
                logger.info("Routine '%s' -> %d jobs to %s", name, len(jobs), recipient)
        if changed:
            save_user_reminders(user_id, reminders)

    logger.info("Routine run: %d users, %d routines, %d emails, %d jobs",
                summary["users"], summary["routines"], summary["emails_sent"],
                summary["jobs_sent"])
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    run_routines()
