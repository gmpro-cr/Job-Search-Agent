# Job Reminders System — Design Document

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:writing-plans to create the implementation plan.

**Goal:** Add configurable per-alert email reminders that fire after each of two daily scrapes, with auto-cleanup of jobs older than 30 days.

**Date:** 2026-02-22

---

## Scope

Three backend changes + one new feature:

1. **Twice-daily scraping** — Replace single configurable digest time with two fixed cron jobs: 7:00 AM and 7:00 PM.
2. **Auto-cleanup** — After each scrape, delete all `job_listings` rows where `date_found < 30 days ago`.
3. **Reminders system** — Multiple custom email alerts, each independently configurable, stored in `reminders.json`.
4. **`/reminders` UI page** — Create, toggle, and delete reminders from the browser.

---

## Data Model

### `reminders.json`

Stored at the project root alongside `user_preferences.json`. Contains a JSON array of reminder objects.

```json
[
  {
    "id": "abc12345",
    "name": "PM jobs Bangalore",
    "keyword": "Product Manager",
    "min_score": 65,
    "max_jobs": 20,
    "email": "user@gmail.com",
    "enabled": true,
    "last_sent": null
  }
]
```

**Field definitions:**
| Field | Type | Description |
|-------|------|-------------|
| `id` | string | 8-char hex UUID, generated at creation |
| `name` | string | Human label for the alert |
| `keyword` | string | Case-insensitive substring matched against `role` column |
| `min_score` | int | Minimum `relevance_score` (0–100) |
| `max_jobs` | int | Max jobs to include in email (1–50) |
| `email` | string | Recipient email for this specific alert |
| `enabled` | bool | Whether the alert fires |
| `last_sent` | string\|null | ISO timestamp of last successful send |

---

## Architecture

### Scheduler (`scheduler.py`)

Replace the single user-configurable cron job with two fixed daily cron jobs:
- `07:00` — morning scrape + reminders
- `19:00` — evening scrape + reminders

The `digest_time` field from `user_preferences.json` is no longer used by the scheduler.

### Pipeline (`app.py` — `run_pipeline()`)

After each scrape completes, the pipeline runs in order:

```
1. Scrape → score → insert new jobs   (existing)
2. Delete jobs older than 30 days     (NEW)
3. Generate + send global digest      (existing)
4. Loop through reminders:            (NEW)
     for each enabled reminder:
       - Query DB with keyword + min_score + LIMIT max_jobs
       - If jobs found → send email
       - Update last_sent in reminders.json
```

### Database change (`database.py`)

New function:
```python
def delete_old_jobs(days=30) -> int:
    """Delete jobs older than `days` days. Returns count deleted."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    # DELETE FROM job_listings WHERE date_found < cutoff
```

New function:
```python
def get_jobs_for_reminder(keyword: str, min_score: int, max_jobs: int) -> list[dict]:
    """
    SELECT * FROM job_listings
    WHERE LOWER(role) LIKE '%keyword%'
      AND relevance_score >= min_score
      AND hidden = 0
    ORDER BY date_found DESC
    LIMIT max_jobs
    """
```

### New module (`reminder_runner.py`)

Handles loading reminders, running queries, sending emails, and updating `last_sent`.

```python
def run_reminders(preferences: dict) -> None:
    """Load reminders.json, fire email for each enabled reminder with results."""
```

### Flask routes (`app.py`)

| Method | Route | Action |
|--------|-------|--------|
| GET | `/reminders` | Render reminders list + create form |
| POST | `/reminders/create` | Validate + append to reminders.json, redirect |
| POST | `/reminders/<id>/delete` | Remove from reminders.json, redirect |
| POST | `/reminders/<id>/toggle` | Flip `enabled` flag, redirect |

### Sidebar (`base.html`)

Add "Reminders" link between "Digests" and "Run Scraper" in both desktop sidebar and mobile bottom nav.

---

## UI — `/reminders` page

### Create form (top of page)
Fields: Alert name · Job keyword · Min score (default 65) · Max jobs (default 20, max 50) · Recipient email

### Reminder cards (below form)
Each card shows: name, keyword, score threshold, max jobs, email, last sent (or "Never"), enabled toggle button, delete button.

---

## Error Handling

- If `reminders.json` does not exist → treat as empty list, create on first save
- If email send fails for a reminder → log error, do NOT update `last_sent`, continue to next reminder
- If `keyword` is blank → skip the reminder (no keyword = too broad, could spam)
- Input validation: `min_score` clamped 0–100, `max_jobs` clamped 1–50

---

## Files Changed

| File | Change |
|------|--------|
| `scheduler.py` | Replace 1 configurable job with 2 fixed cron jobs (07:00, 19:00) |
| `database.py` | Add `delete_old_jobs()` and `get_jobs_for_reminder()` |
| `app.py` | Add 4 routes, call `delete_old_jobs()` and `run_reminders()` in pipeline |
| `reminder_runner.py` | **New file** — reminder loop logic |
| `templates/reminders.html` | **New file** — reminders UI page |
| `templates/base.html` | Add "Reminders" to sidebar + mobile nav |

**Not changed:** `email_notifier.py` (reuse `send_job_email()` as-is), `user_preferences.json` schema.
