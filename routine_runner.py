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


DEDUP_DAYS = 7  # don't re-email a job to the same user within this many days


def run_routines(send_fn=None, now=None, user_id=None):
    """Email per-routine shortlists for saved routines.

    Dedup is by an explicit per-user sent-marker (user_job_state.digest_sent_at),
    NOT by date_found — date_found is refreshed every scrape, so it can't tell
    whether a job was already emailed. This is why the evening digest used to
    repeat the morning's jobs, and why "Send now" re-sent everything.

    user_id: scope to one user (e.g. an on-demand "Send now"); None = all users.
    send_fn(recipient, jobs, preferences, subject=None) -> bool is injectable for
    testing; defaults to email_notifier.send_job_email. Returns a summary dict."""
    from email_notifier import send_job_email
    from database import (get_all_user_reminders, get_user_reminders,
                          get_jobs_for_reminder, save_user_reminders,
                          recently_sent_job_ids, mark_sent_in_digest)
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
            max_jobs = int(r.get("max_jobs", 10) or 10)
            try:
                # Pull a wider candidate window so we can drop already-sent jobs
                # and still fill up to max_jobs with genuinely new matches.
                candidates = get_jobs_for_reminder(
                    keyword,
                    int(r.get("min_score", 50) or 50),
                    min(200, max(max_jobs * 5, max_jobs)),
                    location=r.get("location"),
                    posted_within_days=int(r["max_age_days"]) if r.get("max_age_days") else None,
                    user_id=user_id,
                )
            except Exception as e:
                logger.warning("Routine %s query failed: %s", r.get("id"), e)
                continue
            ids = [j.get("job_id") for j in candidates if j.get("job_id")]
            already = recently_sent_job_ids(ids, days=DEDUP_DAYS, user_id=user_id)
            jobs = [j for j in candidates if j.get("job_id") not in already][:max_jobs]
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
                # Mark these jobs sent for this user so neither the next cron nor
                # a manual "Send now" repeats them.
                try:
                    mark_sent_in_digest([j["job_id"] for j in jobs if j.get("job_id")],
                                        user_id=user_id)
                except Exception as e:
                    logger.warning("mark_sent_in_digest failed for user %s: %s", user_id, e)
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
