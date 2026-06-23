"""On-demand: email a "Funding Rundown" of recently-funded startups, formatted in
The Rundown AI newsletter style. Manual sender — sends to FUNDING_RECIPIENT.

The automatic daily send is done by the main scrape cron (scrape_and_push.py,
Phase 7e) to every user who enabled the Funding Rundown routine on the digests
page. Run this script for a one-off / test send via the "Email Funding Rundown"
workflow_dispatch, or locally with GMAIL_ADDRESS / GMAIL_APP_PASSWORD set.
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from funding_digest import select_funding_items, build_funding_digest  # noqa: E402
from email_notifier import send_html_email                             # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("email_funding")

# `or` (not the get default) because scheduled runs pass these as empty strings.
RECIPIENT = (os.environ.get("FUNDING_RECIPIENT") or "mahalegauravk@gmail.com").strip()
DAYS = int(os.environ.get("FUNDING_DAYS") or "3")
LIMIT = int(os.environ.get("FUNDING_LIMIT") or "12")


def main():
    items = select_funding_items(DAYS, LIMIT)
    by_region = {}
    for it in items:
        by_region[it["region"]] = by_region.get(it["region"], 0) + 1
    logger.info("Funding items selected: %d (window <= %d days) by region: %s",
                len(items), DAYS, by_region)
    if not items:
        logger.warning("No funding rows to send — skipping email")
        return

    subject, html_body, text_body = build_funding_digest(items)
    prefs = {
        "gmail_address": os.environ.get("GMAIL_ADDRESS", ""),
        "gmail_app_password": os.environ.get("GMAIL_APP_PASSWORD", ""),
    }
    ok, err = send_html_email(RECIPIENT, subject, html_body, text_body, prefs)
    logger.info("Email sent: %s -> %s (%d startups)%s",
                ok, RECIPIENT, len(items), "" if ok else f" — {err}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
