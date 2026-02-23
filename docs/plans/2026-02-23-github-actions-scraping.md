# GitHub Actions Scraping Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move job scraping to GitHub Actions (7 AM + 7 PM IST) so it runs cloud-side; send email and Telegram notifications immediately from GH Actions; local app auto-imports results on startup and handles web UI + reminders only.

**Architecture:** GitHub Actions runs `scrape_and_push.py` on cron, which scrapes → analyzes → sends notifications → writes `data/latest_scrape.json` → commits to repo. On Mac startup, `app.py` calls `sync_from_scrape()` (via new `git_sync.py`) which imports any newer scrape file into `jobs.db`.

**Tech Stack:** Python 3.11, Flask, APScheduler (stays), GitHub Actions, `smtplib` (email), `requests` (Telegram). No new libraries required.

---

## Pre-flight checklist

Before starting, confirm:
- [ ] `python -m pytest tests/ -q` passes (16 tests)
- [ ] You are on the `main` branch
- [ ] `data/` directory does not exist yet (will be created)

---

### Task 1: Create `git_sync.py` with tests

**Files:**
- Create: `git_sync.py`
- Create: `tests/test_git_sync.py`

**Step 1: Write the failing tests**

Create `tests/test_git_sync.py`:

```python
"""Tests for git_sync.sync_from_scrape."""
import json
import os
import tempfile
import pytest

from git_sync import sync_from_scrape


def _write_scrape(directory, jobs):
    """Write a fake latest_scrape.json and return its path."""
    data_dir = os.path.join(directory, "data")
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "latest_scrape.json")
    with open(path, "w") as f:
        json.dump(jobs, f)
    return path


def test_imports_jobs_from_scrape_file():
    """sync_from_scrape calls insert_fn with the jobs from the file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        jobs = [{"job_id": "abc", "role": "Engineer", "company": "Acme"}]
        _write_scrape(tmpdir, jobs)

        received = []
        def fake_insert(j):
            received.extend(j)
            return len(j), 0

        sync_from_scrape(tmpdir, fake_insert)
        assert len(received) == 1
        assert received[0]["job_id"] == "abc"


def test_skips_when_already_imported():
    """sync_from_scrape does NOT call insert_fn if last_import.txt is newer."""
    with tempfile.TemporaryDirectory() as tmpdir:
        jobs = [{"job_id": "xyz", "role": "Designer"}]
        scrape_path = _write_scrape(tmpdir, jobs)

        # Write a stamp file with mtime AFTER the scrape file
        import time
        time.sleep(0.01)  # ensure stamp is newer
        stamp_path = os.path.join(tmpdir, "data", "last_import.txt")
        scrape_mtime = os.path.getmtime(scrape_path)
        with open(stamp_path, "w") as f:
            f.write(str(scrape_mtime + 1))  # stamp is newer

        called = []
        def fake_insert(j):
            called.extend(j)
            return len(j), 0

        sync_from_scrape(tmpdir, fake_insert)
        assert called == [], "Should not re-import when stamp is newer than scrape"


def test_returns_none_when_no_file():
    """sync_from_scrape returns None gracefully when no scrape file exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        called = []
        result = sync_from_scrape(tmpdir, lambda j: called.append(j) or (0, 0))
        assert result is None
        assert called == []


def test_stamp_written_after_import():
    """sync_from_scrape writes last_import.txt with the scrape file's mtime."""
    with tempfile.TemporaryDirectory() as tmpdir:
        jobs = [{"job_id": "zzz"}]
        scrape_path = _write_scrape(tmpdir, jobs)
        scrape_mtime = os.path.getmtime(scrape_path)

        sync_from_scrape(tmpdir, lambda j: (len(j), 0))

        stamp_path = os.path.join(tmpdir, "data", "last_import.txt")
        assert os.path.exists(stamp_path)
        with open(stamp_path) as f:
            assert float(f.read().strip()) == scrape_mtime
```

**Step 2: Run tests to confirm they fail**

```bash
cd /Users/gaurav/job-search-agent
python -m pytest tests/test_git_sync.py -v
```

Expected: `ModuleNotFoundError: No module named 'git_sync'`

**Step 3: Implement `git_sync.py`**

Create `git_sync.py` in the project root:

```python
"""
git_sync.py — Import scraped jobs from data/latest_scrape.json.

Called on app startup. If data/latest_scrape.json is newer than the
last recorded import (data/last_import.txt), imports all jobs using
the provided insert function.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)


def sync_from_scrape(base_dir, insert_fn):
    """
    Import jobs from data/latest_scrape.json if newer than last import.

    Args:
        base_dir: project root directory (str)
        insert_fn: callable(jobs: list) -> (inserted: int, skipped: int)

    Returns:
        (inserted, skipped) tuple, or None if no import was needed.
    """
    scrape_file = os.path.join(base_dir, "data", "latest_scrape.json")
    stamp_file = os.path.join(base_dir, "data", "last_import.txt")

    if not os.path.exists(scrape_file):
        logger.info("sync_from_scrape: no scrape file found, skipping")
        return None

    scrape_mtime = os.path.getmtime(scrape_file)

    last_import = 0.0
    if os.path.exists(stamp_file):
        try:
            with open(stamp_file) as f:
                last_import = float(f.read().strip())
        except (ValueError, OSError):
            pass

    if scrape_mtime <= last_import:
        logger.info("sync_from_scrape: already imported this scrape, skipping")
        return None

    try:
        with open(scrape_file) as f:
            jobs = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("sync_from_scrape: failed to read scrape file: %s", e)
        return None

    inserted, skipped = insert_fn(jobs)
    logger.info("sync_from_scrape: inserted=%d, skipped=%d", inserted, skipped)

    try:
        with open(stamp_file, "w") as f:
            f.write(str(scrape_mtime))
    except OSError as e:
        logger.warning("sync_from_scrape: could not write stamp file: %s", e)

    return inserted, skipped
```

**Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_git_sync.py -v
```

Expected: 4 tests pass.

**Step 5: Run all tests to confirm no regressions**

```bash
python -m pytest tests/ -q
```

Expected: 20 passed.

**Step 6: Commit**

```bash
git add git_sync.py tests/test_git_sync.py
git commit -m "feat: add git_sync module for auto-importing scraped jobs on startup"
```

---

### Task 2: Call `sync_from_scrape` from `app.py` on startup

**Files:**
- Modify: `app.py` (two locations)

**Step 1: Add import at top of `app.py`**

In `app.py`, find this import block (around line 50):
```python
from telegram_bot import start_telegram_bot
```

Add one line immediately after it:
```python
from git_sync import sync_from_scrape
```

**Step 2: Add startup call alongside `setup_background_scheduler()`**

Find this block near line 467:
```python
if _should_start_background_tasks():
    setup_background_scheduler()

    # Start Telegram bot if token is configured
    _bot_prefs = apply_env_overrides(load_preferences() or DEFAULT_PREFS.copy())
    _bot_token = _bot_prefs.get("telegram_bot_token", "").strip()
    if _bot_token:
        start_telegram_bot(_bot_token)
```

Replace with:
```python
if _should_start_background_tasks():
    setup_background_scheduler()

    # Auto-import any scraped jobs committed by GitHub Actions
    import threading as _threading
    _threading.Thread(
        target=sync_from_scrape,
        args=(BASE_DIR, insert_jobs_bulk),
        daemon=True,
    ).start()

    # Start Telegram bot if token is configured
    _bot_prefs = apply_env_overrides(load_preferences() or DEFAULT_PREFS.copy())
    _bot_token = _bot_prefs.get("telegram_bot_token", "").strip()
    if _bot_token:
        start_telegram_bot(_bot_token)
```

**Step 3: Verify app still starts**

```bash
python -c "import app; print('app imported ok')"
```

Expected output: `app imported ok` (plus some log lines, no errors)

**Step 4: Commit**

```bash
git add app.py
git commit -m "feat: auto-import scraped jobs from git_sync on app startup"
```

---

### Task 3: Rewrite `scrape_and_push.py`

**Files:**
- Modify: `scrape_and_push.py` (full replacement)

**Step 1: Replace file contents**

The current file POSTs to `RENDER_APP_URL`. Replace entirely with:

```python
#!/usr/bin/env python3
"""
scrape_and_push.py - Standalone scraper that runs on GitHub Actions.

Scrapes all job portals, analyzes/scores jobs, sends email + Telegram
notifications, then writes results to data/latest_scrape.json for the
local app to import on startup.

Required env vars:
    GMAIL_ADDRESS         - sender Gmail address
    GMAIL_APP_PASSWORD    - Gmail App Password (not your Google password)
    EMAIL_RECIPIENT       - recipient email address

Optional env vars:
    TELEGRAM_BOT_TOKEN    - Telegram bot token
    TELEGRAM_CHAT_ID      - Telegram chat ID
    TELEGRAM_MIN_SCORE    - minimum score for Telegram alerts (default: 65)
    OPENROUTER_API_KEY    - for AI-based scoring
"""

import json
import logging
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

from main import load_config, load_preferences, DEFAULT_PREFS, apply_env_overrides
from scrapers import scrape_all_portals
from analyzer import analyze_jobs
from database import generate_job_id
from email_notifier import send_job_email
from telegram_notifier import send_telegram_alert, send_telegram_batch_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(BASE_DIR, "data")
SCRAPE_OUTPUT = os.path.join(DATA_DIR, "latest_scrape.json")


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    config = load_config()
    preferences = apply_env_overrides(load_preferences() or DEFAULT_PREFS.copy())
    job_titles = preferences.get("job_titles", DEFAULT_PREFS["job_titles"])
    locations = preferences.get("locations", DEFAULT_PREFS["locations"])
    top_n = preferences.get("top_jobs_per_digest", 5)

    # --- Phase 1: Scrape ---
    logger.info("Scraping %d titles across %d locations...", len(job_titles), len(locations))
    all_jobs, portal_results = scrape_all_portals(job_titles, locations, config)

    for portal, result in portal_results.items():
        logger.info("  %s: %s (%d jobs)", portal, result.get("status"), result.get("count", 0))

    if not all_jobs:
        logger.warning("No jobs scraped. Exiting.")
        return

    logger.info("Total raw jobs scraped: %d", len(all_jobs))

    # --- Phase 2: Analyze ---
    logger.info("Analyzing and scoring jobs...")
    qualified_jobs, all_analyzed = analyze_jobs(all_jobs, preferences, config)
    logger.info("Analyzed %d jobs, %d qualified", len(all_analyzed), len(qualified_jobs))

    # --- Phase 3: Generate IDs ---
    for job in all_analyzed:
        job["job_id"] = generate_job_id(
            job.get("portal", "unknown"),
            job.get("company", ""),
            job.get("role", ""),
            job.get("location", ""),
        )

    # --- Phase 4: Write JSON for local app to import ---
    serializable_fields = [
        "job_id", "portal", "company", "role", "salary", "salary_currency",
        "location", "job_description", "apply_url", "relevance_score",
        "remote_status", "company_type", "date_posted",
        "experience_min", "experience_max", "salary_min", "salary_max",
        "company_size", "company_funding_stage", "company_glassdoor_rating",
    ]
    payload_jobs = []
    for job in all_analyzed:
        clean = {k: job[k] for k in serializable_fields if k in job and job[k] is not None}
        payload_jobs.append(clean)

    with open(SCRAPE_OUTPUT, "w") as f:
        json.dump(payload_jobs, f)
    logger.info("Wrote %d jobs to %s", len(payload_jobs), SCRAPE_OUTPUT)

    # --- Phase 5: Telegram alerts ---
    tg_token = preferences.get("telegram_bot_token", "").strip()
    tg_chat = preferences.get("telegram_chat_id", "").strip()
    tg_min = int(preferences.get("telegram_min_score", 65))
    if tg_token and tg_chat:
        alert_count = 0
        for job in qualified_jobs:
            if job.get("relevance_score", 0) >= tg_min:
                send_telegram_alert(job, tg_token, tg_chat)
                alert_count += 1
        send_telegram_batch_summary(
            len(all_jobs), len(qualified_jobs), len(payload_jobs), tg_token, tg_chat
        )
        logger.info("Sent %d Telegram alerts", alert_count)
    else:
        logger.info("Telegram not configured — skipping alerts")

    # --- Phase 6: Email digest ---
    recipient = preferences.get("email", "").strip()
    gmail_addr = preferences.get("gmail_address", "").strip()
    gmail_pass = preferences.get("gmail_app_password", "").strip()
    if recipient and gmail_addr and gmail_pass:
        digest_jobs = qualified_jobs[:top_n]
        try:
            send_job_email(recipient, digest_jobs, preferences)
            logger.info("Email digest sent to %s (%d jobs)", recipient, len(digest_jobs))
        except Exception as e:
            logger.error("Failed to send email: %s", e)
    else:
        logger.warning("Email credentials not set — skipping email notification")

    logger.info("Done.")


if __name__ == "__main__":
    main()
```

**Step 2: Verify it imports cleanly (no syntax errors)**

```bash
python -c "import scrape_and_push; print('ok')"
```

Expected: `ok`

**Step 3: Run all tests to confirm no regressions**

```bash
python -m pytest tests/ -q
```

Expected: 20 passed.

**Step 4: Commit**

```bash
git add scrape_and_push.py
git commit -m "feat: scrape_and_push sends notifications directly, writes data/latest_scrape.json"
```

---

### Task 4: Update `.github/workflows/scrape.yml`

**Files:**
- Modify: `.github/workflows/scrape.yml` (full replacement)

**Step 1: Replace file contents**

```yaml
name: Daily Job Scrape

on:
  schedule:
    - cron: '30 1 * * *'   # 7:00 AM IST
    - cron: '30 13 * * *'  # 7:00 PM IST
  workflow_dispatch:

jobs:
  scrape:
    runs-on: ubuntu-latest
    timeout-minutes: 30

    permissions:
      contents: write

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: pip

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run scraper
        env:
          GMAIL_ADDRESS: ${{ secrets.GMAIL_ADDRESS }}
          GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
          EMAIL_RECIPIENT: ${{ secrets.EMAIL_RECIPIENT }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          TELEGRAM_MIN_SCORE: ${{ secrets.TELEGRAM_MIN_SCORE }}
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
        run: python scrape_and_push.py

      - name: Commit scraped jobs
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add data/latest_scrape.json
          git diff --staged --quiet || git commit -m "chore: update scraped jobs [skip ci]"
          git push
```

**Step 2: Verify YAML syntax**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/scrape.yml')); print('yaml ok')"
```

Expected: `yaml ok`

**Step 3: Commit**

```bash
git add .github/workflows/scrape.yml
git commit -m "feat: update GH Actions to run at 7AM/7PM IST, send notifications, commit scrape JSON"
```

---

### Task 5: Update `.gitignore` and create `data/` directory

**Files:**
- Modify: `.gitignore`
- Create: `data/.gitkeep`

**Step 1: Add `data/last_import.txt` to `.gitignore`**

In `.gitignore`, add at the bottom:
```
data/last_import.txt
```

(Do NOT add `data/` itself — `data/latest_scrape.json` must be tracked.)

**Step 2: Create `data/.gitkeep` so the directory is tracked**

```bash
mkdir -p /Users/gaurav/job-search-agent/data
touch /Users/gaurav/job-search-agent/data/.gitkeep
```

**Step 3: Confirm `data/latest_scrape.json` would be tracked (not in gitignore)**

```bash
git check-ignore -v data/latest_scrape.json
```

Expected: no output (file is NOT ignored — that's correct)

**Step 4: Commit**

```bash
git add .gitignore data/.gitkeep
git commit -m "chore: track data/ dir, ignore last_import.txt timestamp"
```

---

### Task 6: (Manual) Add GitHub Secrets

This task is done in the browser — no code changes.

**Steps:**

1. Go to: `https://github.com/gmpro-cr/Discord-Job-Scraper/settings/secrets/actions`
2. Click **"New repository secret"** for each of the following. Copy values from your local `/Users/gaurav/job-search-agent/.env`:

| Secret name | Where to get value |
|---|---|
| `GMAIL_ADDRESS` | Your Gmail address |
| `GMAIL_APP_PASSWORD` | Gmail App Password from `.env` |
| `EMAIL_RECIPIENT` | Your recipient email |
| `TELEGRAM_BOT_TOKEN` | From `.env` |
| `TELEGRAM_CHAT_ID` | From `.env` |
| `TELEGRAM_MIN_SCORE` | From `.env` (e.g. `65`) |
| `OPENROUTER_API_KEY` | From `.env` |

3. Also remove the now-unused secrets if they exist: `RENDER_APP_URL`, `IMPORT_SECRET`

**Verify:** After adding, go to **Actions → Daily Job Scrape → Run workflow** (manual trigger) and confirm it completes without errors.

---

### Task 7: Push all changes and verify end-to-end

**Step 1: Confirm all tests still pass**

```bash
python -m pytest tests/ -q
```

Expected: 20 passed.

**Step 2: Push to GitHub**

```bash
git push origin main
```

**Step 3: Trigger a manual workflow run**

Go to `https://github.com/gmpro-cr/Discord-Job-Scraper/actions` → **Daily Job Scrape** → **Run workflow**.

After it completes (~5-10 minutes):
- Check that a new commit appears with message `"chore: update scraped jobs [skip ci]"`
- Check that `data/latest_scrape.json` exists in the repo
- Check your email and Telegram for the digest

**Step 4: Test local import**

```bash
cd /Users/gaurav/job-search-agent
git pull
python -c "
from git_sync import sync_from_scrape
from database import insert_jobs_bulk
result = sync_from_scrape('.', insert_jobs_bulk)
print('Import result:', result)
"
```

Expected: `Import result: (N, M)` where N > 0

**Step 5: Start local app and verify dashboard**

```bash
python app.py
```

Open `http://localhost:5001` — dashboard should show the newly imported jobs.

---

## Summary of changes

| File | Change |
|---|---|
| `git_sync.py` | **New** — sync helper with 4 tests |
| `tests/test_git_sync.py` | **New** — 4 tests |
| `app.py` | +3 lines: import + background thread call |
| `scrape_and_push.py` | Rewritten: removes HTTP POST, adds email/Telegram/JSON write |
| `.github/workflows/scrape.yml` | New cron × 2, new secrets, git commit step |
| `.gitignore` | +1 line: `data/last_import.txt` |
| `data/.gitkeep` | **New** — track empty directory |

## GitHub Secrets needed (manual)

`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `EMAIL_RECIPIENT`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_MIN_SCORE`, `OPENROUTER_API_KEY`
