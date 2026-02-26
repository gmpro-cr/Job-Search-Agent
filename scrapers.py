"""
scrapers.py - Web scrapers for multiple job portals.
Each scraper returns a list of job dicts with standardized keys:
  portal, company, role, salary, salary_currency, location,
  job_description, apply_url
"""

import time
import random
import logging
import hashlib
import json
import os
from datetime import datetime, timedelta
from urllib.parse import quote_plus, urlencode

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# --- Cache ---
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_IS_VERCEL = bool(os.environ.get("VERCEL"))
if _IS_VERCEL:
    CACHE_DIR = "/tmp/.cache"
else:
    CACHE_DIR = os.path.join(os.environ.get("DATA_DIR", _BASE_DIR), ".cache")
try:
    os.makedirs(CACHE_DIR, exist_ok=True)
except OSError:
    pass

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]


def random_ua():
    return random.choice(USER_AGENTS)


def random_delay(config):
    """Sleep for a random delay between configured min and max seconds."""
    lo = config.get("scraping", {}).get("request_delay_min", 2)
    hi = config.get("scraping", {}).get("request_delay_max", 5)
    delay = random.uniform(lo, hi)
    time.sleep(delay)


def get_cache_path(url):
    h = hashlib.md5(url.encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{h}.html")


def get_cached(url, expiry_hours=12):
    """Return cached HTML if it exists and hasn't expired."""
    path = get_cache_path(url)
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < expiry_hours * 3600:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
    return None


def set_cache(url, html):
    path = get_cache_path(url)
    with open(path, "w", encoding="utf-8", errors="ignore") as f:
        f.write(html)


def fetch_url(url, config, use_selenium=False, retries=3, wait_selector=None):
    """
    Fetch a URL with retry logic and exponential backoff.
    Returns HTML string or None on failure.

    Args:
        wait_selector: CSS selector to wait for when using Selenium (SPA sites).
    """
    timeout = config.get("scraping", {}).get("portal_timeout", 30)

    # Check cache first
    cached = get_cached(url, config.get("scraping", {}).get("cache_expiry_hours", 12))
    if cached:
        logger.debug("Cache hit for %s", url)
        return cached

    if use_selenium:
        return fetch_with_selenium(url, timeout, retries, wait_selector=wait_selector)

    for attempt in range(1, retries + 1):
        try:
            headers = {
                "User-Agent": random_ua(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            }
            resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            resp.raise_for_status()
            html = resp.text
            set_cache(url, html)
            return html
        except requests.RequestException as e:
            wait = 2 ** attempt + random.uniform(0, 1)
            logger.warning(
                "Attempt %d/%d failed for %s: %s. Retrying in %.1fs",
                attempt, retries, url, e, wait,
            )
            if attempt < retries:
                time.sleep(wait)
    logger.error("All %d attempts failed for %s", retries, url)
    return None


def fetch_with_selenium(url, timeout=30, retries=3, wait_selector=None):
    """
    Fetch a JavaScript-heavy page using Selenium with bot-detection evasion.

    Args:
        wait_selector: CSS selector to wait for (e.g. job card elements).
            For SPA/Next.js sites, this ensures we don't grab the page before
            client-side data has loaded into the DOM.
    """
    for attempt in range(1, retries + 1):
        driver = None
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.common.by import By

            chrome_bin = os.environ.get("CHROME_BIN")
            chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")

            if chromedriver_path:
                service = Service(executable_path=chromedriver_path)
            else:
                try:
                    from webdriver_manager.chrome import ChromeDriverManager
                    service = Service(ChromeDriverManager().install())
                except Exception:
                    service = Service()

            options = Options()
            if chrome_bin:
                options.binary_location = chrome_bin
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument(f"user-agent={random_ua()}")
            options.add_argument("--window-size=1920,1080")
            # Bot-detection evasion
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)

            driver = webdriver.Chrome(service=service, options=options)
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
            )
            driver.set_page_load_timeout(timeout)
            driver.get(url)

            if wait_selector:
                # SPA mode: wait for specific DOM elements to appear
                try:
                    WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
                    )
                    logger.debug("Wait selector '%s' found on %s", wait_selector, url)
                except Exception:
                    logger.debug("Wait selector '%s' not found after 20s on %s, scrolling to trigger lazy load", wait_selector, url)
                    # Scroll down to trigger lazy-loading / intersection observers
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(3)
                    driver.execute_script("window.scrollTo(0, 0);")
                    time.sleep(2)
            else:
                # Generic mode: wait for page source to be substantial
                try:
                    WebDriverWait(driver, 10).until(
                        lambda d: len(d.page_source) > 5000
                    )
                except Exception:
                    pass
                time.sleep(3)

            html = driver.page_source
            set_cache(url, html)
            return html
        except Exception as e:
            wait = 2 ** attempt
            logger.warning(
                "Selenium attempt %d/%d failed for %s: %s", attempt, retries, url, e
            )
            if attempt < retries:
                time.sleep(wait)
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
    logger.error("All Selenium attempts failed for %s", url)
    return None


def check_portal_health(portal_name, url, config):
    """Quick health check - see if portal is reachable."""
    try:
        resp = requests.head(
            url,
            headers={"User-Agent": random_ua()},
            timeout=10,
            allow_redirects=True,
        )
        ok = resp.status_code < 400
        logger.info("Portal %s health: %s (status %d)", portal_name, "OK" if ok else "DOWN", resp.status_code)
        return ok
    except requests.RequestException as e:
        logger.warning("Portal %s health check failed: %s", portal_name, e)
        return False


# =============================================================================
# Individual Portal Scrapers
# =============================================================================


def scrape_linkedin(job_titles, locations, config):
    """
    Scrape LinkedIn public job listings.
    LinkedIn heavily blocks scraping, so this uses their public job search page
    which doesn't require login for initial listings.
    """
    portal_config = config.get("portals", {}).get("linkedin", {})
    if not portal_config.get("enabled", True):
        logger.info("LinkedIn scraping disabled in config")
        return []

    jobs = []
    base_url = portal_config.get("base_url", "https://www.linkedin.com/jobs/search/")
    max_pages = portal_config.get("max_pages", 3)
    use_selenium = portal_config.get("use_selenium", True)

    for title in job_titles:
        for location in locations:
            for page in range(max_pages):
                params = {
                    "keywords": title,
                    "location": location,
                    "start": page * 25,
                    "f_TPR": "r259200",  # Past 3 days
                }
                url = f"{base_url}?{urlencode(params)}"
                logger.info("Scraping LinkedIn: %s in %s (page %d)", title, location, page + 1)

                html = fetch_url(url, config, use_selenium=use_selenium)
                if not html:
                    continue

                try:
                    soup = BeautifulSoup(html, "lxml")

                    # LinkedIn public search cards
                    cards = soup.select("div.base-card, div.job-search-card, li.result-card")
                    if not cards:
                        cards = soup.select("[data-entity-urn]")

                    for card in cards:
                        try:
                            title_el = card.select_one(
                                "h3.base-search-card__title, "
                                "h3.job-search-card__title, "
                                "span.sr-only, "
                                "a.job-card-list__title"
                            )
                            company_el = card.select_one(
                                "h4.base-search-card__subtitle, "
                                "a.job-search-card__subtitle-link, "
                                "h4.job-search-card__company-name"
                            )
                            location_el = card.select_one(
                                "span.job-search-card__location, "
                                "span.base-search-card__metadata"
                            )
                            link_el = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
                            date_el = card.select_one("time[datetime], time")

                            role = title_el.get_text(strip=True) if title_el else None
                            company = company_el.get_text(strip=True) if company_el else None
                            loc = location_el.get_text(strip=True) if location_el else location
                            apply_url = link_el["href"] if link_el and link_el.has_attr("href") else None
                            date_posted = date_el.get("datetime", "") if date_el else ""

                            if role and company:
                                jobs.append({
                                    "portal": "LinkedIn",
                                    "company": company,
                                    "role": role,
                                    "salary": None,
                                    "salary_currency": "INR",
                                    "location": loc,
                                    "job_description": "",
                                    "apply_url": apply_url or f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(role + ' ' + company)}",
                                    "date_posted": date_posted,
                                })
                        except Exception as e:
                            logger.debug("Error parsing LinkedIn card: %s", e)
                            continue

                except Exception as e:
                    logger.error("Error parsing LinkedIn page: %s", e)

                random_delay(config)

    logger.info("LinkedIn: found %d jobs", len(jobs))
    return jobs


def _parse_indeed_initial_data(html):
    """Extract jobs from Indeed's embedded JSON: _initialData + mosaic providerData."""
    import re as _re
    jobs = []

    def _extract_json_at(html, start_idx):
        """Extract a balanced JSON object starting at start_idx."""
        depth = 0
        for i in range(start_idx, min(start_idx + 3_000_000, len(html))):
            if html[i] == "{":
                depth += 1
            elif html[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(html[start_idx:i + 1])
                    except (json.JSONDecodeError, TypeError):
                        return None
        return None

    def _find_job_results(obj, depth=0):
        """Recursively find a 'results' list containing job dicts."""
        if depth > 8 or not isinstance(obj, dict):
            return None
        for k, v in obj.items():
            if k == "results" and isinstance(v, list) and len(v) > 0:
                first = v[0]
                if isinstance(first, dict) and ("job" in first or "title" in first or "jobkey" in first):
                    return v
            if isinstance(v, dict):
                found = _find_job_results(v, depth + 1)
                if found:
                    return found
        return None

    # Collect JSON data sources to search for job results
    data_sources = []

    # Source 1: window._initialData
    for marker in ("window._initialData=", "window._initialData ="):
        idx = html.find(marker)
        if idx != -1:
            json_start = html.index("{", idx)
            data = _extract_json_at(html, json_start)
            if data and isinstance(data, dict):
                data_sources.append(data)
            break

    # Source 2: mosaic-provider-jobcards (primary source for card-level data with dates)
    for m in _re.finditer(r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*', html):
        json_start = html.index("{", m.end() - 1)
        data = _extract_json_at(html, json_start)
        if data and isinstance(data, dict):
            data_sources.append(data)

    # Extract results from all sources, dedup by jobkey
    seen_keys = set()
    all_results = []
    for data in data_sources:
        results = _find_job_results(data) or []
        for item in results:
            job_data = item.get("job") or item
            key = job_data.get("jobkey") or job_data.get("key", "")
            if key and key in seen_keys:
                continue
            if key:
                seen_keys.add(key)
            all_results.append(item)

    for item in all_results:
        try:
            job_data = item.get("job") or item
            title = job_data.get("title") or job_data.get("displayTitle", "")
            company = job_data.get("sourceEmployerName") or job_data.get("company") or job_data.get("truncatedCompany", "")
            raw_loc = job_data.get("formattedLocation") or job_data.get("location", "")
            if isinstance(raw_loc, dict):
                loc = (raw_loc.get("formatted") or {}).get("long") or raw_loc.get("fullAddress") or raw_loc.get("city", "")
            else:
                loc = raw_loc

            # Date: try datePublished (ms), pubDate (ms), createDate (ms), or formattedRelativeTime
            date_posted = ""
            for date_field in ("datePublished", "pubDate", "createDate"):
                date_ms = job_data.get(date_field)
                if date_ms and isinstance(date_ms, (int, float)) and date_ms > 1_000_000_000:
                    # Treat as ms if > 10^12, else seconds
                    ts = date_ms / 1000 if date_ms > 1e12 else date_ms
                    date_posted = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                    break
            if not date_posted:
                date_posted = _parse_relative_date(job_data.get("formattedRelativeTime", ""))

            job_key = job_data.get("key") or job_data.get("jobkey", "")
            apply_url = f"https://in.indeed.com/viewjob?jk={job_key}" if job_key else ""

            salary = job_data.get("salary") or job_data.get("salarySnippet") or {}
            salary_text = ""
            if isinstance(salary, dict):
                salary_text = salary.get("text") or salary.get("salaryTextFormatted") or ""

            # Strip HTML from snippet
            snippet = job_data.get("snippet") or ""
            if "<" in snippet:
                snippet = _re.sub(r"<[^>]+>", " ", snippet).strip()

            if title and company:
                jobs.append({
                    "portal": "Indeed",
                    "company": company,
                    "role": title,
                    "salary": salary_text or None,
                    "salary_currency": "INR",
                    "location": loc,
                    "job_description": snippet[:500],
                    "apply_url": apply_url,
                    "date_posted": date_posted,
                })
        except (TypeError, KeyError, AttributeError):
            continue

    return jobs


def _parse_relative_date(text):
    """Parse relative date text like '1 day ago', 'Today', 'Just posted' into YYYY-MM-DD."""
    if not text:
        return ""
    text_lower = text.lower()
    if "today" in text_lower or "just" in text_lower:
        return datetime.now().strftime("%Y-%m-%d")
    if "day" in text_lower:
        try:
            days = int("".join(c for c in text if c.isdigit()) or "0")
            return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        except ValueError:
            pass
    if "hour" in text_lower:
        return datetime.now().strftime("%Y-%m-%d")
    if "week" in text_lower:
        try:
            weeks = int("".join(c for c in text if c.isdigit()) or "1")
            return (datetime.now() - timedelta(weeks=weeks)).strftime("%Y-%m-%d")
        except ValueError:
            pass
    if "month" in text_lower:
        try:
            months = int("".join(c for c in text if c.isdigit()) or "1")
            return (datetime.now() - timedelta(days=months * 30)).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def scrape_indeed(job_titles, locations, config):
    """Scrape Indeed job listings."""
    portal_config = config.get("portals", {}).get("indeed", {})
    if not portal_config.get("enabled", True):
        logger.info("Indeed scraping disabled in config")
        return []

    jobs = []
    base_url = portal_config.get("base_url", "https://in.indeed.com/jobs")
    max_pages = portal_config.get("max_pages", 3)
    use_selenium = portal_config.get("use_selenium", False)

    for title in job_titles:
        for location in locations:
            for page in range(max_pages):
                params = {
                    "q": title,
                    "l": location,
                    "start": page * 10,
                    "fromage": 3,  # Past 3 days
                }
                url = f"{base_url}?{urlencode(params)}"
                logger.info("Scraping Indeed: %s in %s (page %d)", title, location, page + 1)

                html = fetch_url(url, config, use_selenium=use_selenium)
                if not html:
                    continue

                try:
                    # Strategy 1: Parse window._initialData JSON (best for dates)
                    json_jobs = _parse_indeed_initial_data(html)
                    if json_jobs:
                        jobs.extend(json_jobs)
                        logger.info("Indeed: extracted %d jobs via JSON from %s", len(json_jobs), url)
                        random_delay(config)
                        continue

                    # Strategy 2: Fallback to CSS card parsing
                    soup = BeautifulSoup(html, "lxml")

                    cards = soup.select(
                        "div.job_seen_beacon, "
                        "div.jobsearch-SerpJobCard, "
                        "div.cardOutline, "
                        "td.resultContent"
                    )

                    for card in cards:
                        try:
                            title_el = card.select_one(
                                "h2.jobTitle span[title], "
                                "h2.jobTitle a, "
                                "a.jcs-JobTitle"
                            )
                            company_el = card.select_one(
                                "span[data-testid='company-name'], "
                                "span.companyName, "
                                "span.company"
                            )
                            location_el = card.select_one(
                                "div[data-testid='text-location'], "
                                "div.companyLocation, "
                                "span.location"
                            )
                            salary_el = card.select_one(
                                "div.salary-snippet-container, "
                                "div.metadata.salary-snippet-container, "
                                "span.salary-snippet"
                            )
                            link_el = card.select_one("a[href*='/rc/clk'], a[data-jk], h2.jobTitle a")
                            date_el = card.select_one(
                                "span.date, "
                                "span[data-testid='myJobsStateDate'], "
                                "span.css-qvloho"
                            )

                            role = title_el.get_text(strip=True) if title_el else None
                            company = company_el.get_text(strip=True) if company_el else None
                            loc = location_el.get_text(strip=True) if location_el else location
                            salary = salary_el.get_text(strip=True) if salary_el else None
                            date_text = date_el.get_text(strip=True) if date_el else ""

                            href = None
                            if link_el and link_el.has_attr("href"):
                                href = link_el["href"]
                                if href.startswith("/"):
                                    href = f"https://in.indeed.com{href}"

                            date_posted = _parse_relative_date(date_text)

                            if role and company:
                                jobs.append({
                                    "portal": "Indeed",
                                    "company": company,
                                    "role": role,
                                    "salary": salary,
                                    "salary_currency": "INR",
                                    "location": loc,
                                    "job_description": "",
                                    "apply_url": href or f"https://in.indeed.com/jobs?q={quote_plus(role + ' ' + company)}",
                                    "date_posted": date_posted,
                                })
                        except Exception as e:
                            logger.debug("Error parsing Indeed card: %s", e)
                            continue

                except Exception as e:
                    logger.error("Error parsing Indeed page: %s", e)

                random_delay(config)

    logger.info("Indeed: found %d jobs", len(jobs))
    return jobs


def _naukri_ld_json_to_job(item):
    """Convert a JSON-LD JobPosting object to our standard job dict."""
    return {
        "portal": "Naukri",
        "company": (item.get("hiringOrganization") or {}).get("name", ""),
        "role": item.get("title", ""),
        "salary": None,
        "salary_currency": "INR",
        "location": (
            (item.get("jobLocation") or [{}])[0]
            .get("address", {})
            .get("addressLocality", "")
            if isinstance(item.get("jobLocation"), list)
            else (item.get("jobLocation") or {}).get("address", {}).get("addressLocality", "")
        ),
        "job_description": (item.get("description") or "")[:500],
        "apply_url": item.get("url", ""),
        "date_posted": item.get("datePosted", ""),
    }


def _parse_naukri_json(soup):
    """Try to extract jobs from JSON-LD or __NEXT_DATA__ before falling back to CSS."""
    jobs = []

    # Strategy 1: application/ld+json with JobPosting
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == "JobPosting" and item.get("title"):
                    job = _naukri_ld_json_to_job(item)
                    if job["role"] and job["company"]:
                        jobs.append(job)
        except (json.JSONDecodeError, TypeError):
            continue

    if jobs:
        return jobs

    # Strategy 2: __NEXT_DATA__ (Naukri is a React SPA)
    next_script = soup.select_one('script#__NEXT_DATA__')
    if next_script:
        try:
            next_data = json.loads(next_script.string or "")
            # Navigate the typical Naukri NEXT_DATA structure
            page_props = next_data.get("props", {}).get("pageProps", {})
            # Try common keys where job data lives
            for key in ("jobDetails", "searchResult", "jobfeed", "initialJobs", "jobs"):
                raw = page_props.get(key)
                if not raw:
                    continue
                items = raw if isinstance(raw, list) else raw.get("jobDetails", raw.get("jobs", []))
                if not isinstance(items, list):
                    continue
                for item in items:
                    role = item.get("title") or item.get("jobTitle") or item.get("designations") or ""
                    company = item.get("companyName") or item.get("company") or ""
                    if role and company:
                        raw_date = item.get("createdDate") or item.get("datePosted") or item.get("footerPlaceholderLabel", "")
                        # createdDate may be ISO format or relative text like "1 day ago"
                        date_posted = ""
                        if raw_date:
                            if len(raw_date) >= 10 and raw_date[4:5] == "-":
                                date_posted = raw_date[:10]  # Already YYYY-MM-DD
                            else:
                                date_posted = _parse_relative_date(raw_date)
                        jobs.append({
                            "portal": "Naukri",
                            "company": company,
                            "role": role,
                            "salary": item.get("salary") or item.get("placeholders", {}).get("salary"),
                            "salary_currency": "INR",
                            "location": item.get("location") or item.get("placeholders", {}).get("location", ""),
                            "job_description": (item.get("description") or item.get("jobDescription") or "")[:500],
                            "apply_url": item.get("jdURL") or item.get("url") or "",
                            "date_posted": date_posted,
                        })
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    return jobs


def scrape_naukri(job_titles, locations, config):
    """Scrape Naukri.com job listings (India's largest job portal)."""
    portal_config = config.get("portals", {}).get("naukri", {})
    if not portal_config.get("enabled", True):
        logger.info("Naukri scraping disabled in config")
        return []

    jobs = []
    max_pages = portal_config.get("max_pages", 3)
    use_selenium = portal_config.get("use_selenium", True)

    # CSS selectors for Naukri job cards (wait for these in Selenium)
    card_selector = (
        "article.jobTuple, div.srp-jobtuple-wrapper, "
        "div[class*='jobTuple'], div[class*='job-tuple'], "
        "div[class*='srp-tuple'], div[class*='cust-job-tuple']"
    )

    for title in job_titles:
        for location in locations:
            for page in range(1, max_pages + 1):
                title_slug = title.lower().replace(" ", "-")
                location_slug = location.lower().replace(" ", "-")
                url = f"https://www.naukri.com/{title_slug}-jobs-in-{location_slug}-{page}?jobAge=3"

                logger.info("Scraping Naukri: %s in %s (page %d)", title, location, page)

                html = fetch_url(url, config, use_selenium=use_selenium, wait_selector=card_selector)
                if not html:
                    continue

                try:
                    soup = BeautifulSoup(html, "lxml")

                    # Try JSON-first parsing
                    json_jobs = _parse_naukri_json(soup)
                    if json_jobs:
                        jobs.extend(json_jobs)
                        logger.info("Naukri: extracted %d jobs via JSON from %s", len(json_jobs), url)
                        random_delay(config)
                        continue

                    # Fallback: broadened CSS selectors
                    cards = soup.select(
                        "article.jobTuple, "
                        "div.srp-jobtuple-wrapper, "
                        "div.cust-job-tuple, "
                        "div[class*='jobTuple'], "
                        "div[class*='job-tuple'], "
                        "div[class*='srp-tuple']"
                    )

                    for card in cards:
                        try:
                            title_el = card.select_one(
                                "a.title, a[class*='title'], h2 a, "
                                "a[class*='jobTitle'], a[class*='designation']"
                            )
                            company_el = card.select_one(
                                "a.subTitle, a[class*='comp-name'], "
                                "span[class*='comp-name'], a[class*='companyName']"
                            )
                            location_el = card.select_one(
                                "span[class*='locWdth'], span[class*='loc-wrap'], "
                                "span[class*='location'], span[class*='loc'] span"
                            )
                            salary_el = card.select_one(
                                "span[class*='sal-wrap'] span, "
                                "span[class*='salary'], li[class*='salary'] span"
                            )
                            desc_el = card.select_one(
                                "div[class*='job-description'], "
                                "span[class*='job-description'], "
                                "div[class*='description']"
                            )
                            date_el = card.select_one(
                                "span.job-post-day, "
                                "span[class*='job-post-day'], "
                                "span[class*='postDay']"
                            )

                            role = title_el.get_text(strip=True) if title_el else None
                            company = company_el.get_text(strip=True) if company_el else None
                            loc = location_el.get_text(strip=True) if location_el else location
                            salary = salary_el.get_text(strip=True) if salary_el else None
                            description = desc_el.get_text(strip=True) if desc_el else ""
                            apply_url = title_el["href"] if title_el and title_el.has_attr("href") else None
                            date_text = date_el.get_text(strip=True) if date_el else ""
                            date_posted = _parse_relative_date(date_text)

                            if role and company:
                                jobs.append({
                                    "portal": "Naukri",
                                    "company": company,
                                    "role": role,
                                    "salary": salary,
                                    "salary_currency": "INR",
                                    "location": loc,
                                    "job_description": description,
                                    "apply_url": apply_url or f"https://www.naukri.com/{title_slug}-jobs",
                                    "date_posted": date_posted,
                                })
                        except Exception as e:
                            logger.debug("Error parsing Naukri card: %s", e)
                            continue

                except Exception as e:
                    logger.error("Error parsing Naukri page: %s", e)

                random_delay(config)

    logger.info("Naukri: found %d jobs", len(jobs))
    return jobs


def scrape_hiringcafe(job_titles, locations, config):
    """Scrape HiringCafe via its GET JSON API at /api/search-jobs?q=..."""
    portal_config = config.get("portals", {}).get("hiringcafe", {})
    if not portal_config.get("enabled", True):
        logger.info("HiringCafe scraping disabled in config")
        return []

    jobs = []
    base_url = portal_config.get("base_url", "https://hiring.cafe")
    api_url = f"{base_url}/api/search-jobs"
    timeout = portal_config.get("timeout", 30)
    page_size = portal_config.get("page_size", 50)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://hiring.cafe/",
    }

    for title in job_titles:
        logger.info("Scraping HiringCafe API: %s", title)

        try:
            resp = requests.get(
                api_url,
                params={"q": title, "size": page_size},
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("HiringCafe API request failed for '%s': %s", title, e)
            random_delay(config)
            continue

        raw_jobs = data.get("results") or data.get("jobs") or (data if isinstance(data, list) else [])
        if not isinstance(raw_jobs, list):
            logger.warning("HiringCafe: unexpected response shape for '%s'", title)
            random_delay(config)
            continue

        for item in raw_jobs:
            ji = item.get("job_information") or {}
            v5 = item.get("v5_processed_job_data") or {}
            ec = item.get("enriched_company_data") or {}

            role = (ji.get("title") or v5.get("core_job_title") or "").strip()
            company = (ec.get("name") or v5.get("company_name") or "").strip()
            if not role or not company:
                continue

            # Location: prefer formatted_workplace_location, fall back to cities list
            loc = v5.get("formatted_workplace_location") or ""
            if not loc:
                cities = v5.get("workplace_cities") or []
                loc = ", ".join(cities[:2]) if cities else ""

            # Salary: yearly range if present
            sal_min = v5.get("yearly_min_compensation")
            sal_max = v5.get("yearly_max_compensation")
            if sal_min or sal_max:
                salary = f"{sal_min or ''}-{sal_max or ''}".strip("-")
            else:
                salary = None
            currency = v5.get("listed_compensation_currency") or "USD"

            # Strip HTML from description
            raw_desc = ji.get("description") or ""
            if raw_desc:
                try:
                    raw_desc = BeautifulSoup(raw_desc, "lxml").get_text(" ", strip=True)
                except Exception:
                    pass
            desc = raw_desc[:500]

            jobs.append({
                "portal": "HiringCafe",
                "company": company,
                "role": role,
                "salary": salary,
                "salary_currency": currency,
                "location": loc,
                "job_description": desc,
                "apply_url": item.get("apply_url") or "",
                "date_posted": (v5.get("estimated_publish_date") or "")[:10],
                "remote_status": (v5.get("workplace_type") or "").lower() or None,
            })

        logger.info("HiringCafe: got %d jobs for '%s'", len(raw_jobs), title)
        random_delay(config)

    logger.info("HiringCafe: found %d jobs total", len(jobs))
    return jobs


def scrape_angellist(job_titles, locations, config):
    """Scrape Wellfound (formerly AngelList) for startup jobs."""
    portal_config = config.get("portals", {}).get("angellist", {})
    if not portal_config.get("enabled", True):
        logger.info("AngelList/Wellfound scraping disabled in config")
        return []

    jobs = []
    base_url = portal_config.get("base_url", "https://wellfound.com/jobs")
    use_selenium = portal_config.get("use_selenium", True)

    for title in job_titles:
        for location in locations:
            url = f"{base_url}?keywords={quote_plus(title)}&locations={quote_plus(location)}"
            logger.info("Scraping Wellfound: %s in %s", title, location)

            html = fetch_url(url, config, use_selenium=use_selenium)
            if not html:
                continue

            try:
                soup = BeautifulSoup(html, "lxml")

                cards = soup.select(
                    "div[class*='jobListing'], "
                    "div[class*='StartupResult'], "
                    "div[data-test='job-listing']"
                )

                for card in cards:
                    try:
                        title_el = card.select_one("h2, a[class*='title'], span[class*='jobTitle']")
                        company_el = card.select_one("h3, span[class*='company'], a[class*='company']")
                        location_el = card.select_one("span[class*='location']")
                        salary_el = card.select_one("span[class*='salary'], span[class*='compensation']")
                        link_el = card.select_one("a[href*='/jobs/']")

                        role = title_el.get_text(strip=True) if title_el else None
                        company = company_el.get_text(strip=True) if company_el else None
                        loc = location_el.get_text(strip=True) if location_el else location
                        salary = salary_el.get_text(strip=True) if salary_el else None
                        apply_url = link_el["href"] if link_el and link_el.has_attr("href") else None
                        if apply_url and not apply_url.startswith("http"):
                            apply_url = f"https://wellfound.com{apply_url}"

                        if role and company:
                            jobs.append({
                                "portal": "Wellfound",
                                "company": company,
                                "role": role,
                                "salary": salary,
                                "salary_currency": "USD",
                                "location": loc,
                                "job_description": "",
                                "apply_url": apply_url or url,
                            })
                    except Exception as e:
                        logger.debug("Error parsing Wellfound card: %s", e)
                        continue

            except Exception as e:
                logger.error("Error parsing Wellfound page: %s", e)

            random_delay(config)

    logger.info("Wellfound: found %d jobs", len(jobs))
    return jobs


def _iimjobs_ld_json_to_job(item):
    """Convert a JSON-LD JobPosting object from IIMJobs to our standard dict."""
    return {
        "portal": "IIMJobs",
        "company": (item.get("hiringOrganization") or {}).get("name", ""),
        "role": item.get("title", ""),
        "salary": None,
        "salary_currency": "INR",
        "location": (
            (item.get("jobLocation") or [{}])[0]
            .get("address", {})
            .get("addressLocality", "")
            if isinstance(item.get("jobLocation"), list)
            else (item.get("jobLocation") or {}).get("address", {}).get("addressLocality", "")
        ),
        "job_description": (item.get("description") or "")[:500],
        "apply_url": item.get("url", ""),
    }


def _parse_iimjobs_nextdata(soup, base_url):
    """Try to extract jobs from __NEXT_DATA__ or JSON-LD on IIMJobs pages."""
    jobs = []

    # Strategy 1: application/ld+json
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == "JobPosting" and item.get("title"):
                    job = _iimjobs_ld_json_to_job(item)
                    if job["role"] and job["company"]:
                        jobs.append(job)
        except (json.JSONDecodeError, TypeError):
            continue

    if jobs:
        return jobs

    # Strategy 2: __NEXT_DATA__
    next_script = soup.select_one('script#__NEXT_DATA__')
    if next_script:
        try:
            next_data = json.loads(next_script.string or "")
            page_props = next_data.get("props", {}).get("pageProps", {})
            for key in ("jobfeed", "jobs", "searchResults", "jobList", "initialJobs"):
                raw = page_props.get(key)
                if not raw:
                    continue
                items = raw if isinstance(raw, list) else raw.get("jobs", raw.get("data", []))
                if not isinstance(items, list):
                    continue
                for item in items:
                    role = item.get("title") or item.get("jobTitle") or item.get("heading") or ""
                    company = item.get("company") or item.get("companyName") or item.get("organization") or ""
                    if role and company:
                        link = item.get("url") or item.get("jobUrl") or item.get("slug", "")
                        if link and not link.startswith("http"):
                            link = f"{base_url}{link}"
                        jobs.append({
                            "portal": "IIMJobs",
                            "company": company,
                            "role": role,
                            "salary": item.get("salary") or item.get("ctc"),
                            "salary_currency": "INR",
                            "location": item.get("location") or item.get("city") or "",
                            "job_description": (item.get("description") or item.get("snippet") or "")[:500],
                            "apply_url": link,
                        })
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    return jobs


def scrape_iimjobs(job_titles, locations, config):
    """Scrape IIMJobs for MBA/experienced professional jobs."""
    portal_config = config.get("portals", {}).get("iimjobs", {})
    if not portal_config.get("enabled", True):
        logger.info("IIMJobs scraping disabled in config")
        return []

    jobs = []
    base_url = portal_config.get("base_url", "https://www.iimjobs.com")
    max_pages = portal_config.get("max_pages", 2)
    use_selenium = portal_config.get("use_selenium", True)

    # CSS selectors for IIMJobs job cards (wait for these in Selenium)
    card_selector = (
        "div[class*='job-card'], div[class*='jobCard'], "
        "div[class*='job-listing'], div[class*='jobTuple'], "
        "a[class*='job-card'], a[class*='jobCard'], "
        "li[class*='job'], div[class*='listing']"
    )

    for title in job_titles:
        for location in locations:
            url = f"{base_url}/search?q={quote_plus(title)}&l={quote_plus(location)}"
            logger.info("Scraping IIMJobs: %s in %s", title, location)

            html = fetch_url(url, config, use_selenium=use_selenium, wait_selector=card_selector)
            if not html:
                continue

            try:
                soup = BeautifulSoup(html, "lxml")

                # Try JSON-first parsing
                json_jobs = _parse_iimjobs_nextdata(soup, base_url)
                if json_jobs:
                    jobs.extend(json_jobs)
                    logger.info("IIMJobs: extracted %d jobs via JSON from %s", len(json_jobs), url)
                    random_delay(config)
                    continue

                # Fallback: broadened CSS selectors
                cards = soup.select(
                    "div.job-listing, "
                    "div.jobTuple, "
                    "div[class*='job-card'], "
                    "div[class*='job-listing'], "
                    "div[class*='jobCard'], "
                    "li.listing, "
                    "li[class*='job']"
                )

                for card in cards:
                    try:
                        title_el = card.select_one(
                            "h2 a, h3 a, a.job-title, a[class*='title'], "
                            "a[class*='jobTitle'], a[class*='heading']"
                        )
                        company_el = card.select_one(
                            "span.company, div.company, a[class*='company'], "
                            "span[class*='company'], span[class*='org']"
                        )
                        location_el = card.select_one(
                            "span.location, div.location, span[class*='loc'], "
                            "span[class*='location'], span[class*='city']"
                        )
                        salary_el = card.select_one(
                            "span.salary, div.salary, span[class*='sal'], "
                            "span[class*='salary'], span[class*='ctc']"
                        )
                        desc_el = card.select_one(
                            "div.description, p.desc, span.desc, "
                            "div[class*='description'], span[class*='snippet']"
                        )

                        role = title_el.get_text(strip=True) if title_el else None
                        company = company_el.get_text(strip=True) if company_el else None
                        loc = location_el.get_text(strip=True) if location_el else location
                        salary = salary_el.get_text(strip=True) if salary_el else None
                        description = desc_el.get_text(strip=True) if desc_el else ""
                        apply_url = title_el["href"] if title_el and title_el.has_attr("href") else None
                        if apply_url and not apply_url.startswith("http"):
                            apply_url = f"{base_url}{apply_url}"

                        if role and company:
                            jobs.append({
                                "portal": "IIMJobs",
                                "company": company,
                                "role": role,
                                "salary": salary,
                                "salary_currency": "INR",
                                "location": loc,
                                "job_description": description,
                                "apply_url": apply_url or url,
                            })
                    except Exception as e:
                        logger.debug("Error parsing IIMJobs card: %s", e)
                        continue

            except Exception as e:
                logger.error("Error parsing IIMJobs page: %s", e)

            random_delay(config)

    logger.info("IIMJobs: found %d jobs", len(jobs))
    return jobs


def scrape_cutshort(job_titles, locations, config):
    """
    Scrape jobs from Cutshort.io using their public search API.
    Returns list of job dicts.
    """
    import requests as _req
    import time as _time

    portal_config = config.get("portals", {}).get("cutshort", {})
    if not portal_config.get("enabled", True):
        return []

    jobs = []
    seen_ids = set()
    session = _req.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://cutshort.io/",
    })

    for title in job_titles:
        for location in locations:
            try:
                resp = session.get(
                    "https://cutshort.io/api/public/jobs",
                    params={"keywords": title, "location": location, "limit": 20},
                    timeout=portal_config.get("timeout", 20),
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
                items = data.get("data", data.get("jobs", []))
                for item in items:
                    jid = str(item.get("id") or item.get("_id") or "")
                    if jid in seen_ids:
                        continue
                    seen_ids.add(jid)
                    job = {
                        "portal": "cutshort",
                        "company": (item.get("company") or {}).get("name") or item.get("companyName") or "",
                        "role": item.get("title") or item.get("role") or title,
                        "location": item.get("location") or location,
                        "salary": item.get("salary") or "",
                        "job_description": (item.get("description") or "")[:500],
                        "apply_url": f"https://cutshort.io/job/{jid}" if jid else "",
                        "remote_status": "remote" if "remote" in str(item.get("location", "")).lower() else "",
                        "date_posted": item.get("createdAt", "")[:10] if item.get("createdAt") else "",
                    }
                    if job["role"] and job["company"]:
                        jobs.append(job)
            except Exception as e:
                logger.warning("Cutshort scrape error (title=%s, loc=%s): %s", title, location, e)
            _time.sleep(1)

    logger.info("Cutshort: scraped %d jobs", len(jobs))
    return jobs


def scrape_instahyre(job_titles, locations, config):
    """
    Scrape jobs from Instahyre using their JSON search endpoint.
    Returns list of job dicts.
    """
    import requests as _req
    import time as _time

    portal_config = config.get("portals", {}).get("instahyre", {})
    if not portal_config.get("enabled", True):
        return []

    jobs = []
    seen_ids = set()
    session = _req.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.instahyre.com/search-jobs/",
    })

    for title in job_titles:
        for location in locations:
            try:
                resp = session.get(
                    "https://www.instahyre.com/api/v1/employer_search/",
                    params={
                        "designation": title,
                        "location": location if location.lower() != "remote" else "",
                        "page": 1,
                    },
                    timeout=portal_config.get("timeout", 25),
                )
                if resp.status_code != 200:
                    logger.warning("Instahyre returned %d for %s/%s", resp.status_code, title, location)
                    continue
                data = resp.json()
                items = data.get("results", data.get("jobs", []))
                for item in items:
                    jid = str(item.get("id") or item.get("job_id") or "")
                    if jid in seen_ids:
                        continue
                    seen_ids.add(jid)
                    company_info = item.get("company") or {}
                    salary_min_lpa = item.get("min_salary") or item.get("salary_min")
                    salary_max_lpa = item.get("max_salary") or item.get("salary_max")
                    salary_text = ""
                    if salary_min_lpa and salary_max_lpa:
                        salary_text = f"₹{salary_min_lpa}–{salary_max_lpa} LPA"
                    job = {
                        "portal": "instahyre",
                        "company": company_info.get("name") or item.get("company_name") or "",
                        "role": item.get("designation") or item.get("title") or title,
                        "location": item.get("location") or location,
                        "salary": salary_text,
                        "salary_min": int(float(salary_min_lpa) * 100_000) if salary_min_lpa else None,
                        "salary_max": int(float(salary_max_lpa) * 100_000) if salary_max_lpa else None,
                        "job_description": (item.get("description") or item.get("jd") or "")[:500],
                        "apply_url": f"https://www.instahyre.com/job-details/{jid}/" if jid else "",
                        "remote_status": "remote" if "remote" in str(item.get("location", "")).lower() else "",
                        "experience_min": item.get("min_experience"),
                        "experience_max": item.get("max_experience"),
                        "company_size": str(company_info.get("employee_count") or ""),
                        "date_posted": (item.get("created_at") or "")[:10],
                    }
                    if job["role"] and job["company"]:
                        jobs.append(job)
            except Exception as e:
                logger.warning("Instahyre scrape error (title=%s, loc=%s): %s", title, location, e)
            _time.sleep(1.5)

    logger.info("Instahyre: scraped %d jobs", len(jobs))
    return jobs


# =============================================================================
# Orchestrator
# =============================================================================

SCRAPER_MAP = {
    "linkedin": scrape_linkedin,
    "indeed": scrape_indeed,
    "naukri": scrape_naukri,
    "hiringcafe": scrape_hiringcafe,
    "angellist": scrape_angellist,
    "iimjobs": scrape_iimjobs,
    "cutshort": scrape_cutshort,
    "instahyre": scrape_instahyre,
}


def _normalize_company_name(name):
    """Normalize company name by stripping common suffixes and noise."""
    import re as _re
    if not name:
        return ""
    name = name.lower().strip()
    # Strip common suffixes
    for suffix in [
        r'\bpvt\.?\s*ltd\.?', r'\bprivate\s+limited', r'\blimited', r'\bltd\.?',
        r'\binc\.?', r'\bcorp\.?', r'\bcorporation', r'\bllc', r'\bllp',
        r'\btechnologies', r'\bsolutions', r'\bservices', r'\bconsulting',
        r'\bglobal', r'\bindia', r'\b\(india\)',
    ]:
        name = _re.sub(suffix, '', name)
    # Collapse whitespace
    name = _re.sub(r'\s+', ' ', name).strip().rstrip('.,- ')
    return name


def _fuzzy_role_match(role1, role2):
    """Check if two role titles are fuzzy matches via substring or word overlap."""
    if not role1 or not role2:
        return False
    r1 = role1.lower().strip()
    r2 = role2.lower().strip()
    # Exact match
    if r1 == r2:
        return True
    # Substring match
    if r1 in r2 or r2 in r1:
        return True
    # Word overlap >= 80%
    words1 = set(r1.split())
    words2 = set(r2.split())
    if not words1 or not words2:
        return False
    overlap = len(words1 & words2)
    shorter = min(len(words1), len(words2))
    if shorter > 0 and overlap / shorter >= 0.8:
        return True
    return False


def deduplicate_jobs(jobs):
    """Remove duplicate jobs using fuzzy company name and role matching."""
    unique = []
    seen = []  # List of (normalized_company, role, location) tuples
    for job in jobs:
        norm_company = _normalize_company_name(job["company"])
        role = job["role"]
        loc = (job.get("location") or "").lower().strip()

        is_dup = False
        for seen_company, seen_role, seen_loc in seen:
            if norm_company == seen_company and _fuzzy_role_match(role, seen_role):
                is_dup = True
                break
        if not is_dup:
            seen.append((norm_company, role, loc))
            unique.append(job)
    return unique


def scrape_all_portals(job_titles, locations, config, progress_callback=None):
    """
    Scrape all enabled portals using threading for parallelism.
    Returns (all_jobs, portal_results) where portal_results is a dict of
    portal_name -> {"status": "success"/"failed", "count": int, "time": float}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    thread_count = config.get("scraping", {}).get("thread_count", 4)
    portal_results = {}
    all_jobs = []

    enabled_portals = []
    for portal_name, portal_conf in config.get("portals", {}).items():
        if portal_conf.get("enabled", True) and portal_name in SCRAPER_MAP:
            enabled_portals.append(portal_name)

    total = len(enabled_portals)
    completed = 0

    def run_scraper(portal_name):
        start_time = time.time()
        try:
            scraper_fn = SCRAPER_MAP[portal_name]
            jobs = scraper_fn(job_titles, locations, config)
            elapsed = time.time() - start_time
            return portal_name, jobs, "success", elapsed
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error("Portal %s failed: %s", portal_name, e)
            return portal_name, [], "failed", elapsed

    # Run health checks first
    logger.info("Running portal health checks...")
    for portal_name in enabled_portals:
        base_url = config["portals"][portal_name].get("base_url", "")
        if base_url:
            check_portal_health(portal_name, base_url, config)

    logger.info("Starting scraping from %d portals with %d threads", total, thread_count)

    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        futures = {
            executor.submit(run_scraper, name): name
            for name in enabled_portals
        }
        for future in as_completed(futures):
            portal_name, jobs, status, elapsed = future.result()
            completed += 1
            portal_results[portal_name] = {
                "status": status,
                "count": len(jobs),
                "time": round(elapsed, 1),
            }
            all_jobs.extend(jobs)
            if progress_callback:
                progress_callback(portal_name, status, len(jobs), completed, total)
            logger.info(
                "Portal %s: %s (%d jobs in %.1fs) [%d/%d]",
                portal_name, status, len(jobs), elapsed, completed, total,
            )

    # Deduplicate
    before_dedup = len(all_jobs)
    all_jobs = deduplicate_jobs(all_jobs)
    dupes_removed = before_dedup - len(all_jobs)

    succeeded = sum(1 for r in portal_results.values() if r["status"] == "success")
    failed = sum(1 for r in portal_results.values() if r["status"] == "failed")

    logger.info(
        "Scraping session ended: %d portals succeeded, %d failed. "
        "Found %d jobs, %d duplicates removed, %d unique jobs.",
        succeeded, failed, before_dedup, dupes_removed, len(all_jobs),
    )

    return all_jobs, portal_results
