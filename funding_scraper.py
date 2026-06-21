"""Scrape recent startup funding rounds from finsmes.com (per region).

finsmes is behind Cloudflare, so plain requests/RSS get 403 — curl_cffi with a
Chrome TLS fingerprint clears it. Runs cron-side only (curl_cffi lives in
requirements-scraper.txt); the web app reads the stored rows, never scrapes live.
"""
import html
import logging
import re

logger = logging.getLogger(__name__)

# finsmes region category slugs (from the homepage nav).
REGIONS = {
    "usa": "USA", "uk": "UK", "india": "India", "canada": "Canada",
    "france": "France", "germany": "Germany", "italy": "Italy",
    "belgium": "Belgium", "denmark": "Denmark",
}

_BASE = "https://www.finsmes.com/category/{slug}"
_ARTICLE = re.compile(
    r'<a[^>]+href="(https://www\.finsmes\.com/(\d{4})/(\d{2})/[^"]+)"[^>]*rel="bookmark"[^>]*>(.*?)</a>',
    re.S)
_VERB = re.compile(r'\b(raises|closes|secures|lands|gets|bags|completes|receives)\b', re.I)
_ROUND = re.compile(r'\b(pre-seed|seed|series\s+[a-h]|angel|growth|venture|debt|bridge|grant)\b', re.I)
_AMOUNT = re.compile(r'\$\s?[\d.]+\s?(?:million|billion|m|bn|b|k)\b', re.I)


def _clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def parse_listing(html_text, region_slug):
    """Parse a finsmes category page into funding rows. Only includes posts whose
    title states a funding event (has a funding verb)."""
    out = []
    seen = set()
    for m in _ARTICLE.finditer(html_text):
        href, yr, mo, raw_title = m.groups()
        if href in seen:
            continue
        title = _clean(raw_title)
        verb = _VERB.search(title)
        if not title or not verb:
            continue  # skip non-funding posts (reports, analysis, etc.)
        seen.add(href)
        startup = title[:verb.start()].strip(" -–—:")
        amt = _AMOUNT.search(title)
        rnd = _ROUND.search(title)
        out.append({
            "startup": startup or title,
            "title": title,
            "amount": amt.group(0).replace(" ", "") if amt else None,
            "round": rnd.group(0).strip().title() if rnd else None,
            "region": REGIONS.get(region_slug, region_slug.title()),
            "source_url": href,
            "posted_date": f"{yr}-{mo}-01",
        })
    return out


def scrape_finsmes(regions=None, timeout=30):
    """Scrape the given region slugs (default: all). Returns a flat list of rows.
    Best-effort per region — a failing region is logged and skipped."""
    import time
    from curl_cffi import requests as cffi
    slugs = regions or list(REGIONS.keys())
    rows = []
    for idx, slug in enumerate(slugs):
        if idx:
            time.sleep(2.5)   # pace requests — finsmes 429s on rapid-fire hits
        try:
            r = cffi.get(_BASE.format(slug=slug), impersonate="chrome", timeout=timeout)
            if r.status_code != 200:
                logger.warning("finsmes %s: HTTP %s", slug, r.status_code)
                continue
            found = parse_listing(r.text, slug)
            rows.extend(found)
            logger.info("finsmes %s: %d funding rows", slug, len(found))
        except Exception as e:
            logger.warning("finsmes %s scrape failed: %s", slug, e)
    return rows


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    for row in scrape_finsmes(["india"]):
        print(row["region"], "|", row["startup"], "|", row["amount"], "|", row["round"])
