"""
contact_scraper.py - Contact enrichment via web scraping (no API key required).
Uses four strategies in order:
  0. LinkedIn job detail page: parse "Meet the hiring team" / job poster section
     (requires LinkedIn credentials in preferences for best results)
  1. Extract contacts from the job description text itself
  2. Scrape the company's website (careers/about/team page)
  3. Google search for HR contacts at the company
"""
import json
import re
import time
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus, urljoin

logger = logging.getLogger(__name__)

# Request headers to appear as a browser
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
_LINKEDIN_RE = re.compile(r'linkedin\.com/in/([\w-]+)', re.IGNORECASE)

# Domains to skip when found in JD text (noisy/irrelevant emails)
_BLOCKED_EMAIL_DOMAINS = {"example.com", "test.com", "domain.com", "email.com", "yourcompany.com"}


def extract_contacts_from_text(text):
    """
    Extract email addresses and LinkedIn profile URLs from raw text.

    Returns:
        tuple: (emails: list[str], linkedin_urls: list[str])
    """
    if not text:
        return [], []

    raw_emails = _EMAIL_RE.findall(text)
    emails = [e for e in raw_emails if e.split("@")[-1].lower() not in _BLOCKED_EMAIL_DOMAINS]

    linkedin_matches = _LINKEDIN_RE.findall(text)
    linkedin_urls = [f"https://linkedin.com/in/{m}" for m in linkedin_matches]

    return list(dict.fromkeys(emails)), list(dict.fromkeys(linkedin_urls))  # dedupe preserving order


def _scrape_page_for_contacts(url, timeout=8):
    """Fetch a URL and extract emails/LinkedIn URLs from its text content."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        # Remove script/style noise
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator=" ")
        return extract_contacts_from_text(text)
    except Exception as e:
        logger.debug("Failed to scrape %s: %s", url, e)
        return [], []


def _try_company_website(company_name, apply_url=None):
    """
    Try to scrape the company's careers or about page for contacts.
    Uses the apply_url domain as a starting point if available.
    """
    emails, linkedin_urls = [], []

    # Derive company domain from apply URL if possible
    domain = None
    if apply_url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(apply_url)
            host = parsed.netloc.lstrip("www.")
            # Skip known job boards
            known_boards = {"linkedin.com", "naukri.com", "indeed.com", "wellfound.com",
                            "hiringcafe.com", "iimjobs.com", "instahyre.com", "angel.co"}
            if host and not any(board in host for board in known_boards):
                domain = host
        except Exception:
            pass

    if not domain:
        return emails, linkedin_urls

    # Try company contact/about/team pages
    candidate_paths = ["/about", "/team", "/contact", "/careers/contact", "/about-us"]
    for path in candidate_paths:
        url = f"https://{domain}{path}"
        e, l = _scrape_page_for_contacts(url)
        emails.extend(e)
        linkedin_urls.extend(l)
        if emails or linkedin_urls:
            break
        time.sleep(0.5)

    return list(dict.fromkeys(emails)), list(dict.fromkeys(linkedin_urls))


def _google_search_contacts(company_name):
    """
    Search Google for HR/recruiter contacts at a company.
    Parses only the result snippets (no JS rendering needed).
    """
    query = f'"{company_name}" recruiter OR "talent acquisition" OR "HR manager" email'
    url = f"https://www.google.com/search?q={quote_plus(query)}&num=5"

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        # Extract text from result snippets only
        snippets = []
        for div in soup.find_all("div", class_=re.compile(r"BNeawe|s3v9rd|VwiC3b")):
            snippets.append(div.get_text())
        text = " ".join(snippets)
        return extract_contacts_from_text(text)
    except Exception as e:
        logger.debug("Google search failed for %s: %s", company_name, e)
        return [], []


def _build_selenium_options():
    """Build Selenium Chrome options with bot-detection evasion."""
    from scrapers import random_ua
    from selenium.webdriver.chrome.options import Options
    import os

    options = Options()
    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument(f"user-agent={random_ua()}")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    return options


def _build_selenium_service():
    """Build Selenium ChromeDriver service."""
    import os
    from selenium.webdriver.chrome.service import Service

    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")
    if chromedriver_path:
        return Service(executable_path=chromedriver_path)
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        return Service(ChromeDriverManager().install())
    except Exception:
        return Service()


def create_linkedin_session(email, password, timeout=30):
    """
    Create a Selenium Chrome driver that is logged into LinkedIn.

    Tries cached cookies first. On cookie miss or failure, performs a fresh login
    and saves the cookies for subsequent calls.

    Returns:
        selenium.webdriver.Chrome driver logged into LinkedIn, or None on failure.
    """
    import os
    from scrapers import CACHE_DIR
    from selenium import webdriver
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.by import By

    cookies_path = os.path.join(CACHE_DIR, "linkedin_cookies.json")

    def _new_driver():
        drv = webdriver.Chrome(service=_build_selenium_service(), options=_build_selenium_options())
        drv.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
        )
        drv.set_page_load_timeout(timeout)
        return drv

    # ── Try cached cookies ──────────────────────────────────────────────────
    if os.path.exists(cookies_path):
        try:
            driver = _new_driver()
            # Load cookies into the browser
            driver.get("https://www.linkedin.com")
            time.sleep(1)
            with open(cookies_path) as f:
                cookies = json.load(f)
            for cookie in cookies:
                try:
                    driver.add_cookie(cookie)
                except Exception:
                    pass
            driver.get("https://www.linkedin.com/feed/")
            time.sleep(2)
            if "/feed" in driver.current_url or "/mynetwork" in driver.current_url:
                logger.info("LinkedIn: session restored from cached cookies")
                return driver
            # Cookies expired — clean up and fall through to fresh login
            driver.quit()
            os.remove(cookies_path)
        except Exception as e:
            logger.debug("LinkedIn cookie restore failed: %s", e)
            try:
                driver.quit()
            except Exception:
                pass

    # ── Fresh login ─────────────────────────────────────────────────────────
    driver = None
    try:
        driver = _new_driver()
        driver.get("https://www.linkedin.com/login")

        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "username"))
        )
        driver.find_element(By.ID, "username").send_keys(email)
        driver.find_element(By.ID, "password").send_keys(password)
        driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

        # Wait for redirect to feed or checkpoint (CAPTCHA/verification)
        WebDriverWait(driver, timeout).until(
            lambda d: any(p in d.current_url for p in ["/feed", "/checkpoint", "/uas/login-submit"])
        )
        time.sleep(2)

        if "/feed" in driver.current_url or "/mynetwork" in driver.current_url:
            # Save cookies for future use
            try:
                with open(cookies_path, "w") as f:
                    json.dump(driver.get_cookies(), f)
                logger.info("LinkedIn: logged in successfully, cookies saved")
            except Exception as e:
                logger.debug("LinkedIn: could not save cookies: %s", e)
            return driver

        elif "/checkpoint" in driver.current_url:
            logger.warning(
                "LinkedIn: security checkpoint detected after login. "
                "A verification step (email/phone/CAPTCHA) is required. "
                "Complete it manually in a real browser, then try again."
            )
            driver.quit()
            return None
        else:
            logger.warning("LinkedIn: unexpected URL after login: %s", driver.current_url)
            driver.quit()
            return None

    except Exception as e:
        logger.warning("LinkedIn login failed: %s", e)
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        return None


def _extract_linkedin_jsonld(soup):
    """Extract job description and org name from LinkedIn JSON-LD on a job detail page."""
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
            if data.get("@type") == "JobPosting":
                return {
                    "description": (data.get("description") or "").strip(),
                    "company": (data.get("hiringOrganization") or {}).get("name", ""),
                }
        except (json.JSONDecodeError, TypeError):
            continue
    return {}


def _parse_hiring_team_from_soup(soup):
    """
    Try to extract the hiring manager / job poster from a LinkedIn job page.
    Requires the user to be logged in — the section is not rendered for guests.

    Returns (name, profile_url) or ("", "").
    """
    name = ""
    profile_url = ""

    # ── Selector set 1: hirer-card (classic public layout) ───────────────────
    hirer_link = soup.select_one("a.hirer-card__hirer-information")
    if hirer_link:
        name_el = hirer_link.select_one(".hirer-card__hirer-name")
        name = name_el.get_text(strip=True) if name_el else hirer_link.get_text(strip=True)
        href = hirer_link.get("href", "")
        if "/in/" in href:
            profile_url = ("https://www.linkedin.com" + href) if href.startswith("/") else href

    # ── Selector set 2: jobs-poster section ──────────────────────────────────
    if not name:
        poster_section = soup.select_one("div.jobs-poster, section.jobs-poster")
        if poster_section:
            link = poster_section.select_one("a[href*='/in/']")
            if link:
                href = link.get("href", "")
                profile_url = ("https://www.linkedin.com" + href) if href.startswith("/") else href
                name_el = link.select_one("span, strong, div")
                name = name_el.get_text(strip=True) if name_el else link.get_text(strip=True)

    # ── Selector set 3: hiring-team module (newer LinkedIn UI) ───────────────
    if not name:
        for section in soup.select(
            "[data-module-id*='hiring'], .hiring-team, "
            "section[class*='hiring'], div[class*='hiring-team']"
        ):
            link = section.select_one("a[href*='/in/']")
            if link:
                href = link.get("href", "")
                profile_url = ("https://www.linkedin.com" + href) if href.startswith("/") else href
                name_el = link.select_one("span[class*='name'], strong, span")
                name = name_el.get_text(strip=True) if name_el else link.get_text(strip=True)
                break

    # ── Selector set 4: face-pile card (another newer variant) ───────────────
    if not name:
        card = soup.select_one("div.face-pile-card, div.base-main-card--link")
        if card:
            link = card.select_one("a[href*='/in/']")
            if link:
                href = link.get("href", "")
                profile_url = ("https://www.linkedin.com" + href) if href.startswith("/") else href
            name_el = card.select_one(
                "h3.base-main-card__title, .base-main-card__title, "
                "span[class*='name'], h3, strong"
            )
            name = name_el.get_text(strip=True) if name_el else ""

    # ── Selector set 5: "Meet the hiring team" section (logged-in UI) ────────
    if not name:
        for section in soup.select(
            "section[class*='job-details-how-you-match'], "
            "div[class*='jobs-job-details'], "
            "div[class*='job-details-module']"
        ):
            text = section.get_text()
            if "hiring team" in text.lower() or "meet" in text.lower():
                link = section.select_one("a[href*='/in/']")
                if link:
                    href = link.get("href", "")
                    profile_url = ("https://www.linkedin.com" + href) if href.startswith("/") else href
                    name_el = link.select_one("span[class*='name'], strong, span")
                    name = name_el.get_text(strip=True) if name_el else link.get_text(strip=True)
                    break

    # ── Selector set 6: any /in/ profile link with a plausible person name ───
    # (last resort — only if no other selector fired)
    if not name:
        for a in soup.select("a[href*='/in/'], a[href^='/in/']"):
            text = a.get_text(strip=True)
            href = a.get("href", "")
            # Must look like a real name: 2+ words, not too long, no URL-like chars
            if (text and 3 < len(text) < 60
                    and " " in text
                    and not any(c in text for c in ["@", "?", "=", "/", ",", "."])):
                name = text
                profile_url = ("https://www.linkedin.com" + href) if href.startswith("/") else href
                break

    return name, profile_url


def scrape_linkedin_job_poster(job_url, timeout=30, driver=None):
    """
    Fetch a LinkedIn job detail page and extract:
      - The person who posted the job ("Meet the hiring team" section)
      - The job description from JSON-LD (public, no login needed)

    When `driver` is a logged-in Selenium WebDriver, the hiring team section
    is visible. Without a logged-in driver, only the job description can be
    extracted (the hiring team section requires authentication).

    Args:
        job_url:  LinkedIn job detail URL.
        timeout:  Seconds to wait for the page to load.
        driver:   An already-logged-in selenium.webdriver.Chrome instance,
                  or None to use a fresh anonymous session.

    Returns:
        dict with keys:
          poster_name     (str)  — person's name or ""
          poster_linkedin (str)  — LinkedIn profile URL or ""
          jd_text         (str)  — job description from JSON-LD or ""
        or None if the page could not be fetched.
    """
    if not job_url or "linkedin.com" not in job_url:
        return None

    html = None
    owns_driver = False

    try:
        if driver is not None:
            # Use the provided logged-in driver
            try:
                driver.get(job_url)
                time.sleep(3)   # allow React to render hiring team section
                html = driver.page_source
            except Exception as e:
                logger.debug("LinkedIn job page fetch via provided driver failed for %s: %s", job_url, e)
                return None
        else:
            # Fall back to anonymous Selenium session
            from scrapers import fetch_with_selenium
            html = fetch_with_selenium(job_url, timeout=timeout, retries=2)
            if not html:
                logger.debug("LinkedIn Selenium fetch returned nothing for %s", job_url)
                return None

        soup = BeautifulSoup(html, "lxml")

        # Always extract job description from JSON-LD (public, no login needed)
        jsonld = _extract_linkedin_jsonld(soup)
        jd_text = jsonld.get("description", "")

        # Try to find the hiring manager (only works when logged in)
        name, profile_url = _parse_hiring_team_from_soup(soup)

        if name:
            logger.info("LinkedIn poster found for %s: %s (%s)", job_url[:60], name, profile_url)
        elif jd_text:
            logger.debug("LinkedIn: no poster found but extracted JD (%d chars) for %s", len(jd_text), job_url[:60])
        else:
            logger.debug("No LinkedIn data found for %s", job_url)
            return None

        return {
            "poster_name": name,
            "poster_linkedin": profile_url,
            "jd_text": jd_text,
        }

    except Exception as e:
        logger.debug("LinkedIn job page fetch failed for %s: %s", job_url, e)
        return None


def enrich_jobs_with_contacts(jobs_needing_contacts, linkedin_email=None, linkedin_password=None):
    """
    Enrich jobs with recruiter contact data using a free multi-strategy scraper.

    Args:
        jobs_needing_contacts: list of dicts with at least
            {job_id, company, job_description, apply_url}
        linkedin_email:    LinkedIn account email (optional; enables hiring team scraping)
        linkedin_password: LinkedIn account password (optional)

    Returns:
        dict mapping job_id -> {poster_name, poster_email, poster_phone, poster_linkedin, jd_text}
    """
    results = {}
    company_cache = {}

    # ── Create a single LinkedIn session for all LinkedIn jobs (if creds provided) ──
    linkedin_driver = None
    linkedin_jobs = [
        job for job in jobs_needing_contacts
        if "linkedin.com" in (job.get("apply_url") or "")
    ]
    if linkedin_jobs and linkedin_email and linkedin_password:
        logger.info("LinkedIn credentials set — attempting to log in for hiring team scraping")
        linkedin_driver = create_linkedin_session(linkedin_email, linkedin_password)
        if linkedin_driver:
            logger.info("LinkedIn session active; will scrape hiring team for %d jobs", len(linkedin_jobs))
        else:
            logger.warning("LinkedIn login failed — will scrape without authentication (no hiring team)")

    try:
        for job in jobs_needing_contacts:
            company = job.get("company", "").strip()
            job_id = job.get("job_id", "")
            if not company or not job_id:
                continue

            poster_name = ""
            emails, linkedin_urls = [], []
            jd_text = ""

            # Strategy 0: LinkedIn job detail page — scrape the job poster + extract JD
            apply_url = job.get("apply_url", "")
            if "linkedin.com" in (apply_url or ""):
                linkedin_result = scrape_linkedin_job_poster(
                    apply_url, driver=linkedin_driver
                )
                if linkedin_result:
                    poster_name = linkedin_result.get("poster_name", "")
                    li_url = linkedin_result.get("poster_linkedin", "")
                    jd_text = linkedin_result.get("jd_text", "")
                    if li_url:
                        linkedin_urls = [li_url]
                time.sleep(1)  # polite delay

            # Strategy 1: Extract directly from the JD text (fastest, zero network calls)
            if not emails and not linkedin_urls:
                jd_src = job.get("job_description") or jd_text
                emails, lk = extract_contacts_from_text(jd_src)
                if lk and not linkedin_urls:
                    linkedin_urls = lk

            if not emails and not linkedin_urls:
                # Strategy 2 + 3 — cache by company to avoid duplicate scrapes
                cache_key = company.lower()
                if cache_key not in company_cache:
                    w_emails, w_linkedin = _try_company_website(company, apply_url)
                    time.sleep(1)  # polite delay between companies

                    g_emails, g_linkedin = [], []
                    if not w_emails and not w_linkedin:
                        g_emails, g_linkedin = _google_search_contacts(company)
                        time.sleep(2)  # slightly longer delay for Google

                    company_cache[cache_key] = (w_emails + g_emails, w_linkedin + g_linkedin)

                emails, linkedin_urls = company_cache[cache_key]

            if poster_name or emails or linkedin_urls or jd_text:
                results[job_id] = {
                    "poster_name": poster_name,
                    "poster_email": emails[0] if emails else "",
                    "poster_phone": "",
                    "poster_linkedin": linkedin_urls[0] if linkedin_urls else "",
                    "jd_text": jd_text,
                }
                logger.info("Found contact for %s at %s: name=%s email=%s jd=%d chars",
                            company, job_id, poster_name or "—",
                            emails[0] if emails else "—", len(jd_text))

    finally:
        if linkedin_driver:
            try:
                linkedin_driver.quit()
                logger.debug("LinkedIn driver closed")
            except Exception:
                pass

    logger.info(
        "Contact enrichment: %d/%d jobs got contacts (%d unique companies scraped)",
        len(results),
        len(jobs_needing_contacts),
        len(company_cache),
    )
    return results
