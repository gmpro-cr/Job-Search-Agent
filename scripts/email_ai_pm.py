"""On-demand: email the full list of AI Product Manager jobs to AI_PM_RECIPIENT.

Manual sender. The automatic daily send is done by the main scrape cron
(scrape_and_push.py, Phase 7f) to every user who enabled the AI PM jobs routine
on the digests page. Run this for a one-off / test send via the "Email AI PM
Jobs" workflow_dispatch, or locally with GMAIL_ADDRESS / GMAIL_APP_PASSWORD set.
"""
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_ai_pm_jobs            # noqa: E402
from email_notifier import send_job_email      # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("email_ai_pm")

RECIPIENT = (os.environ.get("AI_PM_RECIPIENT") or "mahalegauravk@gmail.com").strip()


def main():
    jobs = get_ai_pm_jobs()
    logger.info("AI PM jobs selected: %d", len(jobs))
    prefs = {
        "email": RECIPIENT,
        "gmail_address": os.environ.get("GMAIL_ADDRESS", ""),
        "gmail_app_password": os.environ.get("GMAIL_APP_PASSWORD", ""),
        "job_titles": ["AI Product Manager"],
    }
    subject = "AI Product Manager jobs — full list (%d roles, %s)" % (
        len(jobs), datetime.now().strftime("%b %d, %Y"))
    ok, err = send_job_email(RECIPIENT, jobs, prefs, subject=subject)
    logger.info("Email sent: %s -> %s (%d jobs)%s",
                ok, RECIPIENT, len(jobs), "" if ok else f" — {err}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
