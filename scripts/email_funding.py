"""On-demand: email the owner a "Funding Rundown" of recently-funded startups,
formatted in The Rundown AI newsletter style.

Run from the "Email Funding Rundown" GitHub Actions workflow (which supplies the
GMAIL_* secrets), or locally with GMAIL_ADDRESS / GMAIL_APP_PASSWORD set.
"""
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db                                          # noqa: E402
from funding_digest import build_funding_digest, _infer_region  # noqa: E402
from email_notifier import send_html_email                      # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("email_funding")

# `or` (not the get default) because scheduled runs pass these as empty strings.
RECIPIENT = (os.environ.get("FUNDING_RECIPIENT") or "mahalegauravk@gmail.com").strip()
DAYS = int(os.environ.get("FUNDING_DAYS") or "3")
LIMIT = int(os.environ.get("FUNDING_LIMIT") or "12")

# Newest funding with an amount first; region is re-derived in Python (the stored
# region is the unreliable news-edition guess). No '?'/'%' in the SQL (the cursor
# adapter would treat them as bind placeholders).
_SQL = """
    SELECT startup, amount, round, region, source, source_url, title, posted_date
    FROM funding_news
    WHERE posted_date::date >= CURRENT_DATE - {days}
    ORDER BY (amount IS NOT NULL) DESC, posted_date DESC NULLS LAST, id DESC
    LIMIT {limit}
"""


def _fetch(days, limit):
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(_SQL.format(days=int(days), limit=int(limit)))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _norm_name(s):
    """Collapse a startup name for dedup: drop {tags}, 'funding alert', and
    non-alphanumerics so 'Recykal', '{Funding Alert} Recykal' all match."""
    s = (s or "").lower()
    s = re.sub(r"\{[^}]*\}", "", s)
    s = s.replace("funding alert", "")
    return re.sub(r"[^a-z0-9]", "", s)


def _dedup(rows):
    """Keep the first occurrence of each company (rows are pre-sorted by amount,
    newest), so the same round reported by multiple outlets shows once."""
    seen, out = set(), []
    for r in rows:
        k = _norm_name(r.get("startup"))
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def _compose(pool, limit):
    """India-first, but keep other regions: lead with India companies, then fill
    the remainder with other regions (reserving room so others always appear)."""
    for it in pool:
        it["region"] = _infer_region(it.get("title"), it.get("source"), it.get("region"))
    india = [it for it in pool if it["region"] == "India"]
    others = [it for it in pool if it["region"] != "India"]
    n_others = min(len(others), max(4, limit // 3))   # guarantee some non-India
    n_india = min(len(india), limit - n_others)
    items = india[:n_india] + others[:n_others]
    if len(items) < limit:                             # backfill leftovers, India first
        items += (india[n_india:] + others[n_others:])[:limit - len(items)]
    return items[:limit]


def main():
    pool = _dedup(_fetch(DAYS, LIMIT * 8))
    # Widen the window if the last few days were quiet, so the email is never empty.
    if len(pool) < 8:
        pool = _dedup(_fetch(7, LIMIT * 8))
    items = _compose(pool, LIMIT)
    regions = {}
    for it in items:
        regions[it["region"]] = regions.get(it["region"], 0) + 1
    logger.info("Funding items selected: %d (window <= %d days) by region: %s",
                len(items), DAYS, regions)
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
