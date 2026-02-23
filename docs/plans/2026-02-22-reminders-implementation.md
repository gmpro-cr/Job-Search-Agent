# Job Reminders System — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add twice-daily scraping, 30-day job auto-cleanup, and configurable per-alert email reminders that fire after each scrape.

**Architecture:** Reminders are stored in `reminders.json` at the project root. A new `reminder_runner.py` module handles loading reminders, querying the DB, sending emails (reusing existing `send_job_email()`), and updating `last_sent`. The pipeline in `app.py` calls cleanup + reminders after each scrape. The scheduler is changed from one configurable job to two fixed cron jobs at 07:00 and 19:00.

**Tech Stack:** Python, Flask, APScheduler (BackgroundScheduler + CronTrigger), SQLite (via existing `database.py`), Gmail SMTP (via existing `email_notifier.py`), Jinja2 templates, Tailwind CSS via CDN.

---

## Context: Key files to understand before starting

- `app.py` lines 168–192: `setup_background_scheduler()` — replace this function in Task 1
- `app.py` lines 223–347: `_run_scraper_pipeline()` — add cleanup + reminders calls at end (Task 2 and Task 4)
- `database.py` lines 1–26: imports and `get_connection()` pattern — follow this in Task 2
- `email_notifier.py` lines 118–172: `send_job_email(recipient, jobs, preferences)` — reuse as-is in Task 3
- `templates/base.html` lines 44–96: sidebar nav links — add Reminders link in Task 5
- `user_preferences.json`: contains `gmail_address` and `gmail_app_password` — reminders read these same creds

---

## Task 1: Change scheduler to twice-daily (07:00 and 19:00)

**Files:**
- Modify: `app.py` lines 168–192 (`setup_background_scheduler`)

**Step 1:** Open `app.py`, find `setup_background_scheduler()` at line 168. Replace the entire function body with this:

```python
def setup_background_scheduler():
    """Start APScheduler with two fixed daily pipeline runs: 07:00 and 19:00."""
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.add_job(
            _scheduled_pipeline_run,
            trigger=CronTrigger(hour=7, minute=0),
            id="morning_pipeline",
            name="Morning job scraper pipeline at 07:00",
            replace_existing=True,
        )
        _scheduler.add_job(
            _scheduled_pipeline_run,
            trigger=CronTrigger(hour=19, minute=0),
            id="evening_pipeline",
            name="Evening job scraper pipeline at 19:00",
            replace_existing=True,
        )
        _scheduler.start()
        logger.info("Background scheduler started - pipeline runs at 07:00 and 19:00 daily")
    except ImportError:
        logger.warning("APScheduler not installed - daily scheduling disabled")
    except Exception as e:
        logger.error("Failed to start background scheduler: %s", e)
```

**Step 2:** Verify manually — restart `app.py`, check the log line reads:
```
Background scheduler started - pipeline runs at 07:00 and 19:00 daily
```

**Step 3:** Commit:
```bash
cd /Users/gaurav/job-search-agent
git add app.py
git commit -m "feat: change scheduler to twice-daily at 07:00 and 19:00"
```

---

## Task 2: Add `delete_old_jobs()` and `get_jobs_for_reminder()` to database.py

**Files:**
- Modify: `database.py` (append two new functions after line 778, the last line)

**Step 1:** Open `database.py`. At the very end of the file, append:

```python

def delete_old_jobs(days: int = 30) -> int:
    """
    Delete job listings older than `days` days.
    Returns the number of rows deleted.
    """
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM job_listings WHERE date_found < ?",
        (cutoff,),
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    logger.info("delete_old_jobs: removed %d jobs older than %d days", deleted, days)
    return deleted


def get_jobs_for_reminder(keyword: str, min_score: int, max_jobs: int) -> list:
    """
    Return up to max_jobs listings whose role contains `keyword` (case-insensitive)
    and whose relevance_score >= min_score, ordered newest first.
    Only returns non-hidden jobs.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM job_listings
        WHERE LOWER(role) LIKE ?
          AND relevance_score >= ?
          AND hidden = 0
        ORDER BY date_found DESC
        LIMIT ?
        """,
        (f"%{keyword.lower()}%", int(min_score), int(max_jobs)),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]
```

**Step 2:** Verify the functions exist by running a quick import check:
```bash
cd /Users/gaurav/job-search-agent
python3 -c "from database import delete_old_jobs, get_jobs_for_reminder; print('OK')"
```
Expected: `OK`

**Step 3:** Commit:
```bash
git add database.py
git commit -m "feat: add delete_old_jobs and get_jobs_for_reminder to database"
```

---

## Task 3: Create `reminder_runner.py`

**Files:**
- Create: `reminder_runner.py`

This module loads `reminders.json`, queries the DB for each enabled reminder, sends emails, and updates `last_sent`.

**Step 1:** Create the file `/Users/gaurav/job-search-agent/reminder_runner.py`:

```python
"""
reminder_runner.py - Fire custom email reminders after each scrape.

Loads reminders.json, queries the DB for each enabled reminder,
sends an email using the existing send_job_email(), and updates last_sent.
"""

import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REMINDERS_PATH = os.path.join(_BASE_DIR, "reminders.json")


def load_reminders() -> list:
    """Load reminders from reminders.json. Returns [] if file missing or invalid."""
    if not os.path.exists(REMINDERS_PATH):
        return []
    try:
        with open(REMINDERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load reminders.json: %s", e)
        return []


def save_reminders(reminders: list) -> None:
    """Persist reminders list to reminders.json."""
    with open(REMINDERS_PATH, "w", encoding="utf-8") as f:
        json.dump(reminders, f, indent=2)


def run_reminders(preferences: dict) -> None:
    """
    For each enabled reminder in reminders.json:
    1. Query DB for matching jobs
    2. Send email if results found
    3. Update last_sent timestamp

    preferences must contain gmail_address and gmail_app_password.
    """
    from database import get_jobs_for_reminder
    from email_notifier import send_job_email

    reminders = load_reminders()
    if not reminders:
        logger.info("No reminders configured, skipping")
        return

    updated = False
    for reminder in reminders:
        if not reminder.get("enabled", True):
            continue

        keyword = (reminder.get("keyword") or "").strip()
        if not keyword:
            logger.warning("Reminder '%s' has no keyword, skipping", reminder.get("name"))
            continue

        min_score = max(0, min(100, int(reminder.get("min_score", 65))))
        max_jobs = max(1, min(50, int(reminder.get("max_jobs", 20))))
        recipient = (reminder.get("email") or "").strip()
        name = reminder.get("name", "Job Alert")

        if not recipient:
            logger.warning("Reminder '%s' has no email, skipping", name)
            continue

        jobs = get_jobs_for_reminder(keyword, min_score, max_jobs)
        if not jobs:
            logger.info("Reminder '%s': no jobs found (keyword=%s, min_score=%d)", name, keyword, min_score)
            continue

        # Build a slim preferences dict for the email builder
        alert_prefs = dict(preferences)
        alert_prefs["job_titles"] = [keyword]

        success = send_job_email(recipient, jobs, alert_prefs)
        if success:
            reminder["last_sent"] = datetime.now().isoformat()
            updated = True
            logger.info("Reminder '%s': sent %d jobs to %s", name, len(jobs), recipient)
        else:
            logger.error("Reminder '%s': email send failed to %s", name, recipient)

    if updated:
        save_reminders(reminders)
```

**Step 2:** Verify it imports cleanly:
```bash
cd /Users/gaurav/job-search-agent
python3 -c "from reminder_runner import run_reminders, load_reminders, save_reminders; print('OK')"
```
Expected: `OK`

**Step 3:** Commit:
```bash
git add reminder_runner.py
git commit -m "feat: add reminder_runner module"
```

---

## Task 4: Wire cleanup + reminders into `_run_scraper_pipeline()` in `app.py`

**Files:**
- Modify: `app.py`

**Step 1:** At the top of `app.py`, add the new import alongside the existing imports. Find the line:
```python
from email_notifier import send_job_email
```
Add directly below it:
```python
from reminder_runner import run_reminders
from database import delete_old_jobs
```

**Step 2:** In `_run_scraper_pipeline()`, find the block that begins at line ~337:
```python
        with scraper_lock:
            scraper_status["phase"] = "done"
            scraper_status["finished_at"] = datetime.now().isoformat()
            scraper_status["running"] = False
```

Insert two new phases **before** that block:

```python
        # Phase 5.5: Cleanup old jobs (>30 days)
        with scraper_lock:
            scraper_status["phase"] = "cleanup"
        try:
            removed = delete_old_jobs(days=30)
            logger.info("Cleanup: removed %d jobs older than 30 days", removed)
        except Exception as e:
            logger.error("Cleanup error: %s", e)

        # Phase 5.6: Custom reminders
        with scraper_lock:
            scraper_status["phase"] = "reminders"
        try:
            run_reminders(preferences)
        except Exception as e:
            logger.error("Reminders error: %s", e)

        with scraper_lock:
            scraper_status["phase"] = "done"
            scraper_status["finished_at"] = datetime.now().isoformat()
            scraper_status["running"] = False
```

Note: Remove the original `with scraper_lock: phase="done"` block since you're replacing it here.

**Step 3:** Verify the app still starts cleanly:
```bash
cd /Users/gaurav/job-search-agent
python3 -c "import app; print('OK')"
```
Expected: `OK` (may show some log lines, that's fine)

**Step 4:** Commit:
```bash
git add app.py
git commit -m "feat: wire delete_old_jobs and run_reminders into pipeline"
```

---

## Task 5: Add Flask routes for the Reminders UI

**Files:**
- Modify: `app.py` (add 4 routes near the bottom, before `if __name__ == "__main__":`)

**Step 1:** Find the end of `app.py`. Before the final `if __name__ == "__main__":` block, add these 4 routes:

```python
# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------

@app.route("/reminders")
def reminders():
    """List all reminders and show create form."""
    from reminder_runner import load_reminders
    all_reminders = load_reminders()
    return render_template("reminders.html", reminders=all_reminders)


@app.route("/reminders/create", methods=["POST"])
def reminders_create():
    """Create a new reminder and save to reminders.json."""
    import uuid
    from reminder_runner import load_reminders, save_reminders
    name = request.form.get("name", "").strip()
    keyword = request.form.get("keyword", "").strip()
    email_addr = request.form.get("email", "").strip()
    if not name or not keyword or not email_addr:
        flash("Name, keyword, and email are required.", "error")
        return redirect(url_for("reminders"))
    try:
        min_score = max(0, min(100, int(request.form.get("min_score", 65))))
        max_jobs = max(1, min(50, int(request.form.get("max_jobs", 20))))
    except (ValueError, TypeError):
        flash("Score and max jobs must be numbers.", "error")
        return redirect(url_for("reminders"))
    all_reminders = load_reminders()
    all_reminders.append({
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "keyword": keyword,
        "min_score": min_score,
        "max_jobs": max_jobs,
        "email": email_addr,
        "enabled": True,
        "last_sent": None,
    })
    save_reminders(all_reminders)
    flash(f"Reminder '{name}' created.", "success")
    return redirect(url_for("reminders"))


@app.route("/reminders/<reminder_id>/delete", methods=["POST"])
def reminders_delete(reminder_id):
    """Delete a reminder by id."""
    from reminder_runner import load_reminders, save_reminders
    all_reminders = load_reminders()
    before = len(all_reminders)
    all_reminders = [r for r in all_reminders if r.get("id") != reminder_id]
    save_reminders(all_reminders)
    if len(all_reminders) < before:
        flash("Reminder deleted.", "success")
    return redirect(url_for("reminders"))


@app.route("/reminders/<reminder_id>/toggle", methods=["POST"])
def reminders_toggle(reminder_id):
    """Enable or disable a reminder without deleting it."""
    from reminder_runner import load_reminders, save_reminders
    all_reminders = load_reminders()
    for r in all_reminders:
        if r.get("id") == reminder_id:
            r["enabled"] = not r.get("enabled", True)
            break
    save_reminders(all_reminders)
    return redirect(url_for("reminders"))
```

**Step 2:** Verify routes registered correctly:
```bash
cd /Users/gaurav/job-search-agent
python3 -c "
import app
with app.app.test_client() as c:
    r = c.get('/reminders')
    print('Status:', r.status_code)
"
```
Expected: `Status: 200`

**Step 3:** Commit:
```bash
git add app.py
git commit -m "feat: add /reminders CRUD routes"
```

---

## Task 6: Create `templates/reminders.html`

**Files:**
- Create: `templates/reminders.html`

**Step 1:** Create the file. It extends `base.html` and uses the same light card style as the rest of the UI:

```html
{% extends "base.html" %}
{% block title %}Reminders — Job Agent{% endblock %}

{% block content %}
<div class="max-w-4xl mx-auto">
  <div class="mb-6">
    <h1 class="text-xl font-semibold text-gray-900">Job Reminders</h1>
    <p class="text-sm text-gray-500 mt-1">Create alerts that email you matching jobs after each scrape (7 AM &amp; 7 PM daily).</p>
  </div>

  <!-- Create form -->
  <div class="bg-white border border-gray-200 rounded-xl p-5 mb-6">
    <h2 class="text-sm font-semibold text-gray-700 mb-4">New Reminder</h2>
    <form method="POST" action="{{ url_for('reminders_create') }}">
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mb-4">
        <div>
          <label class="block text-xs font-medium text-gray-600 mb-1">Alert name</label>
          <input type="text" name="name" placeholder="e.g. PM jobs Bangalore" required
                 class="w-full text-sm rounded-lg border border-gray-200 focus:border-indigo-400 focus:ring-indigo-400 py-2 px-3">
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-600 mb-1">Job title keyword</label>
          <input type="text" name="keyword" placeholder="e.g. Product Manager" required
                 class="w-full text-sm rounded-lg border border-gray-200 focus:border-indigo-400 focus:ring-indigo-400 py-2 px-3">
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-600 mb-1">Recipient email</label>
          <input type="email" name="email" placeholder="you@gmail.com" required
                 class="w-full text-sm rounded-lg border border-gray-200 focus:border-indigo-400 focus:ring-indigo-400 py-2 px-3">
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-600 mb-1">Min match score (0–100)</label>
          <input type="number" name="min_score" value="65" min="0" max="100"
                 class="w-full text-sm rounded-lg border border-gray-200 focus:border-indigo-400 focus:ring-indigo-400 py-2 px-3">
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-600 mb-1">Max jobs per email (1–50)</label>
          <input type="number" name="max_jobs" value="20" min="1" max="50"
                 class="w-full text-sm rounded-lg border border-gray-200 focus:border-indigo-400 focus:ring-indigo-400 py-2 px-3">
        </div>
      </div>
      <button type="submit"
              class="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold rounded-lg transition-colors">
        + Create Reminder
      </button>
    </form>
  </div>

  <!-- Reminder list -->
  {% if reminders %}
  <div class="space-y-3">
    {% for r in reminders %}
    <div class="bg-white border border-gray-200 rounded-xl p-4 flex flex-col sm:flex-row sm:items-center gap-3">
      <!-- Info -->
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 mb-1">
          <span class="font-semibold text-gray-900 text-sm">{{ r.name }}</span>
          {% if r.enabled %}
          <span class="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700 font-medium">Active</span>
          {% else %}
          <span class="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 font-medium">Paused</span>
          {% endif %}
        </div>
        <div class="text-xs text-gray-500 flex flex-wrap gap-3">
          <span>Keyword: <strong class="text-gray-700">{{ r.keyword }}</strong></span>
          <span>Score ≥ <strong class="text-gray-700">{{ r.min_score }}</strong></span>
          <span>Max <strong class="text-gray-700">{{ r.max_jobs }}</strong> jobs</span>
          <span>→ <strong class="text-gray-700">{{ r.email }}</strong></span>
          <span>Last sent: <strong class="text-gray-700">
            {% if r.last_sent %}{{ r.last_sent[:16].replace('T', ' ') }}{% else %}Never{% endif %}
          </strong></span>
        </div>
      </div>
      <!-- Actions -->
      <div class="flex items-center gap-2 flex-shrink-0">
        <form method="POST" action="{{ url_for('reminders_toggle', reminder_id=r.id) }}">
          <button type="submit"
                  class="text-xs px-3 py-1.5 rounded-lg border font-medium transition-colors
                         {% if r.enabled %}border-amber-200 text-amber-700 hover:bg-amber-50
                         {% else %}border-green-200 text-green-700 hover:bg-green-50{% endif %}">
            {% if r.enabled %}Pause{% else %}Enable{% endif %}
          </button>
        </form>
        <form method="POST" action="{{ url_for('reminders_delete', reminder_id=r.id) }}"
              onsubmit="return confirm('Delete reminder \'{{ r.name }}\'?')">
          <button type="submit"
                  class="text-xs px-3 py-1.5 rounded-lg border border-red-200 text-red-600 hover:bg-red-50 font-medium transition-colors">
            Delete
          </button>
        </form>
      </div>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="bg-white border border-gray-200 rounded-xl p-10 text-center text-gray-400 text-sm">
    No reminders yet. Create one above.
  </div>
  {% endif %}
</div>
{% endblock %}
```

**Step 2:** Test the page loads with a browser or curl:
```bash
cd /Users/gaurav/job-search-agent
python3 -c "
import app
with app.app.test_client() as c:
    r = c.get('/reminders')
    assert r.status_code == 200
    assert b'Job Reminders' in r.data
    print('OK')
"
```
Expected: `OK`

**Step 3:** Commit:
```bash
git add templates/reminders.html
git commit -m "feat: add reminders UI template"
```

---

## Task 7: Add Reminders to the sidebar navigation

**Files:**
- Modify: `templates/base.html`

**Step 1:** Open `templates/base.html`. Find the desktop sidebar nav block. It has links for Dashboard, Jobs, Digests, Run Scraper, My CV. Find the Digests link block (lines ~61–68):

```html
      <a href="{{ url_for('digests') }}"
         class="sidebar-link {% if request.endpoint == 'digests' %}active{% endif %}">
        <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
        </svg>
        Digests
      </a>
```

Insert this block **immediately after** the Digests `</a>` closing tag:

```html
      <a href="{{ url_for('reminders') }}"
         class="sidebar-link {% if request.endpoint == 'reminders' or request.endpoint in ['reminders_create','reminders_delete','reminders_toggle'] %}active{% endif %}">
        <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"/>
        </svg>
        Reminders
      </a>
```

**Step 2:** Find the mobile bottom nav (lines ~101–132). Add a Reminders tab after the Digests tab. Find the Digests mobile link:

```html
    <a href="{{ url_for('digests') }}" class="flex-1 flex flex-col items-center py-2 text-xs {% if request.endpoint == 'digests' %}text-indigo-600{% else %}text-gray-500{% endif %}">
```

Insert this **immediately after** its closing `</a>`:

```html
    <a href="{{ url_for('reminders') }}" class="flex-1 flex flex-col items-center py-2 text-xs {% if request.endpoint == 'reminders' %}text-indigo-600{% else %}text-gray-500{% endif %}">
      <svg class="w-5 h-5 mb-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"/>
      </svg>
      Alerts
    </a>
```

**Step 3:** Verify the sidebar renders — visit `http://localhost:5001/reminders` and confirm "Reminders" appears highlighted in the sidebar.

**Step 4:** Commit:
```bash
git add templates/base.html
git commit -m "feat: add Reminders link to sidebar and mobile nav"
```

---

## Final Verification

Start the app and manually verify:
```bash
cd /Users/gaurav/job-search-agent
python3 app.py
```

Checklist:
- [ ] Log shows `pipeline runs at 07:00 and 19:00 daily`
- [ ] `http://localhost:5001/reminders` loads the page
- [ ] Can create a reminder (name + keyword + email + score + max_jobs)
- [ ] Reminder appears in list with Active badge
- [ ] Pause button toggles to Paused / Enable
- [ ] Delete button (with confirm dialog) removes reminder
- [ ] `reminders.json` created at project root after first save
- [ ] "Reminders" link appears in sidebar and is highlighted when on that page
