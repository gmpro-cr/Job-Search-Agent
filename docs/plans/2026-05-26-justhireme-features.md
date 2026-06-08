# JustHireMe Features Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add 5 zero-cost features inspired by JustHireMe: Quality Gate, Seniority Filter, Explainable Scoring, Keyword Gap badges, and ATS board scrapers (Greenhouse + Lever).

**Architecture:** All logic lives in `analyzer.py` and `scrapers.py`. Explainable scoring adds one new `score_breakdown TEXT` column to `job_listings` and a new API route. ATS scrapers use public unauthenticated JSON APIs — no new dependencies beyond `requests` which is already installed.

**Tech Stack:** Python (stdlib only for new logic), existing `requests` + `BeautifulSoup`, Flask routes, Jinja2 templates, existing `_add_columns_idempotent` DB helper.

---

## Task 1: Quality Gate

Pre-filter jobs before they enter the scoring pipeline. Reject thin, stale, or obviously wrong-fit postings.

**Files:**
- Modify: `analyzer.py` (add `quality_gate()` near top of file, after `IRRELEVANT_KEYWORDS`)
- Modify: `analyzer.py:548` — `analyze_jobs()` loop (call gate at top of loop)

**Step 1: Add `quality_gate()` to `analyzer.py` after the `IRRELEVANT_KEYWORDS` list (around line 96)**

```python
# Seniority overqualification patterns — roles clearly above IC PM target
_OVERQUALIFIED_PATTERNS = [
    r'\b(?:vp|vice\s+president|chief\s+\w+\s+officer|cxo|c-suite)\b',
    r'\b(?:director|managing\s+director|president)\b',
    r'\b(?:15|18|20)\s*\+?\s*(?:years?|yrs?)\b',
    r'minimum\s+(?:15|18|20)\s*years?',
]
_OVERQUALIFIED_RE = re.compile('|'.join(_OVERQUALIFIED_PATTERNS), re.IGNORECASE)

# Underqualified/irrelevant role patterns
_WRONG_LEVEL_PATTERNS = [
    r'\bfreshers?\s+only\b',
    r'\b0[\s-]1\s*(?:year|yr)\b',
    r'\binternship\s+(?:only|position|role|opportunity)\b',
]
_WRONG_LEVEL_RE = re.compile('|'.join(_WRONG_LEVEL_PATTERNS), re.IGNORECASE)


def quality_gate(job):
    """
    Pre-filter a job before scoring.
    Returns (passed: bool, reason: str).
    'passed=False' means the job should be skipped entirely.
    """
    desc = job.get("job_description") or ""
    role = job.get("role") or ""
    combined = f"{role} {desc}"

    # Reject thin descriptions (< 40 words)
    word_count = len(desc.split())
    if 0 < word_count < 40:
        return False, f"thin_description:{word_count}_words"

    # Reject clearly overqualified roles (VP, Director, 15+ yrs)
    if _OVERQUALIFIED_RE.search(combined):
        return False, "overqualified_level"

    # Reject fresher-only / intern-only postings
    if _WRONG_LEVEL_RE.search(combined):
        return False, "wrong_level"

    return True, ""
```

**Step 2: Call `quality_gate()` at the top of the `analyze_jobs()` loop**

In `analyze_jobs()` at line ~568, replace:
```python
    for i, job in enumerate(jobs):
        text = " ".join([
```
with:
```python
    for i, job in enumerate(jobs):
        passed, gate_reason = quality_gate(job)
        if not passed:
            logger.debug("Quality gate rejected %s @ %s: %s",
                         job.get("role"), job.get("company"), gate_reason)
            if progress_callback:
                progress_callback(i + 1, total, job.get("role", ""), 0)
            continue

        text = " ".join([
```

**Step 3: Run manual test**
```bash
cd /Users/gaurav/job-search-agent
python -c "
from analyzer import quality_gate
# Should reject — thin
print(quality_gate({'role': 'PM', 'job_description': 'short desc'}))
# Should reject — VP level
print(quality_gate({'role': 'VP Product', 'job_description': 'managing director VP role'}))
# Should reject — fresher only
print(quality_gate({'role': 'PM', 'job_description': 'freshers only 0-1 year experience welcome'}))
# Should pass
print(quality_gate({'role': 'Product Manager', 'job_description': ' '.join(['word'] * 50)}))
"
```
Expected: `(False, 'thin_description:2_words')`, `(False, 'overqualified_level')`, `(False, 'wrong_level')`, `(True, '')`

**Step 4: Commit**
```bash
git add analyzer.py
git commit -m "feat: add quality gate to reject thin/overqualified/wrong-level jobs before scoring"
```

---

## Task 2: Seniority Penalty in Scoring

Add a named seniority component to `keyword_score()`. When a JD requires 10+ years or VP/Director level, deduct 15 points and record the penalty in the breakdown.

**Files:**
- Modify: `analyzer.py:292` — `keyword_score()` function

**Step 1: Add seniority patterns constant after `_OVERQUALIFIED_RE` (Task 1 added this)**

No new constant needed — reuse `_OVERQUALIFIED_RE` from Task 1.

**Step 2: Add seniority penalty calculation inside `keyword_score()`**

In `keyword_score()`, after the transferable skills section (before the final `return`), add:

```python
    # Seniority penalty (0 to -15): JD targets a level above our candidate
    seniority_penalty = 0
    _SENIORITY_PENALTY_PATTERNS = [
        r'\b(?:10|12|15|18|20)\s*\+?\s*(?:years?|yrs?)\b',
        r'\b(?:vp|vice\s+president|chief|director|managing\s+director)\b',
        r'minimum\s+10\s*years?',
    ]
    _sen_re = re.compile('|'.join(_SENIORITY_PENALTY_PATTERNS), re.IGNORECASE)
    if _sen_re.search(text):
        seniority_penalty = -15
    score = max(0, score + seniority_penalty)
```

**Step 3: Run manual test**
```bash
cd /Users/gaurav/job-search-agent
python -c "
from analyzer import keyword_score
prefs = {'job_titles': ['Product Manager'], 'locations': ['Pune']}
# Normal job — no penalty
j1 = {'role': 'Product Manager', 'location': 'Pune', 'job_description': 'fintech payments product manager role 5 years experience'}
print('normal:', keyword_score(j1, prefs))
# Senior job with 10+ years — should score lower
j2 = {'role': 'VP Product', 'location': 'Pune', 'job_description': 'VP product manager fintech 10+ years required director level'}
print('senior:', keyword_score(j2, prefs))
"
```
Expected: normal score ≥ 65, senior score visibly lower (penalty applied).

**Step 4: Commit**
```bash
git add analyzer.py
git commit -m "feat: add seniority penalty to keyword_score for VP/Director/10+ yr roles"
```

---

## Task 3: Explainable Scoring

Replace the hardcoded fake score bars in the job card with real per-component data stored in the DB.

**Files:**
- Modify: `analyzer.py:292` — `keyword_score()` — add `breakdown=False` param
- Modify: `analyzer.py:548` — `analyze_jobs()` — store breakdown JSON on job dict
- Modify: `database.py:284` — `_extra_cols` list — add `score_breakdown TEXT`
- Modify: `app.py` — add `/api/jobs/<job_id>/score-breakdown` route
- Modify: `templates/_job_card_list.html:137` — score panel — load real data via JS

**Step 1: Add `breakdown=False` param to `keyword_score()` and return a dict when True**

Change the signature line of `keyword_score` to:
```python
def keyword_score(job, preferences, cv_data=None, breakdown=False):
```

Before the final `return min(score, 100)`, add:
```python
    if breakdown:
        return {
            "total": min(score, 100),
            "title": best_title_score,
            "location": location_score,
            "domain": min(industry_score, 30),
            "pm_keywords": min(pm_score, 20),
            "cv_skills": min(ts_score if transferable else 0, 15),
            "seniority_penalty": seniority_penalty,
            "irrelevant_penalty": -20 if any(kw in text for kw in IRRELEVANT_KEYWORDS) else 0,
        }
```

(You'll need to capture the intermediate variables — `location_score`, `ts_score` — in named vars rather than adding directly to `score`. Refactor the location block to: `location_score = 10 if ... else 0; score += location_score` and similarly for `ts_score`.)

**Step 2: Store breakdown in `analyze_jobs()` loop**

In `analyze_jobs()`, where `keyword_score` is called for the non-LLM path:
```python
        if not scored:
            breakdown = keyword_score(job, preferences, cv_data=cv_data, breakdown=True)
            job["relevance_score"] = breakdown["total"]
            job["score_breakdown"] = json.dumps(breakdown)
            ...
```

For the LLM path, after a successful `llm_score()`, build a partial breakdown:
```python
            job["score_breakdown"] = json.dumps({
                "total": job["relevance_score"],
                "llm": True,
                "reason": result.get("reason", ""),
            })
```

**Step 3: Add `score_breakdown TEXT` column to `job_listings` in `database.py`**

In the `_extra_cols` list (line ~284), append:
```python
        "score_breakdown TEXT",
        "quality_flag TEXT",
```

**Step 4: Save `score_breakdown` in `push_jobs()` / `save_job()`**

Find the `INSERT INTO job_listings` statement in `database.py` (the one used by the scraper) and add `score_breakdown` to its column list and placeholder. Also update the `UPDATE` path if it exists.

**Step 5: Add API route in `app.py`**

After the existing `/api/jobs/<job_id>/gap-analysis` route (~line 3514):
```python
@app.route("/api/jobs/<job_id>/score-breakdown")
def score_breakdown_api(job_id):
    uid = current_user_id()
    job = get_job_by_id(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    raw = (job.get("score_breakdown") or "")
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    return jsonify(data)
```

**Step 6: Replace hardcoded score dims in `_job_card_list.html`**

Replace the hardcoded `{% set dims = [...] %}` block (lines 149–165) with a JS-driven version. Add a `data-breakdown-url` attribute to the score panel div, and a JS function `loadScoreBreakdown(jobId)` called from `toggleScorePanel()`.

The JS function fetches `/api/jobs/<job_id>/score-breakdown` and renders component bars dynamically. Replace the Jinja static bars with an empty `<div id="breakdown-bars-{{ job.job_id }}">` placeholder.

Example JS (add to `jobs.html` or `base.html` script block):
```javascript
function loadScoreBreakdown(jobId) {
  const container = document.getElementById('breakdown-bars-' + jobId);
  if (!container || container.dataset.loaded) return;
  fetch('/api/jobs/' + jobId + '/score-breakdown')
    .then(r => r.json())
    .then(data => {
      if (data.llm) {
        container.innerHTML = `<p class="text-xs text-gray-500 italic">${data.reason || 'LLM scored — no breakdown available.'}</p>`;
      } else {
        const dims = [
          ['Title Match', data.title || 0, 30],
          ['Domain Fit', data.domain || 0, 30],
          ['PM Keywords', data.pm_keywords || 0, 20],
          ['Location', data.location || 0, 10],
          ['CV Skills', data.cv_skills || 0, 15],
        ];
        if (data.seniority_penalty) dims.push(['Seniority', data.seniority_penalty, 0]);
        container.innerHTML = dims.map(([label, val, max]) => `
          <div class="flex items-center gap-3">
            <span class="text-[11px] text-gray-500 w-28 flex-shrink-0">${label}</span>
            <div class="flex-1 bg-gray-200 rounded-full h-1">
              <div class="h-1 rounded-full ${val < 0 ? 'bg-red-400' : 'bg-gray-700'}"
                   style="width:${max ? Math.round(Math.abs(val)/max*100) : 0}%"></div>
            </div>
            <span class="text-[11px] font-semibold text-gray-500 w-8 text-right">${val}</span>
          </div>`).join('');
      }
      container.dataset.loaded = '1';
    });
}
```

Also update `toggleScorePanel()` in the existing JS to call `loadScoreBreakdown(jobId)` when the panel opens.

**Step 7: Test in browser**

```bash
cd /Users/gaurav/job-search-agent && python app.py
```
Open `http://localhost:5001/jobs` → click ••• on a job card → "Why this score?" → verify real component bars appear (not hardcoded fake ones).

**Step 8: Commit**
```bash
git add analyzer.py database.py app.py templates/_job_card_list.html
git commit -m "feat: explainable scoring — real per-component score breakdown stored and displayed"
```

---

## Task 4: Keyword Gap Badges on Job Card

The full gap panel already works (Tasks 1–3 didn't touch it). This task adds a compact "missing skills" badge row directly on the card face so users see gaps without opening a panel.

**Files:**
- Modify: `app.py:1706` — `_build_jobs_query` enrichment block — `job["_missing_top3"]` is already set here
- Modify: `templates/_job_card_list.html:33` — meta row — add missing skill badges

**Step 1: Verify `_missing_top3` is populated**

In `app.py` line ~1710, the enrichment already does:
```python
job["_missing_top3"] = gap.get("missing_skills", [])[:3]
```
This is already there — no change needed.

**Step 2: Add missing skill badges to the job card meta row**

In `_job_card_list.html`, after the closing `</div>` of the meta row (around line 52), add:
```html
  {% if job._missing_top3 %}
  <div class="flex flex-wrap gap-1.5 px-5 pb-3 -mt-1">
    <span class="text-[10px] text-gray-400 font-medium mr-0.5">Missing:</span>
    {% for skill in job._missing_top3 %}
    <span class="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200">{{ skill }}</span>
    {% endfor %}
  </div>
  {% endif %}
```

**Step 3: Test visually**

Run `python app.py`, open Jobs page. Cards where you're missing skills should show amber pill badges below the meta line. Cards with full skill match show nothing (clean).

**Step 4: Commit**
```bash
git add templates/_job_card_list.html
git commit -m "feat: show top-3 missing skills as badges on job card face"
```

---

## Task 5: ATS Board Scrapers (Greenhouse + Lever)

Both Greenhouse and Lever expose public unauthenticated JSON APIs for job postings. No API keys, no auth, no new packages — just `requests` (already installed).

**Target companies** (Indian fintech/tech known to use these ATS):

Lever companies: `razorpay`, `slice-1`, `jupiter-6`, `mypaisabazaar`  
Greenhouse companies: `cred`, `mfine`, `khatabook`

(These slugs are the company identifiers used in the ATS URLs. Verify each by opening `https://api.lever.co/v0/postings/<slug>?mode=json` in a browser before adding.)

**Files:**
- Modify: `scrapers.py` — add `scrape_greenhouse()` and `scrape_lever()` functions
- Modify: `scrapers.py` — add dispatch in `scrape_all_portals()`
- Modify: `config.json` — add `greenhouse` and `lever` portal configs

**Step 1: Add `scrape_lever()` to `scrapers.py`**

Add after the last scraper function (before `scrape_all_portals`):

```python
# ---------------------------------------------------------------------------
# Lever ATS scraper (public JSON API, no auth)
# ---------------------------------------------------------------------------

_LEVER_COMPANIES = [
    "razorpay",
    "slice-1",
    "jupiter-6",
    "mypaisabazaar",
]


def scrape_lever(config, preferences):
    """
    Scrape Lever ATS boards for target companies.
    API: https://api.lever.co/v0/postings/{company}?mode=json
    Returns list of normalised job dicts.
    """
    if not config.get("portals", {}).get("lever", {}).get("enabled", False):
        return []

    companies = config.get("portals", {}).get("lever", {}).get("companies", _LEVER_COMPANIES)
    user_titles = [t.lower() for t in preferences.get("job_titles", [])]
    timeout = config.get("portals", {}).get("lever", {}).get("timeout", 20)
    jobs = []

    for company in companies:
        url = f"https://api.lever.co/v0/postings/{company}?mode=json"
        try:
            resp = requests.get(url, timeout=timeout, headers={"User-Agent": random_ua()})
            if resp.status_code != 200:
                logger.warning("Lever %s returned %s", company, resp.status_code)
                continue
            postings = resp.json()
            if not isinstance(postings, list):
                continue
            for p in postings:
                role = p.get("text", "")
                # Filter by relevance: only include if title overlaps user preferences
                role_lower = role.lower()
                if user_titles and not any(t in role_lower for t in user_titles):
                    continue
                # Extract text from lists of content blocks
                desc_parts = []
                for section in (p.get("descriptionPlain") or p.get("description") or ""):
                    if isinstance(section, str):
                        desc_parts.append(section)
                description = " ".join(desc_parts) if desc_parts else (p.get("descriptionPlain") or "")
                location = (p.get("categories") or {}).get("location", "")
                jobs.append({
                    "portal": "lever",
                    "company": company.replace("-", " ").title(),
                    "role": role,
                    "location": location,
                    "job_description": description[:3000],
                    "apply_url": p.get("hostedUrl", ""),
                    "date_posted": p.get("createdAt", ""),
                    "salary": "",
                    "salary_currency": "INR",
                })
        except Exception as e:
            logger.warning("Lever scrape failed for %s: %s", company, e)
        random_delay(config)

    logger.info("Lever: scraped %d jobs across %d companies", len(jobs), len(companies))
    return jobs
```

**Step 2: Add `scrape_greenhouse()` to `scrapers.py`**

```python
# ---------------------------------------------------------------------------
# Greenhouse ATS scraper (public JSON API, no auth)
# ---------------------------------------------------------------------------

_GREENHOUSE_COMPANIES = [
    "cred",
    "mfine",
    "khatabook",
]


def scrape_greenhouse(config, preferences):
    """
    Scrape Greenhouse ATS boards for target companies.
    API: https://boards-api.greenhouse.io/v1/boards/{company}/jobs
    Returns list of normalised job dicts.
    """
    if not config.get("portals", {}).get("greenhouse", {}).get("enabled", False):
        return []

    companies = config.get("portals", {}).get("greenhouse", {}).get("companies", _GREENHOUSE_COMPANIES)
    user_titles = [t.lower() for t in preferences.get("job_titles", [])]
    timeout = config.get("portals", {}).get("greenhouse", {}).get("timeout", 20)
    jobs = []

    for company in companies:
        url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
        try:
            resp = requests.get(url, timeout=timeout, headers={"User-Agent": random_ua()})
            if resp.status_code != 200:
                logger.warning("Greenhouse %s returned %s", company, resp.status_code)
                continue
            data = resp.json()
            postings = data.get("jobs", [])
            for p in postings:
                role = p.get("title", "")
                role_lower = role.lower()
                if user_titles and not any(t in role_lower for t in user_titles):
                    continue
                location = (p.get("location") or {}).get("name", "")
                # Greenhouse jobs endpoint returns minimal data; full JD needs a separate call
                # Fetch full JD only for matching roles to keep request count low
                job_id = p.get("id")
                description = ""
                if job_id:
                    try:
                        jd_resp = requests.get(
                            f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}",
                            timeout=timeout,
                            headers={"User-Agent": random_ua()},
                        )
                        if jd_resp.status_code == 200:
                            jd_data = jd_resp.json()
                            # Strip HTML tags from content
                            raw_html = jd_data.get("content", "")
                            description = re.sub(r'<[^>]+>', ' ', raw_html)
                            description = re.sub(r'\s+', ' ', description).strip()
                    except Exception:
                        pass
                    time.sleep(1)  # be polite between JD fetches

                jobs.append({
                    "portal": "greenhouse",
                    "company": company.title(),
                    "role": role,
                    "location": location,
                    "job_description": description[:3000],
                    "apply_url": p.get("absolute_url", ""),
                    "date_posted": p.get("updated_at", ""),
                    "salary": "",
                    "salary_currency": "INR",
                })
        except Exception as e:
            logger.warning("Greenhouse scrape failed for %s: %s", company, e)
        random_delay(config)

    logger.info("Greenhouse: scraped %d jobs across %d companies", len(jobs), len(companies))
    return jobs
```

**Step 3: Add dispatch in `scrape_all_portals()`**

Find `scrape_all_portals()` in `scrapers.py`. It likely has a dict or sequence of `(name, scraper_fn)` pairs. Add:
```python
    results["lever"] = scrape_lever(config, preferences)
    results["greenhouse"] = scrape_greenhouse(config, preferences)
```

**Step 4: Add config entries to `config.json`**

Add inside the `"portals"` object:
```json
"lever": {
  "enabled": true,
  "timeout": 20,
  "companies": ["razorpay", "slice-1", "jupiter-6", "mypaisabazaar"]
},
"greenhouse": {
  "enabled": true,
  "timeout": 20,
  "companies": ["cred", "mfine", "khatabook"]
}
```

**Step 5: Verify APIs return data**

```bash
python -c "
import requests
# Test Lever
r = requests.get('https://api.lever.co/v0/postings/razorpay?mode=json', timeout=10)
print('Lever razorpay status:', r.status_code, 'jobs:', len(r.json()) if r.ok else 'N/A')
# Test Greenhouse
r = requests.get('https://boards-api.greenhouse.io/v1/boards/cred/jobs', timeout=10)
print('Greenhouse cred status:', r.status_code)
"
```
Expected: 200 status with job counts. If a company slug returns 404, remove it from the list or find the correct slug.

**Step 6: Quick scraper test**

```bash
cd /Users/gaurav/job-search-agent
python -c "
import json
config = json.load(open('config.json'))
prefs = {'job_titles': ['Product Manager', 'PM']}
from scrapers import scrape_lever, scrape_greenhouse
lever_jobs = scrape_lever(config, prefs)
print(f'Lever: {len(lever_jobs)} PM jobs')
gh_jobs = scrape_greenhouse(config, prefs)
print(f'Greenhouse: {len(gh_jobs)} PM jobs')
"
```

**Step 7: Commit**
```bash
git add scrapers.py config.json
git commit -m "feat: add Greenhouse and Lever ATS scrapers (public JSON APIs, no auth required)"
```

---

## Final Smoke Test

```bash
cd /Users/gaurav/job-search-agent
python app.py &
# In another terminal or browser:
# 1. Open http://localhost:5001/jobs
# 2. Click ••• → "Why this score?" — verify real component bars (not fake proportional bars)
# 3. Click ••• → "Skills Gap" — verify matched/missing skills appear
# 4. Check for amber "Missing:" badges on cards where CV skills don't match JD
# 5. Run a manual scrape: python scrape_and_push.py --portals lever,greenhouse
#    Verify lever/greenhouse jobs appear in the DB
```

---

## Summary of Changes

| File | What changed |
|---|---|
| `analyzer.py` | `quality_gate()` + `_OVERQUALIFIED_RE` + seniority penalty in `keyword_score()` + `breakdown=False` param |
| `database.py` | `score_breakdown TEXT`, `quality_flag TEXT` in `_extra_cols` |
| `app.py` | `/api/jobs/<job_id>/score-breakdown` route |
| `templates/_job_card_list.html` | Missing-skills badge row + real breakdown bars via JS |
| `scrapers.py` | `scrape_lever()` + `scrape_greenhouse()` + dispatch in `scrape_all_portals()` |
| `config.json` | `lever` + `greenhouse` portal configs |
