# Design: GitHub Actions Scraping + Local Web UI

**Date:** 2026-02-23
**Status:** Approved

## Goal

Move job scraping out of the local Mac entirely so it runs 24/7 on GitHub Actions.
Email and Telegram notifications fire immediately when scraping completes.
The local Flask app handles only the web UI and reminders.

## Architecture

```
GitHub Actions (cloud)                    Mac (local)
─────────────────────────────────         ──────────────────────────────
scrape.yml (cron: 1:30 AM & 1:30 PM UTC)
  │
  ▼
scrape_and_push.py
  ├─ scrape_all_portals()
  ├─ analyze_jobs()
  ├─ send_telegram_alert() ──────────────► Telegram (immediate)
  ├─ send_job_email() ───────────────────► Gmail (immediate)
  ├─ write data/latest_scrape.json
  └─ git commit + push ──────────────────► GitHub repo (main branch)
                                                │
                                                │  (git pull on app startup)
                                                ▼
                                          app.py startup
                                            └─ sync_from_scrape()
                                                 └─ insert_jobs_bulk()
                                                      └─ jobs.db
                                                           │
                                                           ▼
                                                     Web UI at :5001
```

## Schedule

- GitHub Actions cron: `30 1 * * *` and `30 13 * * *` UTC
- = 7:00 AM and 7:00 PM IST

## Files Modified

### `scrape_and_push.py`
- **Remove:** HTTP POST to `RENDER_APP_URL` (all batching logic)
- **Add:** `send_job_email()` call for digest notification
- **Add:** `send_telegram_alert()` + `send_telegram_batch_summary()` calls
- **Add:** Write all analyzed jobs to `data/latest_scrape.json`
- **Env vars removed:** `RENDER_APP_URL`, `IMPORT_SECRET`
- **Env vars added:** `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `EMAIL_RECIPIENT`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_MIN_SCORE`

### `.github/workflows/scrape.yml`
- **Update cron:** two schedules — 1:30 AM UTC and 1:30 PM UTC
- **Update env vars:** replace `RENDER_APP_URL`/`IMPORT_SECRET` with email + Telegram secrets
- **Add step:** after script runs, `git add data/latest_scrape.json` + `git commit` + `git push`
- **Add:** `data/` directory to `.gitignore` exclusion (or ensure it is tracked)

### `app.py`
- **Add:** `sync_from_scrape()` function (~20 lines)
  - Reads `data/latest_scrape.json`
  - Compares mtime against `data/last_import.txt` timestamp
  - If newer: calls `insert_jobs_bulk(jobs)`, updates `last_import.txt`
  - Runs in a background thread so startup is non-blocking
- **Call site:** invoked alongside `setup_background_scheduler()` at module load

## Files Not Modified

`scrapers.py`, `analyzer.py`, `database.py`, `email_notifier.py`,
`telegram_notifier.py`, `telegram_bot.py`, `scheduler.py`, `reminder_runner.py`

## GitHub Secrets Required (one-time manual setup)

```
GMAIL_ADDRESS
GMAIL_APP_PASSWORD
EMAIL_RECIPIENT
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
TELEGRAM_MIN_SCORE
OPENROUTER_API_KEY
```

## Local App Behaviour After Change

- Web UI still runs at `http://localhost:5001`
- On each startup, auto-imports any scrape results committed since last run
- Local APScheduler (07:00/19:00) remains in code as a fallback; GH Actions is the primary trigger
- Reminders, CV management, job tracking — all unchanged

## Non-Goals

- No server infrastructure (no Render, no Oracle Cloud, no Docker)
- No changes to `database.py`, scrapers, or analyzer
- No changes to the web UI templates
