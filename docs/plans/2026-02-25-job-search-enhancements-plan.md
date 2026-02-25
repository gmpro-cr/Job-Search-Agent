# Job Search Agent Enhancements — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Surface hidden database fields on job cards, add missing filters, rebuild the CV page into a Profile Hub with skills gap + keyword heatmap, add split-pane job browsing, smarter dashboard insights, and two new scrapers (Cutshort + Instahyre).

**Architecture:** All DB data already exists. Changes are frontend-heavy (Jinja2 templates + vanilla JS) with a handful of new Flask API endpoints and two new scraper functions. No new Python packages required.

**Tech Stack:** Flask, Jinja2, Tailwind CSS, vanilla JS, SQLite, requests, selenium (existing)

---

## Task 1: Add company_stage filter to backend query builder

**Files:**
- Modify: `app.py:515-651` (`_build_jobs_query`) and `app.py:654-704` (`jobs()` route)

**Step 1: Open app.py and find `_build_jobs_query` at line 515**

The function reads filter params from `filters` dict and builds SQL. We need to add `company_stage` as a new filter param.

**Step 2: Add `company_stage` to the filter extraction block (around line 533)**

Find this block (lines 523–534):
```python
    search = filters.get("search", "")
    portal = filters.get("portal", "")
    remote = filters.get("remote", "")
    company_type = filters.get("company_type", "")
    sort = filters.get("sort", "date_desc")
    applied = filters.get("applied", "")
    location = filters.get("location", "")
    recency = filters.get("recency", "")
    min_score = filters.get("min_score", "0")
    experience = filters.get("experience", "")
    salary_min = filters.get("salary_min", "")
    salary_max = filters.get("salary_max", "")
```

Add one line at the end of this block:
```python
    company_stage = filters.get("company_stage", "")
```

**Step 3: Add SQL condition for company_stage (after the salary_max block, around line 640)**

Find the `if salary_max:` block ending around line 640. After it, add:
```python
    if company_stage:
        conditions.append("company_funding_stage = ?")
        params.append(company_stage)
```

**Step 4: Add `company_stage` to the filters dict in the `jobs()` route (around line 668)**

In `jobs()` at line 654, add `company_stage` to the `filters` dict:
```python
        "company_stage": request.args.get("company_stage", ""),
```

**Step 5: Verify by running the app and checking no errors**

```bash
cd /Users/gaurav/job-search-agent
python -c "from app import app; print('OK')"
```
Expected: `OK`

**Step 6: Commit**
```bash
git add app.py
git commit -m "feat: add company_stage filter to job query builder"
```

---

## Task 2: Enhance job cards — date posted, company stage badge, formatted salary

**Files:**
- Modify: `templates/jobs.html:186-237` (job card body)
- Modify: `templates/jobs.html:6-44` (add CSS for new badges)

**Step 1: Add CSS for new badges in the `<style>` block (after line 33, the `tag-yoe` rule)**

Add these lines:
```css
  .tag-stage-seed    { background:#fef9c3; border:1px solid #fde047; border-radius:999px; padding:2px 9px; font-size:11px; color:#854d0e; display:inline-block; }
  .tag-stage-series  { background:#dbeafe; border:1px solid #93c5fd; border-radius:999px; padding:2px 9px; font-size:11px; color:#1d4ed8; display:inline-block; }
  .tag-stage-large   { background:#f1f5f9; border:1px solid #cbd5e1; border-radius:999px; padding:2px 9px; font-size:11px; color:#475569; display:inline-block; }
  .tag-date          { font-size:10px; color:#a1a1aa; }
```

**Step 2: Replace the job card body (lines 186–236) with the enhanced version**

Replace everything between `<div class="p-3 flex-1 flex flex-col">` and the closing `</div>` of that block with:

```html
    <div class="p-3 flex-1 flex flex-col">

      <!-- Row 1: Avatar + company + score -->
      <div class="flex items-center justify-between mb-2">
        <div class="flex items-center gap-2">
          <div class="w-7 h-7 rounded-lg flex-shrink-0 flex items-center justify-center text-xs font-bold text-white"
               style="background:{{ avatar_bg }}">
            {{ (job.company or '?')[0] | upper }}
          </div>
          <div>
            <p class="text-xs font-medium text-gray-900 leading-tight">{{ job.company | truncate(20, true, '…') }}</p>
            <p class="hc-mono text-[10px] text-gray-400">{{ job.portal }}</p>
          </div>
        </div>
        <div class="flex flex-col items-end gap-1">
          <span class="hc-mono text-xs font-bold tabular-nums px-1.5 py-0.5 rounded
            {% if score >= 80 %}score-high{% elif score >= 65 %}score-mid{% else %}score-low{% endif %}">
            {{ score }}
          </span>
          {% if job.date_posted %}
          <span class="tag-date relative-date" data-date="{{ job.date_posted }}">{{ job.date_posted[:10] }}</span>
          {% endif %}
        </div>
      </div>

      <!-- Row 2: Job title -->
      <h3 class="text-sm font-semibold text-gray-900 leading-snug line-clamp-2 mb-2">{{ job.role }}</h3>

      <!-- Row 3: Location + type + experience tags -->
      <div class="flex flex-wrap items-center gap-1 mb-1">
        {% if job.location %}
        <span class="text-[11px] text-gray-400">{{ job.location | truncate(16, true, '…') }}</span>
        {% endif %}
        {% if job.remote_status == 'remote' %}<span class="tag-remote">Remote</span>
        {% elif job.remote_status == 'hybrid' %}<span class="tag-hybrid">Hybrid</span>
        {% else %}<span class="tag-pill">Onsite</span>{% endif %}
        {% if job.experience_min is not none and job.experience_max is not none %}
          <span class="tag-yoe">{{ job.experience_min }}–{{ job.experience_max }} yrs</span>
        {% elif job.experience_min is not none %}
          <span class="tag-yoe">{{ job.experience_min }}+ yrs</span>
        {% endif %}
      </div>

      <!-- Row 4: Company stage + size -->
      {% if job.company_funding_stage or job.company_size %}
      <div class="flex flex-wrap gap-1 mb-1">
        {% if job.company_funding_stage %}
          {% set stage = job.company_funding_stage | lower %}
          {% if 'seed' in stage or 'pre' in stage or 'bootstrap' in stage %}
            <span class="tag-stage-seed">{{ job.company_funding_stage }}</span>
          {% elif 'series' in stage %}
            <span class="tag-stage-series">{{ job.company_funding_stage }}</span>
          {% else %}
            <span class="tag-stage-large">{{ job.company_funding_stage }}</span>
          {% endif %}
        {% endif %}
        {% if job.company_size %}
        <span class="tag-stage-large">{{ job.company_size }}</span>
        {% endif %}
      </div>
      {% endif %}

      <!-- Row 5: Salary -->
      {% if job.salary_min and job.salary_max %}
      <p class="hc-mono text-xs font-semibold text-emerald-600 mt-1">
        ₹{{ (job.salary_min / 100000) | int }}L – ₹{{ (job.salary_max / 100000) | int }}L/yr
      </p>
      {% elif job.salary %}
      <p class="hc-mono text-xs font-semibold text-emerald-600 mt-1">{{ job.salary_currency or '₹' }}{{ job.salary }}</p>
      {% endif %}

    </div>
```

**Step 3: Verify the template renders without Jinja errors**
```bash
cd /Users/gaurav/job-search-agent
python -c "
from app import app
with app.test_client() as c:
    r = c.get('/jobs')
    print('Status:', r.status_code)
"
```
Expected: `Status: 200`

**Step 4: Commit**
```bash
git add templates/jobs.html
git commit -m "feat: enhance job cards with date posted, company stage, salary range"
```

---

## Task 3: Add company_stage filter to the "More filters" UI

**Files:**
- Modify: `templates/jobs.html:114-151` (more-filters section)

**Step 1: Find the more-filters grid (line 114)**

The grid currently has 5 filter selects: portal, location, recency, experience, min_score.

**Step 2: Add company_stage select as the 6th filter**

Change the grid to `lg:grid-cols-6` and add the select after the min_score select (before the closing `</div>`):

Replace:
```html
  <div id="more-filters" class="hidden mt-3 pt-3 border-t border-gray-100 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
```
With:
```html
  <div id="more-filters" class="hidden mt-3 pt-3 border-t border-gray-100 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
```

Then add after the `min_score` select (before the closing `</div>` of the grid):
```html
    <select name="company_stage" class="text-sm rounded-lg border-gray-200 focus:border-indigo-400 focus:ring-indigo-400 border p-2">
      <option value="">Any stage</option>
      <option value="Seed" {% if filters.company_stage == 'Seed' %}selected{% endif %}>Seed</option>
      <option value="Pre-Seed" {% if filters.company_stage == 'Pre-Seed' %}selected{% endif %}>Pre-Seed</option>
      <option value="Series A" {% if filters.company_stage == 'Series A' %}selected{% endif %}>Series A</option>
      <option value="Series B" {% if filters.company_stage == 'Series B' %}selected{% endif %}>Series B</option>
      <option value="Series C" {% if filters.company_stage == 'Series C' %}selected{% endif %}>Series C</option>
      <option value="Series D+" {% if filters.company_stage == 'Series D+' %}selected{% endif %}>Series D+</option>
      <option value="IPO/Public" {% if filters.company_stage == 'IPO/Public' %}selected{% endif %}>IPO/Public</option>
      <option value="Bootstrapped" {% if filters.company_stage == 'Bootstrapped' %}selected{% endif %}>Bootstrapped</option>
    </select>
```

Also update the "auto-show more-filters" JS check on line 262 to include `company_stage`:
```javascript
  const advanced = ['portal', 'location', 'recency', 'experience', 'min_score', 'salary_min', 'salary_max', 'company_type', 'company_stage'];
```

**Step 3: Verify**
```bash
python -c "
from app import app
with app.test_client() as c:
    r = c.get('/jobs?company_stage=Series+A')
    print('Status:', r.status_code)
"
```
Expected: `Status: 200`

**Step 4: Commit**
```bash
git add templates/jobs.html
git commit -m "feat: add company stage filter to jobs page"
```

---

## Task 4: Inline top-3 missing skills on job cards (no modal needed)

**Files:**
- Modify: `app.py:654-704` (`jobs()` route) — pass gap data
- Modify: `templates/jobs.html:184-240` (job card) — show missing skills inline

**Step 1: Modify the `jobs()` route in app.py to attach gap data**

In `jobs()` (line 654), after fetching `rows` (line 682), add:

```python
    # Attach inline gap data if CV is uploaded
    cv_data = load_cv_data()
    if cv_data:
        for job in rows:
            gap = compute_gap_analysis(job, cv_data)
            job["_missing_top3"] = gap.get("missing_skills", [])[:3]
            job["_cv_score"] = gap.get("cv_score", 0)
    else:
        for job in rows:
            job["_missing_top3"] = []
            job["_cv_score"] = 0
```

Make sure `compute_gap_analysis` and `load_cv_data` are imported at the top of app.py. Check line ~36 where imports happen:
```python
from analyzer import (analyze_jobs, keyword_score, parse_cv_text,
                      load_cv_data, save_cv_data, cv_score,
                      compute_gap_analysis)
```

**Step 2: Add inline gap preview to job card (in templates/jobs.html)**

After the salary row (after the `{% endif %}` closing the salary block), add before the closing `</div>` of `.p-3.flex-1.flex.flex-col`:

```html
      <!-- Row 6: Inline gap preview -->
      {% if job._missing_top3 %}
      <div class="mt-2 pt-2 border-t border-gray-50">
        <p class="text-[10px] text-gray-400 mb-1">Missing skills:</p>
        <div class="flex flex-wrap gap-1">
          {% for skill in job._missing_top3 %}
          <span class="px-1.5 py-0.5 bg-red-50 text-red-500 border border-red-100 rounded text-[10px]">{{ skill }}</span>
          {% endfor %}
        </div>
      </div>
      {% elif job._cv_score > 0 %}
      <div class="mt-2 pt-2 border-t border-gray-50">
        <p class="text-[10px] {% if job._cv_score >= 70 %}text-green-600{% elif job._cv_score >= 40 %}text-amber-600{% else %}text-gray-400{% endif %}">
          CV match: {{ job._cv_score }}%
        </p>
      </div>
      {% endif %}
```

**Step 3: Verify**
```bash
python -c "
from app import app
with app.test_client() as c:
    r = c.get('/jobs')
    print('Status:', r.status_code)
    assert b'Missing skills' in r.data or b'CV match' in r.data or b'job-card' in r.data
    print('OK')
"
```
Expected: `Status: 200` then `OK`

**Step 4: Commit**
```bash
git add app.py templates/jobs.html
git commit -m "feat: show inline gap analysis (missing skills + CV match) on job cards"
```

---

## Task 5: Skills gap API endpoint + database helper

**Files:**
- Modify: `database.py` — add `get_skill_frequency(job_titles)` function at end of file
- Modify: `app.py` — add `GET /api/cv/skills-gap` route

**Step 1: Add `get_skill_frequency` to database.py (after line 834)**

```python
def get_skill_frequency(job_titles: list, limit: int = 50) -> list:
    """
    Return skills appearing most frequently across jobs matching the given titles.
    Returns list of dicts: {skill, count, pct} sorted by count desc.
    """
    import re as _re
    conn = get_connection()
    cursor = conn.cursor()

    if job_titles:
        like_clauses = " OR ".join("LOWER(role) LIKE ?" for _ in job_titles)
        params = [f"%{t.lower()}%" for t in job_titles]
        cursor.execute(
            f"SELECT job_description, role FROM job_listings WHERE ({like_clauses}) AND (hidden = 0 OR hidden IS NULL)",
            params,
        )
    else:
        cursor.execute("SELECT job_description, role FROM job_listings WHERE (hidden = 0 OR hidden IS NULL)")

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return []

    total_jobs = len(rows)
    skill_counts: dict = {}

    # Import skill patterns from analyzer
    from analyzer import extract_skills
    for row in rows:
        text = " ".join([row["role"] or "", row["job_description"] or ""])
        skills = extract_skills(text, max_skills=None)
        for skill in skills:
            skill_counts[skill] = skill_counts.get(skill, 0) + 1

    sorted_skills = sorted(skill_counts.items(), key=lambda x: -x[1])[:limit]
    return [
        {"skill": skill, "count": count, "pct": round(count / total_jobs * 100, 1)}
        for skill, count in sorted_skills
    ]


def get_keyword_frequency(job_titles: list, top_n: int = 30) -> list:
    """
    Return most frequent meaningful words in job descriptions for the given titles.
    Returns list of dicts: {word, count} sorted desc.
    """
    import re as _re
    from collections import Counter

    STOP_WORDS = {
        'the','and','for','with','our','you','your','will','this','that','are',
        'from','have','has','been','was','were','they','their','which','about',
        'into','more','also','than','can','all','any','its','not','but','who',
        'what','how','when','work','team','role','job','experience','skills',
        'looking','strong','ability','must','good','years','year','working',
        'based','across','including','company','position','candidate','knowledge',
        'manage','ensure','provide','support','help','using','use','used',
        'new','key','high','well','large','great','multiple','other','join',
    }

    conn = get_connection()
    cursor = conn.cursor()

    if job_titles:
        like_clauses = " OR ".join("LOWER(role) LIKE ?" for _ in job_titles)
        params = [f"%{t.lower()}%" for t in job_titles]
        cursor.execute(
            f"SELECT job_description FROM job_listings WHERE ({like_clauses}) AND (hidden = 0 OR hidden IS NULL)",
            params,
        )
    else:
        cursor.execute("SELECT job_description FROM job_listings WHERE (hidden = 0 OR hidden IS NULL)")

    rows = cursor.fetchall()
    conn.close()

    counter: Counter = Counter()
    for row in rows:
        text = (row["job_description"] or "").lower()
        words = _re.findall(r'\b[a-z]{4,}\b', text)
        for w in words:
            if w not in STOP_WORDS:
                counter[w] += 1

    return [{"word": w, "count": c} for w, c in counter.most_common(top_n)]
```

**Step 2: Add 3 new API routes to app.py (after the `/api/cv/rescore` route, around line 1132)**

```python
@app.route("/api/cv/skills-gap")
def cv_skills_gap():
    """Return skill frequency across target-role jobs vs CV skills."""
    cv_data = load_cv_data()
    preferences = load_preferences() or DEFAULT_PREFS.copy()
    job_titles = preferences.get("job_titles", [])

    from database import get_skill_frequency
    skill_freq = get_skill_frequency(job_titles)

    cv_skills_lower = {s.lower() for s in (cv_data or {}).get("skills", [])}
    result = []
    for item in skill_freq:
        result.append({
            "skill": item["skill"],
            "count": item["count"],
            "pct": item["pct"],
            "in_cv": item["skill"].lower() in cv_skills_lower,
        })
    return jsonify({"ok": True, "skills": result, "cv_uploaded": cv_data is not None})


@app.route("/api/cv/keyword-heatmap")
def cv_keyword_heatmap():
    """Return top keywords across target-role jobs."""
    preferences = load_preferences() or DEFAULT_PREFS.copy()
    job_titles = preferences.get("job_titles", [])

    from database import get_keyword_frequency
    keywords = get_keyword_frequency(job_titles)
    return jsonify({"ok": True, "keywords": keywords})


@app.route("/api/cv/profile-score")
def cv_profile_score():
    """Return a simple profile completeness score 0-100."""
    cv_data = load_cv_data()
    preferences = load_preferences() or DEFAULT_PREFS.copy()

    score = 0
    breakdown = []

    if cv_data:
        score += 30
        breakdown.append({"label": "CV uploaded", "points": 30, "done": True})
    else:
        breakdown.append({"label": "Upload your CV", "points": 30, "done": False})

    skills = (cv_data or {}).get("skills", [])
    if len(skills) >= 5:
        score += 20
        breakdown.append({"label": f"{len(skills)} skills detected", "points": 20, "done": True})
    else:
        breakdown.append({"label": "CV needs more skills (aim for 5+)", "points": 20, "done": False})

    has_prefs = bool(preferences.get("job_titles") and preferences.get("locations"))
    if has_prefs:
        score += 20
        breakdown.append({"label": "Preferences configured", "points": 20, "done": True})
    else:
        breakdown.append({"label": "Set job titles & locations in Preferences", "points": 20, "done": False})

    has_salary = bool(preferences.get("salary_min") or preferences.get("salary_expectation"))
    if has_salary:
        score += 15
        breakdown.append({"label": "Salary expectation set", "points": 15, "done": True})
    else:
        breakdown.append({"label": "Add salary expectation in Preferences", "points": 15, "done": False})

    has_gmail = bool(preferences.get("gmail_address") and preferences.get("gmail_app_password"))
    if has_gmail:
        score += 15
        breakdown.append({"label": "Email alerts configured", "points": 15, "done": True})
    else:
        breakdown.append({"label": "Configure Gmail in Preferences for email alerts", "points": 15, "done": False})

    return jsonify({"ok": True, "score": score, "breakdown": breakdown})
```

**Step 3: Verify endpoints respond**
```bash
cd /Users/gaurav/job-search-agent
python -c "
from app import app
with app.test_client() as c:
    for url in ['/api/cv/skills-gap', '/api/cv/keyword-heatmap', '/api/cv/profile-score']:
        r = c.get(url)
        print(url, r.status_code)
"
```
Expected: all three print `200`

**Step 4: Commit**
```bash
git add app.py database.py
git commit -m "feat: add skills-gap, keyword-heatmap, profile-score API endpoints"
```

---

## Task 6: Rebuild CV page into Profile Hub

**Files:**
- Replace: `templates/cv.html` (full rewrite)

**Step 1: Replace the entire content of `templates/cv.html` with the Profile Hub**

```html
{% extends "base.html" %}
{% block title %}Profile Hub — Job Search Agent{% endblock %}

{% block head %}
<style>
  .hub-card { background:#fff; border:1px solid #e4e4e7; border-radius:16px; padding:1.5rem; }
  .skill-bar { height:6px; border-radius:999px; background:#e4e4e7; overflow:hidden; }
  .skill-bar-fill { height:100%; border-radius:999px; background:#6366f1; transition:width .4s; }
  .skill-bar-fill.in-cv { background:#10b981; }
  .profile-ring { width:80px; height:80px; }
  .kw-pill { display:inline-block; padding:3px 10px; border-radius:999px; font-size:11px; font-weight:600; border:1px solid; margin:3px; }
</style>
{% endblock %}

{% block content %}
<div class="mb-6">
  <h1 class="text-2xl font-semibold text-gray-900">Profile Hub</h1>
  <p class="text-gray-500 mt-1 text-sm">Your CV, skill gaps, and keyword insights in one place.</p>
</div>

<div class="grid grid-cols-1 lg:grid-cols-3 gap-6">

  <!-- Left column: Upload + Completeness -->
  <div class="space-y-6">

    <!-- Profile Completeness -->
    <div class="hub-card" id="completeness-card">
      <h2 class="text-base font-semibold text-gray-900 mb-4">Profile Completeness</h2>
      <div class="flex items-center gap-4 mb-4">
        <div class="relative profile-ring flex-shrink-0">
          <svg viewBox="0 0 80 80" class="w-20 h-20 -rotate-90">
            <circle cx="40" cy="40" r="34" fill="none" stroke="#e4e4e7" stroke-width="8"/>
            <circle cx="40" cy="40" r="34" fill="none" stroke="#6366f1" stroke-width="8"
                    stroke-dasharray="213.6"
                    stroke-dashoffset="213.6"
                    id="score-ring" stroke-linecap="round"/>
          </svg>
          <div class="absolute inset-0 flex items-center justify-center">
            <span class="text-lg font-bold text-gray-900" id="score-pct">…</span>
          </div>
        </div>
        <div id="score-label" class="text-sm text-gray-500">Loading…</div>
      </div>
      <div id="score-breakdown" class="space-y-2 text-sm"></div>
    </div>

    <!-- CV Upload -->
    <div class="hub-card">
      <h2 class="text-base font-semibold text-gray-900 mb-4">Your CV</h2>
      {% if cv_data %}
      <div class="flex items-center justify-between p-3 bg-green-50 border border-green-200 rounded-lg mb-4">
        <div>
          <p class="text-sm font-medium text-green-800">✓ CV uploaded — {{ cv_data.skills | length }} skills</p>
          <p class="text-xs text-green-600 mt-0.5">Updated: {{ cv_data.uploaded_at[:10] }}</p>
        </div>
        <button onclick="document.getElementById('cv-replace-zone').classList.toggle('hidden')"
                class="text-xs text-green-600 hover:text-green-800 font-medium underline ml-4 flex-shrink-0">Replace</button>
      </div>
      <div id="cv-replace-zone" class="hidden mb-4">
        <div id="drop-zone" class="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center cursor-pointer hover:border-indigo-400 hover:bg-indigo-50 transition-colors"
             onclick="document.getElementById('cv-file-input').click()"
             ondragover="event.preventDefault(); this.classList.add('border-indigo-500','bg-indigo-50')"
             ondragleave="this.classList.remove('border-indigo-500','bg-indigo-50')"
             ondrop="handleDrop(event)">
          <p class="text-sm text-gray-600">Drop new CV or <span class="text-indigo-600">browse</span></p>
          <p class="text-xs text-gray-400 mt-1">PDF, DOCX, TXT</p>
        </div>
      </div>
      {% else %}
      <div id="drop-zone" class="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center cursor-pointer hover:border-indigo-400 hover:bg-indigo-50 transition-colors mb-4"
           onclick="document.getElementById('cv-file-input').click()"
           ondragover="event.preventDefault(); this.classList.add('border-indigo-500','bg-indigo-50')"
           ondragleave="this.classList.remove('border-indigo-500','bg-indigo-50')"
           ondrop="handleDrop(event)">
        <p class="text-sm font-medium text-gray-700">Drop CV here or <span class="text-indigo-600">browse</span></p>
        <p class="text-xs text-gray-400 mt-1">PDF, DOCX, TXT</p>
      </div>
      {% endif %}
      <input type="file" id="cv-file-input" accept=".pdf,.docx,.txt" class="hidden" onchange="uploadCV(this.files[0])">
      <div id="upload-status" class="hidden mt-3 p-3 rounded-lg text-sm"></div>
      <button onclick="rescoreJobs()" id="rescore-btn"
              class="mt-2 w-full px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50"
              {% if not cv_data %}disabled{% endif %}>
        Re-score All Jobs Against CV
      </button>
      <div id="rescore-status" class="hidden mt-2 text-sm text-gray-600"></div>

      {% if cv_data and cv_data.skills %}
      <div class="mt-4">
        <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Detected Skills</p>
        <div id="skills-fallback" class="flex flex-wrap gap-1.5">
          {% for skill in cv_data.skills %}
          <span class="skill-pill inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-indigo-50 text-indigo-700 border border-indigo-100" data-skill="{{ skill | lower }}">{{ skill }}</span>
          {% endfor %}
        </div>
      </div>
      {% endif %}
    </div>

  </div>

  <!-- Right columns: Skills gap + Keyword heatmap -->
  <div class="lg:col-span-2 space-y-6">

    <!-- Skills Gap Report -->
    <div class="hub-card">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-base font-semibold text-gray-900">Skills Gap Report</h2>
        <p class="text-xs text-gray-400">Skills by frequency in your target roles</p>
      </div>
      {% if not cv_data %}
      <div class="text-center py-8 text-gray-400 text-sm">Upload your CV to see which skills you're missing.</div>
      {% else %}
      <div id="skills-gap-list" class="space-y-2">
        <div class="text-xs text-gray-400 py-4 text-center">Loading…</div>
      </div>
      {% endif %}
    </div>

    <!-- Keyword Heatmap -->
    <div class="hub-card">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-base font-semibold text-gray-900">Keyword Heatmap</h2>
        <p class="text-xs text-gray-400">Most common words in target-role JDs</p>
      </div>
      <div id="keyword-heatmap" class="leading-relaxed">
        <div class="text-xs text-gray-400 py-4 text-center">Loading…</div>
      </div>
    </div>

  </div>
</div>
{% endblock %}

{% block scripts %}
<script>
// ── Profile completeness ring ──────────────────────────────────────────────
fetch('/api/cv/profile-score')
  .then(r => r.json())
  .then(data => {
    if (!data.ok) return;
    const score = data.score;
    document.getElementById('score-pct').textContent = score + '%';
    document.getElementById('score-label').textContent =
      score >= 80 ? 'Great shape! Your profile is strong.' :
      score >= 50 ? 'Good start — a few things to improve.' :
      'Set up your profile to get better results.';
    // Animate the SVG ring (circumference = 2πr = 213.6)
    const ring = document.getElementById('score-ring');
    ring.style.strokeDashoffset = 213.6 - (score / 100 * 213.6);

    const breakdown = document.getElementById('score-breakdown');
    breakdown.innerHTML = data.breakdown.map(item => `
      <div class="flex items-center gap-2">
        <span class="${item.done ? 'text-green-500' : 'text-gray-300'} flex-shrink-0">${item.done ? '✓' : '○'}</span>
        <span class="${item.done ? 'text-gray-700' : 'text-gray-400'} text-xs">${item.label}</span>
        <span class="ml-auto text-xs font-semibold ${item.done ? 'text-indigo-600' : 'text-gray-300'}">+${item.points}</span>
      </div>
    `).join('');
  });

// ── Skills gap report ──────────────────────────────────────────────────────
{% if cv_data %}
fetch('/api/cv/skills-gap')
  .then(r => r.json())
  .then(data => {
    const container = document.getElementById('skills-gap-list');
    if (!data.ok || !data.skills.length) {
      container.innerHTML = '<div class="text-xs text-gray-400 py-4 text-center">No skill data yet — run the scraper first.</div>';
      return;
    }
    const maxCount = data.skills[0].count;
    container.innerHTML = data.skills.slice(0, 25).map(item => `
      <div class="flex items-center gap-3">
        <div class="w-28 flex-shrink-0 flex items-center gap-1.5">
          <span class="${item.in_cv ? 'text-green-500' : 'text-red-400'} text-xs flex-shrink-0">${item.in_cv ? '✓' : '✗'}</span>
          <span class="text-xs text-gray-700 truncate" title="${item.skill}">${item.skill}</span>
        </div>
        <div class="flex-1 skill-bar">
          <div class="skill-bar-fill ${item.in_cv ? 'in-cv' : ''}" style="width:${Math.round(item.count / maxCount * 100)}%"></div>
        </div>
        <span class="text-xs text-gray-400 w-10 text-right flex-shrink-0">${item.pct}%</span>
      </div>
    `).join('');
  });
{% endif %}

// ── Keyword heatmap ────────────────────────────────────────────────────────
fetch('/api/cv/keyword-heatmap')
  .then(r => r.json())
  .then(data => {
    const container = document.getElementById('keyword-heatmap');
    if (!data.ok || !data.keywords.length) {
      container.innerHTML = '<div class="text-xs text-gray-400 py-4 text-center">No keyword data yet.</div>';
      return;
    }
    const maxCount = data.keywords[0].count;
    const PALETTE = [
      '#6366f1','#8b5cf6','#0ea5e9','#10b981','#f59e0b','#ef4444','#06b6d4','#f97316'
    ];
    container.innerHTML = data.keywords.map((item, i) => {
      const intensity = item.count / maxCount;
      const fontSize = Math.round(11 + intensity * 10); // 11px to 21px
      const opacity = 0.4 + intensity * 0.6;
      const color = PALETTE[i % PALETTE.length];
      return `<span class="kw-pill" style="font-size:${fontSize}px; opacity:${opacity}; color:${color}; border-color:${color}40">${item.word} <span style="font-size:9px;opacity:0.7">${item.count}</span></span>`;
    }).join('');
  });

// ── CV upload + rescore (unchanged logic) ──────────────────────────────────
function handleDrop(event) {
  event.preventDefault();
  document.getElementById('drop-zone').classList.remove('border-indigo-500', 'bg-indigo-50');
  const file = event.dataTransfer.files[0];
  if (file) uploadCV(file);
}

function uploadCV(file) {
  if (!file) return;
  const ext = file.name.split('.').pop().toLowerCase();
  if (!['pdf','docx','txt'].includes(ext)) {
    showStatus('upload-status', `Unsupported: .${ext}. Use PDF, DOCX, or TXT.`, 'error');
    return;
  }
  const formData = new FormData();
  formData.append('cv_file', file);
  showStatus('upload-status', 'Uploading and parsing CV...', 'info');
  fetch('/api/cv/upload', { method: 'POST', body: formData })
    .then(r => r.json())
    .then(data => {
      if (data.ok) {
        showStatus('upload-status', `✓ ${data.skills_count} skills detected. Reloading...`, 'success');
        setTimeout(() => window.location.reload(), 1200);
      } else {
        showStatus('upload-status', `Error: ${data.error}`, 'error');
      }
    })
    .catch(err => showStatus('upload-status', `Network error: ${err.message}`, 'error'));
}

function rescoreJobs() {
  const btn = document.getElementById('rescore-btn');
  btn.disabled = true; btn.textContent = 'Rescoring...';
  const status = document.getElementById('rescore-status');
  status.classList.remove('hidden');
  status.textContent = 'Computing CV match for all jobs...';
  fetch('/api/cv/rescore', { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      btn.disabled = false; btn.textContent = 'Re-score All Jobs Against CV';
      if (data.ok) {
        status.textContent = `✓ ${data.updated} jobs re-scored.`;
        status.className = 'mt-2 text-sm text-green-700';
      } else {
        status.textContent = `Error: ${data.error}`;
        status.className = 'mt-2 text-sm text-red-600';
      }
    });
}

function showStatus(id, msg, type) {
  const el = document.getElementById(id);
  el.classList.remove('hidden');
  el.className = `mt-3 p-3 rounded-lg text-sm ${
    type === 'success' ? 'bg-green-50 text-green-800 border border-green-200' :
    type === 'error' ? 'bg-red-50 text-red-700 border border-red-200' :
    'bg-blue-50 text-blue-700 border border-blue-200'}`;
  el.textContent = msg;
}

// ── Skill grouping (collapsed into tech/domain/product/other) ─────────────
(function groupSkills() {
  const TECH = new Set(['sql','python','excel','figma','api','rest','git','github','ios','android','java','javascript','typescript','react','node','go','rust','swift','kotlin','docker','kubernetes','aws','gcp','azure','mongodb','redis','tableau','power bi','powerbi','looker','dbt','airflow','spark','ml','nlp','ui','ux']);
  const DOMAIN = new Set(['lending','credit','banking','finance','fintech','insurance','payments','nbfc','risk','compliance','audit','tax','accounting','treasury','ipo','saas','b2b','b2c','e-commerce']);
  const AI_PRODUCT = new Set(['ai','analytics','a/b testing','metrics','kpi','okr','roadmap','prd','product','mvp','user research','ux research']);
  const pills = document.querySelectorAll('.skill-pill');
  if (!pills.length) return;
  const groups = { tech:[], domain:[], ai_product:[], other:[] };
  pills.forEach(p => {
    const s = p.dataset.skill;
    if (TECH.has(s)) groups.tech.push(p);
    else if (AI_PRODUCT.has(s)) groups.ai_product.push(p);
    else if (DOMAIN.has(s)) groups.domain.push(p);
    else groups.other.push(p);
  });
  const fallback = document.getElementById('skills-fallback');
  if (!fallback) return;
  fallback.innerHTML = '';
  [
    { key:'tech',       label:'Technical',           pillClass:'bg-blue-50 text-blue-700 border-blue-100' },
    { key:'ai_product', label:'Analytics & Product', pillClass:'bg-violet-50 text-violet-700 border-violet-100' },
    { key:'domain',     label:'Domain & Industry',   pillClass:'bg-purple-50 text-purple-700 border-purple-100' },
    { key:'other',      label:'Other',               pillClass:'bg-gray-50 text-gray-600 border-gray-200' },
  ].forEach(({ key, label, pillClass }) => {
    const items = groups[key];
    if (!items.length) return;
    const sec = document.createElement('div');
    sec.innerHTML = `<p class="text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1 mt-3">${label}</p>`;
    const wrap = document.createElement('div');
    wrap.className = 'flex flex-wrap gap-1.5';
    items.forEach(p => { p.className = `inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${pillClass}`; wrap.appendChild(p); });
    sec.appendChild(wrap);
    fallback.appendChild(sec);
  });
})();
</script>
{% endblock %}
```

**Step 2: Verify page loads**
```bash
python -c "
from app import app
with app.test_client() as c:
    r = c.get('/cv')
    print('Status:', r.status_code)
    assert b'Profile Hub' in r.data
    print('OK')
"
```
Expected: `Status: 200` then `OK`

**Step 3: Commit**
```bash
git add templates/cv.html
git commit -m "feat: rebuild CV page as Profile Hub with skills gap and keyword heatmap"
```

---

## Task 7: Split-pane job browsing

**Files:**
- Modify: `templates/jobs.html` — restructure to split-pane layout with detail panel

**Step 1: Add CSS for split-pane layout (in the `<style>` block, after existing rules)**

```css
  /* Split-pane layout */
  .jobs-split { display:flex; gap:0; height:calc(100vh - 120px); }
  .jobs-list-pane { width:360px; flex-shrink:0; overflow-y:auto; border-right:1px solid #e4e4e7; padding-right:0; }
  .jobs-detail-pane { flex:1; overflow-y:auto; padding:1.5rem; }
  @media (max-width:768px) {
    .jobs-split { flex-direction:column; height:auto; }
    .jobs-list-pane { width:100%; border-right:none; border-bottom:1px solid #e4e4e7; max-height:50vh; }
    .jobs-detail-pane { padding:1rem; }
  }
  /* Compact card for list pane */
  .job-card-compact {
    border-bottom:1px solid #f4f4f5;
    padding:10px 14px;
    cursor:pointer;
    transition:background .12s;
    border-left:3px solid transparent;
  }
  .job-card-compact:hover { background:#f9f9fb; }
  .job-card-compact.selected { background:#eef2ff; border-left-color:#6366f1; }
  /* Detail pane */
  .detail-empty { display:flex; align-items:center; justify-content:center; height:100%; color:#a1a1aa; font-size:14px; }
```

**Step 2: Replace the job grid section (lines 174–246) with the split-pane layout**

Replace the section starting `<!-- Job List -->` through `{% endif %}` with:

```html
<!-- Split-pane layout -->
{% if jobs %}
{% set avatar_colors = ['#6366f1','#0ea5e9','#10b981','#f59e0b','#8b5cf6','#ef4444','#06b6d4','#f97316'] %}
<div class="jobs-split bg-white rounded-xl border border-gray-200 overflow-hidden">

  <!-- Left: Compact job list -->
  <div class="jobs-list-pane" id="job-list">
    {% for job in jobs %}
    {% set s = job.applied_status | int %}
    {% set score = job.relevance_score | int %}
    {% set avatar_bg = avatar_colors[loop.index0 % 8] %}
    <div class="job-card-compact" id="job-{{ job.job_id }}"
         data-role="{{ job.role | lower }}"
         onclick="selectJob('{{ job.job_id }}', this)"
         data-job="{{ job | tojson | forceescape }}">
      <div class="flex items-center gap-2 mb-1">
        <div class="w-6 h-6 rounded flex-shrink-0 flex items-center justify-center text-[10px] font-bold text-white"
             style="background:{{ avatar_bg }}">{{ (job.company or '?')[0] | upper }}</div>
        <p class="text-xs font-medium text-gray-800 truncate flex-1">{{ job.company | truncate(22, true, '…') }}</p>
        <span class="hc-mono text-[11px] font-bold px-1 py-0.5 rounded flex-shrink-0
          {% if score >= 80 %}score-high{% elif score >= 65 %}score-mid{% else %}score-low{% endif %}">{{ score }}</span>
      </div>
      <p class="text-xs font-semibold text-gray-900 leading-tight line-clamp-1 mb-1">{{ job.role }}</p>
      <div class="flex items-center gap-1.5 flex-wrap">
        <span class="text-[10px] text-gray-400">{{ job.location | truncate(14, true, '…') }}</span>
        {% if job.remote_status == 'remote' %}<span class="tag-remote" style="font-size:9px;padding:1px 6px">Remote</span>
        {% elif job.remote_status == 'hybrid' %}<span class="tag-hybrid" style="font-size:9px;padding:1px 6px">Hybrid</span>{% endif %}
        {% if job.date_posted %}<span class="tag-date relative-date ml-auto" data-date="{{ job.date_posted }}">{{ job.date_posted[:10] }}</span>{% endif %}
      </div>
      {% if job._missing_top3 %}
      <div class="flex flex-wrap gap-1 mt-1">
        {% for skill in job._missing_top3 %}<span style="font-size:9px" class="px-1 py-0.5 bg-red-50 text-red-400 border border-red-100 rounded">{{ skill }}</span>{% endfor %}
      </div>
      {% endif %}
    </div>
    {% endfor %}
  </div>

  <!-- Right: Job detail -->
  <div class="jobs-detail-pane" id="job-detail">
    <div class="detail-empty" id="detail-empty">
      <span>← Select a job to view details</span>
    </div>
    <div id="detail-content" class="hidden"></div>
  </div>

</div>
{% else %}
<div class="bg-white rounded-xl border border-gray-200 p-12 text-center">
  <p class="text-gray-500 text-sm">No jobs found.</p>
  <p class="hc-mono text-xs text-gray-400 mt-1">Try broadening your filters or run a live search.</p>
</div>
{% endif %}
```

**Step 3: Add `selectJob()` JS function (in the scripts block, before `searchPollInterval`)**

```javascript
function selectJob(jobId, cardEl) {
  // Deselect previous
  document.querySelectorAll('.job-card-compact.selected').forEach(el => el.classList.remove('selected'));
  cardEl.classList.add('selected');

  const job = JSON.parse(cardEl.dataset.job);
  document.getElementById('detail-empty').classList.add('hidden');
  const content = document.getElementById('detail-content');
  content.classList.remove('hidden');

  const salaryHtml = job.salary_min && job.salary_max
    ? `₹${Math.round(job.salary_min/100000)}L – ₹${Math.round(job.salary_max/100000)}L/yr`
    : (job.salary ? `${job.salary_currency || '₹'}${job.salary}` : '—');

  const stageHtml = job.company_funding_stage
    ? `<span class="tag-stage-series text-xs">${job.company_funding_stage}</span>` : '';
  const sizeHtml = job.company_size
    ? `<span class="tag-stage-large text-xs">${job.company_size}</span>` : '';
  const expHtml = job.experience_min != null
    ? `<span class="tag-yoe">${job.experience_min}${job.experience_max ? '–'+job.experience_max : '+'} yrs</span>` : '';

  const missingHtml = (job._missing_top3 || []).length
    ? `<div class="mt-4"><p class="text-xs font-semibold text-red-500 mb-1">Missing skills:</p><div class="flex flex-wrap gap-1">${(job._missing_top3).map(s => `<span class="px-2 py-0.5 bg-red-50 text-red-500 border border-red-100 rounded text-xs">${escapeHtml(s)}</span>`).join('')}</div></div>` : '';
  const cvScoreHtml = job._cv_score > 0
    ? `<span class="ml-2 text-xs font-bold ${job._cv_score >= 70 ? 'text-green-600' : job._cv_score >= 40 ? 'text-amber-600' : 'text-gray-400'}">CV ${job._cv_score}%</span>` : '';

  content.innerHTML = `
    <div class="flex items-start justify-between mb-4">
      <div>
        <h2 class="text-lg font-bold text-gray-900">${escapeHtml(job.role)}</h2>
        <p class="text-gray-600 mt-0.5">${escapeHtml(job.company || '')}${cvScoreHtml}</p>
      </div>
      ${job.apply_url ? `<a href="${escapeHtml(job.apply_url)}" target="_blank" rel="noopener"
        onclick="onApplyClick('${escapeHtml(jobId)}')"
        class="apply-btn flex-shrink-0 px-4 py-2 text-sm font-semibold ml-4">Apply ↗</a>` : ''}
    </div>

    <div class="flex flex-wrap gap-2 mb-4">
      <span class="tag-pill">${escapeHtml(job.portal || '')}</span>
      <span class="tag-pill">${escapeHtml(job.location || '—')}</span>
      ${job.remote_status === 'remote' ? '<span class="tag-remote">Remote</span>' :
        job.remote_status === 'hybrid' ? '<span class="tag-hybrid">Hybrid</span>' :
        '<span class="tag-pill">Onsite</span>'}
      ${expHtml}
      ${stageHtml}
      ${sizeHtml}
    </div>

    <div class="flex items-center gap-4 mb-4 p-3 bg-gray-50 rounded-lg">
      <div><p class="text-xs text-gray-400">Salary</p><p class="text-sm font-semibold text-emerald-600">${salaryHtml}</p></div>
      <div><p class="text-xs text-gray-400">Match Score</p><p class="text-sm font-bold text-gray-900">${job.relevance_score}</p></div>
      <div><p class="text-xs text-gray-400">Posted</p><p class="text-sm text-gray-700">${job.date_posted ? job.date_posted.slice(0,10) : '—'}</p></div>
    </div>

    ${missingHtml}

    ${job.job_description ? `
    <div class="mt-4">
      <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Job Description</p>
      <div class="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap bg-gray-50 rounded-lg p-4 max-h-72 overflow-y-auto">${escapeHtml(job.job_description)}</div>
    </div>` : ''}

    <div class="mt-4 pt-4 border-t border-gray-100 flex items-center gap-3">
      <select onchange="updateJobStatus('${escapeHtml(jobId)}', this.value, this)"
              class="text-sm rounded-lg border-gray-200 border p-2 focus:border-indigo-400">
        <option value="">Update status…</option>
        <option value="1">Applied</option>
        <option value="2">Saved</option>
        <option value="3">Phone Screen</option>
        <option value="4">Interview</option>
        <option value="5">Offer</option>
        <option value="6">Rejected</option>
      </select>
      <button onclick="toggleGapAnalysis('${escapeHtml(jobId)}', this)"
              class="text-xs text-indigo-600 hover:text-indigo-800 underline">Full gap analysis</button>
    </div>

    <!-- Gap analysis panel (hidden until toggled) -->
    <div id="gap-${escapeHtml(jobId)}" class="hidden mt-4 p-4 bg-indigo-50 rounded-lg text-xs">
      <div id="gap-content-${escapeHtml(jobId)}">Loading…</div>
      <span id="gap-score-${escapeHtml(jobId)}"></span>
    </div>
  `;

  // Update relative dates in detail pane
  document.querySelectorAll('.relative-date').forEach(el => {
    const rel = toRelativeDate(el.dataset.date);
    if (rel) el.textContent = rel;
  });
}
```

**Step 4: Verify**
```bash
python -c "
from app import app
with app.test_client() as c:
    r = c.get('/jobs')
    print('Status:', r.status_code)
    assert b'jobs-split' in r.data or b'job-list' in r.data
    print('OK')
"
```

**Step 5: Commit**
```bash
git add templates/jobs.html
git commit -m "feat: split-pane job browsing with inline JD and full metadata"
```

---

## Task 8: Dashboard insight cards

**Files:**
- Modify: `database.py` — add `get_dashboard_insights()` function
- Modify: `app.py:500-512` (`dashboard()` route) — pass insights
- Modify: `templates/dashboard.html:1-45` — replace stat cards with insight cards

**Step 1: Add `get_dashboard_insights()` to database.py (after `get_recommended_actions`)**

```python
def get_dashboard_insights() -> dict:
    """
    Compute insight metrics for dashboard cards.
    Returns dict with: unopened_high_score, overdue_followups,
    avg_cv_score_this_week, avg_cv_score_last_week, top_missing_skill.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Unopened high-score jobs (score >= 75, status = New)
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM job_listings WHERE relevance_score >= 75 AND applied_status = 0 AND (hidden=0 OR hidden IS NULL)"
    )
    unopened = cursor.fetchone()["cnt"]

    # Overdue follow-ups
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM job_listings WHERE follow_up_date IS NOT NULL AND follow_up_date <= ? AND applied_status NOT IN (5,6)",
        (today,)
    )
    overdue = cursor.fetchone()["cnt"]

    # Avg CV score this week vs last week
    week_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    prev_week_start = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    cursor.execute(
        "SELECT ROUND(AVG(cv_score),1) as avg FROM job_listings WHERE date_found >= ? AND cv_score > 0",
        (week_start,)
    )
    r = cursor.fetchone()
    avg_cv_this_week = r["avg"] if r["avg"] else 0

    cursor.execute(
        "SELECT ROUND(AVG(cv_score),1) as avg FROM job_listings WHERE date_found >= ? AND date_found < ? AND cv_score > 0",
        (prev_week_start, week_start)
    )
    r = cursor.fetchone()
    avg_cv_last_week = r["avg"] if r["avg"] else 0

    # Top missing skill: skill most often required but NOT in top applied jobs
    # Approximation: most frequent skill in all jobs that were NOT applied to
    cursor.execute(
        "SELECT job_description FROM job_listings WHERE applied_status = 0 AND (hidden=0 OR hidden IS NULL) ORDER BY relevance_score DESC LIMIT 100"
    )
    jd_rows = cursor.fetchall()
    conn.close()

    from analyzer import extract_skills
    skill_counts: dict = {}
    for row in jd_rows:
        for skill in extract_skills(row["job_description"] or "", max_skills=None):
            skill_counts[skill] = skill_counts.get(skill, 0) + 1
    top_missing = max(skill_counts, key=skill_counts.get) if skill_counts else None

    return {
        "unopened_high_score": unopened,
        "overdue_followups": overdue,
        "avg_cv_this_week": avg_cv_this_week,
        "avg_cv_last_week": avg_cv_last_week,
        "top_missing_skill": top_missing,
    }
```

**Step 2: Add `get_dashboard_insights` import and call in `dashboard()` route in app.py**

In `app.py` around line 500, modify the `dashboard()` route:

Find:
```python
@app.route("/dashboard")
def dashboard():
    stats = get_comprehensive_stats()
    portal_quality = get_portal_quality_stats()
    pipeline = get_application_pipeline_stats()
    categories = get_best_matching_categories()
    activity = get_application_activity()
    recommendations = get_recommended_actions()
    return render_template(
        "dashboard.html", stats=stats, portal_quality=portal_quality,
        pipeline=pipeline, categories=categories, activity=activity,
        recommendations=recommendations,
    )
```

Replace with:
```python
@app.route("/dashboard")
def dashboard():
    from database import get_dashboard_insights
    stats = get_comprehensive_stats()
    portal_quality = get_portal_quality_stats()
    pipeline = get_application_pipeline_stats()
    categories = get_best_matching_categories()
    activity = get_application_activity()
    recommendations = get_recommended_actions()
    insights = get_dashboard_insights()
    return render_template(
        "dashboard.html", stats=stats, portal_quality=portal_quality,
        pipeline=pipeline, categories=categories, activity=activity,
        recommendations=recommendations, insights=insights,
    )
```

**Step 3: Replace the stat cards section in dashboard.html (lines 10–43)**

Replace the `<!-- Stat Cards -->` grid (lines 10–43) with insight cards:

```html
<!-- Insight Cards -->
<div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">

  <!-- Unopened high-score jobs -->
  <a href="/jobs?min_score=75&applied=none" class="stat-card block hover:shadow-md transition-shadow">
    <p class="text-xs font-medium text-gray-400 uppercase tracking-wide">Unreviewed Gems</p>
    <p class="text-3xl font-bold {% if insights.unopened_high_score > 0 %}text-indigo-600{% else %}text-gray-900{% endif %} mt-1">
      {{ insights.unopened_high_score }}
    </p>
    <p class="text-xs text-gray-400 mt-1">high-score jobs not yet opened</p>
  </a>

  <!-- Follow-ups overdue -->
  <a href="/jobs?applied=applied" class="stat-card block hover:shadow-md transition-shadow">
    <p class="text-xs font-medium text-gray-400 uppercase tracking-wide">Follow-ups Due</p>
    <p class="text-3xl font-bold {% if insights.overdue_followups > 0 %}text-amber-500{% else %}text-gray-900{% endif %} mt-1">
      {{ insights.overdue_followups }}
    </p>
    <p class="text-xs text-gray-400 mt-1">overdue application follow-ups</p>
  </a>

  <!-- CV match trend -->
  <div class="stat-card">
    <p class="text-xs font-medium text-gray-400 uppercase tracking-wide">CV Match (7d avg)</p>
    <p class="text-3xl font-bold text-gray-900 mt-1">
      {% if insights.avg_cv_this_week %}{{ insights.avg_cv_this_week }}%{% else %}—{% endif %}
    </p>
    {% if insights.avg_cv_this_week and insights.avg_cv_last_week %}
      {% set delta = insights.avg_cv_this_week - insights.avg_cv_last_week %}
      {% if delta > 0 %}
      <p class="text-xs mt-1 text-green-600 font-medium">↑ {{ "%.1f"|format(delta) }}% vs last week</p>
      {% elif delta < 0 %}
      <p class="text-xs mt-1 text-red-500 font-medium">↓ {{ "%.1f"|format(delta|abs) }}% vs last week</p>
      {% else %}
      <p class="text-xs mt-1 text-gray-400">same as last week</p>
      {% endif %}
    {% else %}
    <p class="text-xs text-gray-400 mt-1">upload CV to track</p>
    {% endif %}
  </div>

  <!-- Applied / Saved -->
  <div class="stat-card">
    <p class="text-xs font-medium text-gray-400 uppercase tracking-wide">Pipeline</p>
    <p class="text-3xl font-bold text-gray-900 mt-1">
      {{ stats.applied_count }}<span class="text-lg text-gray-400"> / {{ stats.saved_count }}</span>
    </p>
    <p class="text-xs text-gray-400 mt-1">applied / saved · <a href="{{ url_for('jobs') }}" class="text-indigo-500 hover:underline">browse →</a></p>
    {% if insights.top_missing_skill %}
    <p class="text-xs text-amber-600 mt-2">Top gap: <strong>{{ insights.top_missing_skill }}</strong></p>
    {% endif %}
  </div>

</div>
```

**Step 4: Verify**
```bash
python -c "
from app import app
with app.test_client() as c:
    r = c.get('/dashboard')
    print('Status:', r.status_code)
    assert b'Unreviewed Gems' in r.data
    print('OK')
"
```

**Step 5: Commit**
```bash
git add app.py database.py templates/dashboard.html
git commit -m "feat: dashboard insight cards with CV trend, top gap skill, and actionable counts"
```

---

## Task 9: Cutshort scraper

**Files:**
- Modify: `scrapers.py` — add `scrape_cutshort()` function and register in `SCRAPER_MAP`
- Modify: `config.json` — add cutshort portal entry

**Step 1: Add `scrape_cutshort()` to scrapers.py (before the `SCRAPER_MAP` dict at line 1157)**

```python
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
                        "remote_status": "remote" if "remote" in str(item.get("location","")).lower() else "",
                        "date_posted": item.get("createdAt", "")[:10] if item.get("createdAt") else "",
                    }
                    if job["role"] and job["company"]:
                        jobs.append(job)
            except Exception as e:
                logger.warning("Cutshort scrape error (title=%s, loc=%s): %s", title, location, e)
            _time.sleep(1)

    logger.info("Cutshort: scraped %d jobs", len(jobs))
    return jobs
```

**Step 2: Register in SCRAPER_MAP (line 1157)**

Find:
```python
SCRAPER_MAP = {
    "linkedin": scrape_linkedin,
    "indeed": scrape_indeed,
    "naukri": scrape_naukri,
    "hiringcafe": scrape_hiringcafe,
    "angellist": scrape_angellist,
    "iimjobs": scrape_iimjobs,
}
```

Replace with:
```python
SCRAPER_MAP = {
    "linkedin": scrape_linkedin,
    "indeed": scrape_indeed,
    "naukri": scrape_naukri,
    "hiringcafe": scrape_hiringcafe,
    "angellist": scrape_angellist,
    "iimjobs": scrape_iimjobs,
    "cutshort": scrape_cutshort,
}
```

**Step 3: Add cutshort to config.json (after the iimjobs entry)**

In `config.json`, after the `"iimjobs"` block, add:
```json
    "cutshort": {
      "enabled": true,
      "base_url": "https://cutshort.io",
      "timeout": 20
    }
```

**Step 4: Verify import works**
```bash
python -c "from scrapers import scrape_cutshort; print('OK')"
```
Expected: `OK`

**Step 5: Commit**
```bash
git add scrapers.py config.json
git commit -m "feat: add Cutshort scraper (API-based, no Selenium)"
```

---

## Task 10: Instahyre scraper

**Files:**
- Modify: `scrapers.py` — add `scrape_instahyre()` function and register in `SCRAPER_MAP`
- Modify: `config.json` — add instahyre portal entry

**Step 1: Add `scrape_instahyre()` to scrapers.py (after `scrape_cutshort`, before `SCRAPER_MAP`)**

```python
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
                    salary_min = item.get("min_salary") or item.get("salary_min")
                    salary_max = item.get("max_salary") or item.get("salary_max")
                    salary_text = ""
                    if salary_min and salary_max:
                        salary_text = f"₹{salary_min}–{salary_max} LPA"
                    job = {
                        "portal": "instahyre",
                        "company": company_info.get("name") or item.get("company_name") or "",
                        "role": item.get("designation") or item.get("title") or title,
                        "location": item.get("location") or location,
                        "salary": salary_text,
                        "salary_min": int(salary_min * 100_000) if salary_min else None,
                        "salary_max": int(salary_max * 100_000) if salary_max else None,
                        "job_description": (item.get("description") or item.get("jd") or "")[:500],
                        "apply_url": f"https://www.instahyre.com/job-details/{jid}/" if jid else "",
                        "remote_status": "remote" if "remote" in str(item.get("location","")).lower() else "",
                        "experience_min": item.get("min_experience"),
                        "experience_max": item.get("max_experience"),
                        "company_size": company_info.get("employee_count") or "",
                        "date_posted": (item.get("created_at") or "")[:10],
                    }
                    if job["role"] and job["company"]:
                        jobs.append(job)
            except Exception as e:
                logger.warning("Instahyre scrape error (title=%s, loc=%s): %s", title, location, e)
            _time.sleep(1.5)

    logger.info("Instahyre: scraped %d jobs", len(jobs))
    return jobs
```

**Step 2: Add instahyre to SCRAPER_MAP**

```python
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
```

**Step 3: Add instahyre to config.json**

```json
    "instahyre": {
      "enabled": true,
      "base_url": "https://www.instahyre.com",
      "timeout": 25
    }
```

**Step 4: Verify import works**
```bash
python -c "from scrapers import scrape_instahyre; print('OK')"
```
Expected: `OK`

**Step 5: Commit**
```bash
git add scrapers.py config.json
git commit -m "feat: add Instahyre scraper (API-based)"
```

---

## Final Step: Push all changes

```bash
git pull --rebase origin main && git push origin main
```

---

## Summary of Changes

| Task | Files Changed | What It Does |
|------|-------------|--------------|
| 1 | `app.py` | Company stage filter in SQL query builder |
| 2 | `templates/jobs.html` | Date posted, company stage badge, formatted salary on cards |
| 3 | `templates/jobs.html` | Company stage dropdown in "More filters" |
| 4 | `app.py`, `templates/jobs.html` | Inline top-3 missing skills + CV% on each card |
| 5 | `database.py`, `app.py` | 3 new API endpoints: skills-gap, keyword-heatmap, profile-score |
| 6 | `templates/cv.html` | Full Profile Hub rebuild |
| 7 | `templates/jobs.html` | Split-pane layout with JD preview |
| 8 | `database.py`, `app.py`, `templates/dashboard.html` | Actionable insight cards on dashboard |
| 9 | `scrapers.py`, `config.json` | Cutshort scraper |
| 10 | `scrapers.py`, `config.json` | Instahyre scraper |
