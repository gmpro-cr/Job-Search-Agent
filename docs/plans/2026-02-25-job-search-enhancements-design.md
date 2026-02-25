# Job Search Agent — Enhancement Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Surface hidden database fields in the UI, add missing filters, rebuild the CV page into a Profile Hub, improve job browsing with split-pane + inline JD, add dashboard insight cards, and add Cutshort/Instahyre scrapers.

**Architecture:** All backend data already exists. Changes are mostly frontend (Jinja2 templates + vanilla JS) + 3-4 new Flask API endpoints + 2 new scrapers. No new dependencies except the scrapers.

**Tech Stack:** Flask, Jinja2, Tailwind CSS, vanilla JS, SQLite (existing)

---

## Phase 1 — Surface Hidden Data + Filters

### What to build
- Show `date_posted`, `company_funding_stage`, `company_size`, `experience_min/max`, normalized salary range on every job card
- Add filter controls: salary range slider (using salary_min/salary_max), company stage multi-select, experience range, date posted recency
- Show top-3 missing CV skills inline on each card (no modal needed) — use existing `compute_gap_analysis()`

### Files to change
- `templates/jobs.html` — add badge rendering, new filter controls, inline gap skills
- `app.py` `/jobs` route — pass gap data per job when CV is loaded; ensure salary_min/max, company_funding_stage, company_size returned in job query
- `database.py` `get_jobs()` — ensure all new filter params (salary_min, company_stage, date_posted_days) are wired into SQL WHERE clause

### Data already available
- `salary_min`, `salary_max` (INR, integer) — in DB
- `company_funding_stage` (string: "Series A", "Bootstrapped", etc.) — in DB
- `company_size` (string: "Startup", "Mid-size", etc.) — in DB
- `experience_min`, `experience_max` (integer, years) — in DB
- `date_posted` (ISO string) — in DB
- Gap analysis via `compute_gap_analysis(job, cv_data)` returns `missing_skills` list

---

## Phase 2 — Profile Hub (CV Page Rebuild)

### What to build
Replace the bare CV upload page with a Profile Hub containing:

1. **Skills Gap Report** — for each skill appearing in ≥ 10% of target-role jobs in DB, show: skill name, % of jobs mentioning it, whether it's on your CV (green tick / red cross). Sorted by frequency desc.

2. **Keyword Frequency Heatmap** — top 30 keywords across all scraped jobs matching user's preferred titles. Displayed as a visual tag cloud / ranked list. Color intensity = frequency.

3. **Profile Completeness Score** — simple meter 0-100% based on: CV uploaded (+30), skills extracted (+20), preferences set (+20), salary expectation set (+15), Apollo key set (+15).

4. **Tailored Bullet Points** — already exists at `/api/jobs/<id>/tailored-points` but hidden. Add a "Generate bullets" button on each job card that calls this and shows result in a modal.

### New API endpoints needed
- `GET /api/cv/skills-gap` — query DB for skill frequency across jobs matching user's preferred titles; compare against cv_data skills; return ranked list
- `GET /api/cv/keyword-heatmap` — query job_descriptions in DB for word frequency; return top 30 keywords with counts
- `GET /api/cv/profile-score` — compute completeness score based on what's configured

### Files to change
- `templates/cv.html` — full rebuild into Profile Hub layout
- `app.py` — add 3 new API routes above
- `database.py` — add `get_skill_frequency(job_titles)` and `get_keyword_frequency(job_titles)` helpers

---

## Phase 3 — Better Job Browsing

### What to build

**Split-pane layout** on `/jobs`:
- Left pane: job list (compact cards, scrollable)
- Right pane: when a card is clicked, show full job details — full JD text, all metadata, gap analysis, tailored bullet points button, apply button
- Single-page, no navigation. Right pane empty state = "Select a job to view details"

**Dashboard insight cards** (replace raw stat boxes):
- "5 high-scoring jobs you haven't opened yet"
- "3 follow-ups overdue today"
- "Your avg CV match this week: 68% (↑ 12% vs last week)"
- "Most common missing skill in your target roles: SQL"

### Files to change
- `templates/jobs.html` — restructure to split-pane (CSS flex/grid), add right-pane detail panel
- `templates/dashboard.html` — replace stat widgets with insight cards
- `app.py` `/dashboard` route — compute insight data (unopened high-score count, overdue follow-ups, weekly cv_score avg, top missing skill)

---

## Phase 4 — New Scrapers (Cutshort + Instahyre)

### Cutshort
- API-based: `https://cutshort.io/api/public/jobs` with query params
- Returns structured JSON: title, company, location, salary, skills, description, apply_url
- No Selenium needed — pure requests

### Instahyre
- Search page: `https://www.instahyre.com/search-jobs/`
- JSON response from XHR endpoint; no login needed for basic search
- Selenium likely needed for initial page load + XHR intercept

### Files to change
- `scrapers.py` — add `scrape_cutshort()` and `scrape_instahyre()` functions; register in `scrape_all_portals()`
- `config.json` — add `cutshort` and `instahyre` portal entries with `enabled: true`

---

## Key Constraints
- No new Python packages (use existing requests, selenium, beautifulsoup4)
- All LLM calls optional (tailored bullets work without OpenRouter)
- Each phase independently deployable — commit after each phase
- Mobile layout: keep existing responsive grid, split-pane collapses to tabs on mobile
