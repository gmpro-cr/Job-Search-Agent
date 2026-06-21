"""Scrape startup funding news from finsmes (all regions) and upsert to the DB.

RUN THIS LOCALLY (from a residential IP). finsmes is behind Cloudflare, which
returns 403 to datacenter IPs (GitHub Actions, Vercel) even with curl_cffi's
Chrome TLS impersonation — but a home/residential IP works fine. So the funding
data is refreshed from your machine, not the cloud cron.

Usage:
    python -m scripts.scrape_funding

Writes to whatever DATABASE_URL points at (Neon when set in .env, else SQLite).
Requires curl_cffi (pip install curl_cffi). A daily macOS launchd job is
provided at scripts/com.gaurav.finsmes-funding.plist.
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db                       # noqa: E402
from funding_scraper import scrape_finsmes  # noqa: E402
from news_scraper import scrape_news_sources  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scrape_funding")


def main():
    db.init_db()  # ensure funding_news exists
    # Full refresh from a residential IP: finsmes (Cloudflare) + the public RSS
    # sources (which the cloud cron also runs).
    rows = scrape_finsmes() + scrape_news_sources()
    new = db.insert_funding_bulk(rows)
    try:
        db.delete_old_funding(days=120)
    except Exception as e:
        logger.warning("retention sweep failed: %s", e)
    logger.info("Funding: scraped %d rows, inserted %d new across regions", len(rows), new)


if __name__ == "__main__":
    main()
