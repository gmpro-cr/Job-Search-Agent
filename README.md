# Job Search Agent

A personal job search dashboard that scrapes Indian portals twice a day, scores each listing against your CV, and emails you a morning digest.

**Your own deployment in 15 minutes:** fork → configure → deploy.

---

## What it does

- Scrapes LinkedIn, Indeed, Naukri (and more) twice a day via GitHub Actions
- Scores every listing against your uploaded CV — relevance score out of 100
- Emails a daily digest with top matches
- Web dashboard: view jobs, track applications, draft outreach to hiring managers
- Designed for one person per deployment — no shared accounts, no SaaS complexity

---

## Quick Start — fork and deploy your own

### 1. Fork this repo

Click **Fork** on GitHub. You'll get your own copy at `github.com/<you>/Job-Search-Agent`.

### 2. Set up Neon (free Postgres)

1. Create a free account at https://neon.tech
2. Create a new project
3. Copy the **pooled** connection string (`DATABASE_URL`) and the **direct** connection string (`DATABASE_URL_DIRECT`)

### 3. Set up Vercel Blob (free file storage)

1. Go to https://vercel.com → Storage → Blob → Create Store
2. Copy the `BLOB_READ_WRITE_TOKEN`

### 4. Set up Google OAuth

1. Go to https://console.cloud.google.com → APIs & Services → Credentials
2. Create an **OAuth 2.0 Client ID** (Web application)
3. Under Authorized redirect URIs, add: `https://<your-vercel-url>/auth/callback`
4. Copy `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`

### 5. Deploy to Vercel

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/gmpro-cr/Job-Search-Agent)

Set these environment variables in your Vercel project settings:

| Variable | Value |
|---|---|
| `OWNER_EMAIL` | Your Google email — **only this account can sign in** |
| `DATABASE_URL` | Neon pooled connection string |
| `DATABASE_URL_DIRECT` | Neon direct connection string |
| `BLOB_READ_WRITE_TOKEN` | Vercel Blob token |
| `GOOGLE_CLIENT_ID` | From Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | From Google Cloud Console |
| `FLASK_SECRET` | Random secret: `python -c 'import secrets; print(secrets.token_urlsafe(48))'` |
| `GMAIL_ADDRESS` | Gmail address for sending digests |
| `GMAIL_APP_PASSWORD` | Gmail app password (Settings → Security → App passwords) |
| `GEMINI_API_KEY` | Optional — for AI-generated daily PRDs |

### 6. Set GitHub Actions secrets

In your forked repo → Settings → Secrets → Actions, add:

- `DATABASE_URL`
- `DATABASE_URL_DIRECT`
- `BLOB_READ_WRITE_TOKEN`
- `GMAIL_ADDRESS`
- `GMAIL_APP_PASSWORD`
- `EMAIL_RECIPIENT` (email address to receive daily digest — usually same as `OWNER_EMAIL`)
- `GEMINI_API_KEY` (optional)

The scraper runs automatically at **7 AM and 7 PM IST** via GitHub Actions.

### 7. Sign in and set up

1. Go to your Vercel URL → sign in with Google
2. Upload your CV (PDF or DOCX)
3. Set your job preferences (titles, locations, experience level)
4. Come back tomorrow morning — your digest will be waiting

---

## Local development

```bash
git clone https://github.com/<you>/Job-Search-Agent
cd Job-Search-Agent
pip install -r requirements.txt -r requirements-scraper.txt
python app.py        # http://localhost:5001
```

Without `OWNER_EMAIL` set, a **Dev login** button appears on the login page — no Google OAuth needed.

To run the scraper locally:

```bash
python scrape_and_push.py
```

---

## Architecture

```
GitHub Actions (7 AM + 7 PM IST)
    └── scrape_and_push.py
        └── Neon Postgres (job_listings)

Flask app on Vercel
    └── Dashboard, CV upload, job scoring, outreach

Vercel Blob
    └── Email digests, daily PRDs
```

Jobs are scraped once into a shared pool. Your CV scores run against that pool when you open the dashboard — no compute wasted on re-scraping.

---

## Why single-owner?

Scraping is the bottleneck. GitHub Actions gives 2,000 free minutes/month. Running a tailored scrape for multiple users with different job preferences would exhaust that in days. One person per deployment keeps it free indefinitely.

---

## Tech stack

- **Backend:** Python / Flask
- **Database:** SQLite (local) or Neon Postgres (production) — same code, auto-detected
- **Storage:** Local filesystem (dev) or Vercel Blob (production) — same code, auto-detected
- **Hosting:** Vercel (serverless Flask via WSGI)
- **Scraping:** Selenium + BeautifulSoup, runs on GitHub Actions
- **Auth:** Google OAuth via Authlib
