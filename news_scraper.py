"""Aggregate startup funding news from public RSS sources.

Unlike finsmes (Cloudflare, residential-IP only), these feeds work from any IP
(incl. GitHub Actions), so they power the automated daily refresh:
  - Google News RSS — per-country funding query; aggregates many publishers.
  - TechCrunch funding RSS — global.

Each item is normalised to the same shape funding_scraper produces:
  {startup, title, amount, round, region, source, source_url, posted_date}
Funding parsing + roundup filtering is shared via funding_scraper.parse_funding_title.
"""
import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

from funding_scraper import parse_funding_title

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

# region -> Google News country edition params (hl, gl, ceid)
GOOGLE_NEWS_EDITIONS = {
    "India": ("en-IN", "IN", "IN:en"),
    "USA": ("en-US", "US", "US:en"),
    "UK": ("en-GB", "GB", "GB:en"),
    "Canada": ("en-CA", "CA", "CA:en"),
    "Singapore": ("en-SG", "SG", "SG:en"),
    "Australia": ("en-AU", "AU", "AU:en"),
}
_GN_QUERY = "startup raises funding when:7d"


def _pubdate_iso(s):
    """RFC822 pubDate -> YYYY-MM-DD (best-effort)."""
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except (ValueError, AttributeError):
            continue
    return datetime.now().date().isoformat()


def _publisher(item, title):
    src = item.find("{*}source")
    if src is not None and src.text:
        return src.text.strip()
    m = re.search(r'\s-\s([^-]+)$', title)  # "... - Publisher"
    return m.group(1).strip() if m else "Google News"


def _parse_feed(xml_bytes, region, default_source, strip_publisher=False):
    out = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        logger.warning("feed parse error (%s): %s", region, e)
        return out
    for item in root.findall(".//item"):
        title = html.unescape(item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        publisher = _publisher(item, title) if strip_publisher else default_source
        base = re.sub(r'\s-\s[^-]+$', '', title) if strip_publisher else title
        parsed = parse_funding_title(base)
        if not parsed or not (parsed["amount"] or parsed["round"]):
            continue  # require an amount or round to keep the signal clean
        out.append({
            **parsed,
            "title": base,
            "region": region,
            "source": publisher,
            "source_url": link,
            "posted_date": _pubdate_iso(item.findtext("pubDate") or ""),
        })
    return out


def scrape_google_news(regions=None, timeout=20):
    rows = []
    editions = regions or list(GOOGLE_NEWS_EDITIONS)
    for i, region in enumerate(editions):
        params = GOOGLE_NEWS_EDITIONS.get(region)
        if not params:
            continue
        if i:
            time.sleep(1.0)
        hl, gl, ceid = params
        url = (f"https://news.google.com/rss/search?q={requests.utils.quote(_GN_QUERY)}"
               f"&hl={hl}&gl={gl}&ceid={ceid}")
        try:
            r = requests.get(url, headers=_UA, timeout=timeout)
            if r.status_code != 200:
                logger.warning("google news %s: HTTP %s", region, r.status_code)
                continue
            found = _parse_feed(r.content, region, "Google News", strip_publisher=True)
            rows.extend(found)
            logger.info("google news %s: %d funding items", region, len(found))
        except Exception as e:
            logger.warning("google news %s failed: %s", region, e)
    return rows


def scrape_techcrunch(timeout=20):
    try:
        r = requests.get("https://techcrunch.com/tag/funding/feed/", headers=_UA, timeout=timeout)
        if r.status_code != 200:
            logger.warning("techcrunch: HTTP %s", r.status_code)
            return []
        rows = _parse_feed(r.content, "Global", "TechCrunch", strip_publisher=False)
        logger.info("techcrunch: %d funding items", len(rows))
        return rows
    except Exception as e:
        logger.warning("techcrunch failed: %s", e)
        return []


def scrape_news_sources(regions=None):
    """All RSS funding sources (cloud-safe). Returns a flat list of rows."""
    return scrape_google_news(regions) + scrape_techcrunch()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    for r in scrape_news_sources(["India"]):
        print(r["region"], "|", r["startup"][:30], "|", r["amount"], "|", r["round"], "|", r["source"])
