"""On-demand: email the owner the full list of AI Product Manager jobs.

Selects every job whose role is a Product Manager/Owner/Lead role AND carries an
AI signal as a whole word (so "retail", "email", "domain", "maintenance" don't
false-match), then emails all of them via Gmail SMTP.

Run from the "Email AI PM Jobs" GitHub Actions workflow (which supplies the
GMAIL_* secrets), or locally with GMAIL_ADDRESS / GMAIL_APP_PASSWORD set.
"""
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db                       # noqa: E402
from email_notifier import send_job_email   # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("email_ai_pm")

RECIPIENT = (os.environ.get("AI_PM_RECIPIENT") or "mahalegauravk@gmail.com").strip()

# Role must read as a PM role; AI signal matched as whole words via POSIX regex
# word boundaries (\m \M). No literal '?' in the pattern — the SQLite/PG cursor
# adapter rewrites '?' into a bind placeholder.
_AI_PM_SQL = r"""
    SELECT role, company, location, salary, apply_url, date_posted, remote_status,
           experience_min, experience_max, relevance_score, portal, job_description
    FROM job_listings
    WHERE LOWER(role) ~ '(product manager|product owner|product lead)'
      AND LOWER(role) ~ '(\mai\M|\mml\M|artificial intelligence|machine learning|genai|gen ai|generative ai|\mllm\M)'
    ORDER BY date_posted DESC NULLS LAST, relevance_score DESC NULLS LAST
"""


def main():
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(_AI_PM_SQL)
    jobs = [dict(r) for r in cur.fetchall()]
    conn.close()
    logger.info("AI PM jobs selected: %d", len(jobs))

    prefs = {
        "email": RECIPIENT,
        "gmail_address": os.environ.get("GMAIL_ADDRESS", ""),
        "gmail_app_password": os.environ.get("GMAIL_APP_PASSWORD", ""),
        "job_titles": ["AI Product Manager"],
    }
    subject = "AI Product Manager jobs — full list (%d roles, %s)" % (
        len(jobs), datetime.now().strftime("%b %d, %Y"))
    ok = send_job_email(RECIPIENT, jobs, prefs, subject=subject)
    logger.info("Email sent: %s -> %s (%d jobs)", ok, RECIPIENT, len(jobs))
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
