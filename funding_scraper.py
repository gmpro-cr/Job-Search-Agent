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
_VERB = re.compile(r'\b(raises|raised|closes|secures|secured|lands|gets|bags|nets|completes|receives)\b', re.I)
_ROUND = re.compile(r'\b(pre-seed|seed|series\s+[a-h]|angel|growth|venture|debt|bridge|grant)\b', re.I)
_AMOUNT = re.compile(r'(?:\$|₹|€|£)\s?[\d.,]+\s?(?:million|billion|crore|cr|mn|bn|m|b|k)?', re.I)
# "<descriptor> startup NAME raises ..." — pull the name after the descriptor.
_DESC = re.compile(
    r'\b(?:startup|start-up|firm|company|platform|venture|maker|brand|app|provider|player)\s+'
    r'([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,2})\s+(?:rais|secur|clos|land|bag|net|get|complet)', re.I)
# Roundup / list / non-single-company posts to skip.
_ROUNDUP = re.compile(
    r'\b(biggest|top\s*\d|week[\'’]?s|weekly|round-?up|these\s+\d|\d+\s+(?:startups|deals|rounds|companies)|'
    r'funding news|list of|intelligence release|report)\b', re.I)
# Connective/temporal words a real company name never starts with — when the
# parser lands on one of these it has grabbed a roundup date-phrase, not a name.
_BAD_START = re.compile(
    r'^(between|from|during|over|after|before|this|these|those|here|today|'
    r'in|on|at|the|a|an|week|month|year|q[1-4])\b', re.I)


def _looks_like_name(s):
    """Reject obvious non-company strings (date phrases, digit-laden fragments,
    run-on clauses) the headline parser sometimes lands on."""
    if not s or any(ch.isdigit() for ch in s):
        return False
    if _BAD_START.match(s):
        return False
    return len(s.split()) <= 5


def _clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def parse_funding_title(title):
    """Extract {startup, amount, round} from a funding headline, or None if the
    title isn't a single-company funding event. Handles both clean finsmes
    titles ("X Raises $Ym in Series A") and messier news headlines
    ("... startup X raises seed funding")."""
    title = (title or "").strip()
    if not title or _ROUNDUP.search(title):
        return None
    verb = _VERB.search(title)
    if not verb:
        return None
    amt = _AMOUNT.search(title)
    rnd = _ROUND.search(title)
    desc = _DESC.search(title)
    if desc:
        startup = desc.group(1).strip(" -–—:\"")
    else:
        startup = title[:verb.start()].strip(" -–—:\"")
    if not _looks_like_name(startup):
        return None
    return {
        "startup": startup,
        "amount": amt.group(0).replace(" ", "") if amt else None,
        "round": rnd.group(0).strip().title() if rnd else None,
    }


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
        parsed = parse_funding_title(title)
        if not parsed:
            continue  # skip non-funding posts (reports, analysis, roundups)
        seen.add(href)
        out.append({
            **parsed,
            "title": title,
            "region": REGIONS.get(region_slug, region_slug.title()),
            "source": "FinSMEs",
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
            time.sleep(3.0)   # pace requests — finsmes 429s on rapid-fire hits
        try:
            r = cffi.get(_BASE.format(slug=slug), impersonate="chrome", timeout=timeout)
            if r.status_code == 429:
                # Rate-limited: back off once and retry.
                time.sleep(20)
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
