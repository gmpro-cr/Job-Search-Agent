# Job Search Agent

> **Your personal job hunting assistant.** It wakes up every morning, searches 6+ job portals, scores every listing against your CV, and delivers only the relevant ones — straight to your inbox or Telegram.

---

## What Problem Does This Solve?

Job hunting in 2025 is exhausting. Here is what most people go through every day:

```
┌─────────────────────────────────────────────────────────┐
│  The typical job seeker's morning                       │
│                                                         │
│  Open LinkedIn  → scroll 45 min → 3 relevant jobs      │
│  Open Naukri    → scroll 30 min → 2 relevant jobs      │
│  Open Indeed    → scroll 25 min → 1 relevant job       │
│  Open HiringCafe→ 20 min        → 0 relevant jobs      │
│                                                         │
│  Total: 2 hours wasted. 6 jobs found. Burnt out.       │
└─────────────────────────────────────────────────────────┘
```

**Job Search Agent does all of that for you — automatically, every day, in under 10 minutes.**

```
┌─────────────────────────────────────────────────────────┐
│  With Job Search Agent                                  │
│                                                         │
│  7:00 AM  Agent scrapes 6 portals automatically        │
│  7:08 AM  Scores every job against your CV             │
│  7:10 AM  Sends you only the top matches via email     │
│                                                         │
│  Total: 0 hours of your time. Best jobs in inbox.      │
└─────────────────────────────────────────────────────────┘
```

---

## Who Is This For?

- **Active job seekers** tired of checking multiple job boards every single day
- **Product managers, engineers, designers, analysts** targeting roles in India
- **Career changers** who want to track their application pipeline in one clean place
- **Anyone** who wants a personal job agent working in the background 24/7

---

## Live Demo

**Production URL:** [job-search-agent-green.vercel.app](https://job-search-agent-green.vercel.app)

Sign in with your Google account — you get your own private workspace with your own jobs, CV, scores, and preferences. No other user can see your data.

---

## The Big Picture

Think of this system as a personal assistant with four departments:

```
┌──────────────────────────────────────────────────────────────────────┐
│                         JOB SEARCH AGENT                            │
│                                                                      │
│   ┌──────────────┐    ┌──────────────────┐    ┌──────────────────┐  │
│   │    YOU       │    │    THE AGENT     │    │  JOB PORTALS     │  │
│   │              │    │                  │    │                  │  │
│   │  Upload CV   │───▶│  Reads your      │───▶│  LinkedIn        │  │
│   │  Set targets │    │  preferences     │    │  Naukri          │  │
│   │  Check jobs  │◀───│  Searches daily  │    │  Indeed          │  │
│   │  Apply       │    │  Scores & ranks  │    │  HiringCafe      │  │
│   │              │    │  Notifies you    │    │  Remotive        │  │
│   └──────────────┘    └────────┬─────────┘    │  Hacker News     │  │
│                                │              └──────────────────┘  │
│                                ▼                                     │
│                       ┌────────────────┐                            │
│                       │   DATABASE     │                            │
│                       │                │                            │
│                       │  All jobs      │                            │
│                       │  Your CV       │                            │
│                       │  Your scores   │                            │
│                       │  Your pipeline │                            │
│                       └────────────────┘                            │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Features

### 1. Automated Job Scraping

The agent visits 6 job portals twice a day and collects every relevant listing — no browser needed, no scrolling, no effort from you.

| Portal | Type | Best For |
|--------|------|----------|
| LinkedIn | Professional network | Senior roles, well-known companies |
| Naukri | India's largest job board | All experience levels |
| Indeed | Global job board | Wide variety |
| HiringCafe | Fast API-based search | Accurate, up-to-date results |
| Remotive | Remote jobs worldwide | Work-from-home roles |
| Hacker News | Tech community board | Startup and tech roles |

### 2. Smart CV Matching

Upload your resume (PDF or Word) and the agent reads it, extracts your skills, and scores every job on how well your background fits — like a recruiter doing a first-pass screen, but working for *you*.

```
YOUR CV                      JOB DESCRIPTION            MATCH SCORE
──────────────────────       ──────────────────────      ───────────
Skills: Python,        ────▶ Requires: Python,     ────▶    87%
        SQL,                 SQL preferred,
        Product Mgmt,        Product background,
        Fintech exp          Fintech a plus
```

### 3. Relevance Scoring (0–100)

Every job gets a relevance score before it reaches you:

```
SCORING BREAKDOWN
────────────────────────────────────────────────────
Job title match            up to 20 points
  ("Product Manager", "Product Lead", "APM"...)
Industry / domain match    up to 30 points
  (fintech, banking, SaaS, credit, payments...)
Location match             up to 10 points
  (Pune = 10 pts, Remote = 10 pts, Hybrid = 7 pts)
Your CV skills in the JD   up to 25 points
  (extracted automatically from your uploaded CV)
Transferable skills        up to 15 points
Red flags detected         minus points
  ("fresher only", "10+ years coding required"...)
────────────────────────────────────────────────────
Score ≥ 65  →  shown in your daily digest
Score < 65  →  stored in database, filtered from digest
```

### 4. Daily Digest Emails

Every morning, a formatted email lands in your inbox with:
- Top matching jobs of the day
- Company name, role, location, and salary range
- Direct link to apply
- Your match percentage per job

### 5. Hiring Manager Finder

The agent searches LinkedIn for Talent Acquisition professionals and recruiters who are actively hiring for roles like yours. Every day, it sends you 5 new contacts — complete with LinkedIn profile links and personalised outreach messages ready to copy and send.

```
TODAY'S HIRING MANAGERS
───────────────────────────────────────────────────────
1. Priya Sharma   · TA Manager · HDFC Bank
   linkedin.com/in/priyasharma · Hiring: Product Manager
   Outreach: "Hi Priya, I'm a fintech PM with 9 years..."

2. Rahul Nair     · Senior Recruiter · Razorpay
   linkedin.com/in/rahulnair · Hiring: APM / PM
   Outreach: "Hi Rahul, saw your post about PM roles..."
───────────────────────────────────────────────────────
```

### 6. Application Pipeline Tracker

Track every job you apply to through its full journey — in one place, with notes:

```
NEW  →  SAVED  →  APPLIED  →  PHONE SCREEN  →  INTERVIEW  →  OFFER
                                                            →  REJECTED
```

### 7. Daily PRD Practice (for Product Managers)

Every morning, the agent generates a full Product Requirements Document for a random product — chosen from a curated list of 365 products ordered by complexity (from a simple water tracker on Day 1 to a real-time regulatory compliance platform on Day 365). Great for staying sharp while searching.

### 8. Custom Alerts and Reminders

Set multiple email alerts with different keyword combinations. For example:
- Alert 1: "Product Manager" jobs in Pune — daily at 8 AM
- Alert 2: "Head of Product" remote jobs — daily at 9 AM
- Alert 3: Hiring manager digest — daily at 11 AM

---

## How It Works — Step by Step

```
                          EVERY DAY AT 7 AM
                                │
                                ▼
           ┌────────────────────────────────────────┐
           │  STEP 1: SCRAPE                        │
           │                                        │
           │  Visit LinkedIn, Naukri, Indeed,       │
           │  HiringCafe, Remotive, Hacker News     │
           │                                        │
           │  Search: your job titles × locations   │
           │  Result: 200–500 raw job listings      │
           └───────────────────┬────────────────────┘
                               │
                               ▼
           ┌────────────────────────────────────────┐
           │  STEP 2: DEDUPLICATE                   │
           │                                        │
           │  Each job gets a unique fingerprint    │
           │  (portal + company + role + location)  │
           │                                        │
           │  Already seen? → Skip                 │
           │  New?          → Continue             │
           └───────────────────┬────────────────────┘
                               │
                               ▼
           ┌────────────────────────────────────────┐
           │  STEP 3: SCORE                         │
           │                                        │
           │  Each job scored 0–100 based on:       │
           │  • Your target job titles              │
           │  • Industry keywords                   │
           │  • Location preferences                │
           │  • Skills extracted from your CV       │
           └───────────────────┬────────────────────┘
                               │
                               ▼
           ┌────────────────────────────────────────┐
           │  STEP 4: STORE                         │
           │                                        │
           │  All new jobs saved to the database    │
           │  (SQLite locally / Postgres on cloud)  │
           └───────────────────┬────────────────────┘
                               │
                               ▼
           ┌────────────────────────────────────────┐
           │  STEP 5: NOTIFY                        │
           │                                        │
           │  Build daily digest (jobs with ≥65)   │
           │  Send email  → your inbox              │
           │  Send Telegram → your phone            │
           │  Update web dashboard                  │
           └────────────────────────────────────────┘

           Total time: 3–8 minutes per run.
```

---

## Architecture (Technical Overview)

```
┌──────────────────────────────────────────────────────────────────────┐
│                        PRODUCTION SETUP                              │
│                                                                      │
│  ┌─────────────────┐         ┌──────────────────────────────────┐   │
│  │  GitHub Actions │ ──────▶ │         Neon Postgres            │   │
│  │  scrape 2×/day  │         │                                  │   │
│  │  (free tier)    │         │  job_listings   ← shared         │   │
│  └─────────────────┘         │  users                           │   │
│          │                   │  user_job_state  ← per user      │   │
│          │ stores digests    │  user_preferences← per user      │   │
│          ▼                   │  user_cv_data    ← per user      │   │
│  ┌─────────────────┐         │  user_reminders  ← per user      │   │
│  │  Vercel Blob    │◀───────▶│  outreach_queue                  │   │
│  │  digests/       │         └──────────────────────────────────┘   │
│  │  prds/          │                          ▲                      │
│  └─────────────────┘                          │                      │
│                                               │                      │
│  ┌────────────────────────────────────────────┴────────────────────┐ │
│  │              Flask App on Vercel (Serverless)                   │ │
│  │                                                                 │ │
│  │  • Google OAuth — sign in with your Google account             │ │
│  │  • Per-user data isolation — you only see your own data        │ │
│  │  • CSRF protection on all forms and API calls                  │ │
│  │  • Background CV rescoring (doesn't block page load)           │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│                       LOCAL DEVELOPMENT                              │
│                                                                      │
│  Same Python code. SQLite replaces Postgres.                        │
│  Local filesystem replaces Vercel Blob.                             │
│  Scheduler runs inside the app process.                             │
└──────────────────────────────────────────────────────────────────────┘
```

### How Your Data Stays Separate from Other Users

Every user shares the same pool of scraped job listings (the "raw" data). But everything you do with those jobs — your scores, applied status, notes, CV, and preferences — is stored in tables that are locked to your account:

```
SHARED (everyone reads this)      YOUR PRIVATE DATA (only you)
─────────────────────────────     ─────────────────────────────────────
job_listings                 ───▶  user_job_state
  company: Razorpay                  cv_score: 84%      (your score)
  role: Product Manager              applied_status: 1  (you applied)
  location: Bangalore                notes: "Great team"(your note)
  description: ...
                                   user_cv_data
                                     skills: [Python, SQL, Fintech]
                                     filename: gaurav_cv_2025.pdf

                                   user_preferences
                                     job_titles: [PM, APM]
                                     locations: [Pune, Remote]

                                   user_reminders
                                     daily digest at 8 AM
```

---

## Pages in the App

| Page | What You See | What It Does |
|------|-------------|--------------|
| **Dashboard** | Today's top jobs, 3 stats, quick apply | Runs lazy CV scoring, shows last 24h jobs |
| **Jobs** | Full list with filters, scores, apply links | Client-side search from the database |
| **My CV** | Upload resume, skill match %, gap analysis | Parses PDF/Word, extracts skills, scores all jobs |
| **Digests** | Past daily email summaries | Reads HTML files from Blob or local disk |
| **Hiring Managers** | LinkedIn recruiters found today | Reads from contacts JSON, deduped by name |
| **Outbox** | Cold emails ready to send | Paginated outreach queue |
| **Reminders** | Your scheduled alerts | Full CRUD on alert configuration |
| **Pipeline** | Kanban of your applications | Drag-and-drop status updates |
| **PRD Library** | Daily PM case study | AI-generated PRDs, one per day |
| **Scraper** | Trigger a manual run | Runs scraper in background thread |

---

## Getting Started — Local Setup

### What You Need First

- **Python 3.11+** → [download here](https://www.python.org/downloads/)
- **Git** → to download the code
- A **Gmail App Password** (optional, only needed for email alerts)

### 5-Minute Setup

**Step 1 — Download the code**
```bash
git clone https://github.com/gmpro-cr/Job-Search-Agent.git
cd Job-Search-Agent
```

**Step 2 — Install packages**
```bash
pip install -r requirements.txt
```

**Step 3 — Create a `.env` file** in the project folder:
```env
# Required — change this to any long random string
FLASK_SECRET=change-this-to-a-long-random-string

# Optional — Google sign-in (skip for local use, use dev login instead)
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret

# Optional — email alerts
GMAIL_ADDRESS=your.email@gmail.com
GMAIL_APP_PASSWORD=your-16-char-app-password

# Optional — Telegram alerts
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
```

> To generate a strong secret key, run:
> ```bash
> python -c "import secrets; print(secrets.token_urlsafe(48))"
> ```

**Step 4 — Start the app**
```bash
python app.py
```

**Step 5 — Open your browser**

Go to: **http://localhost:5001**

Click "Dev login" (available in local mode only), then:
1. Go to **Settings** → enter your target job titles and locations
2. Go to **My CV** → upload your resume
3. Go to **Scraper** → click "Run Now" for your first scrape

The scheduler will automatically run the scraper twice a day going forward.

---

## Deploying to the Cloud (Free)

The app is designed to deploy on **Vercel** (web app) + **Neon** (database) + **GitHub Actions** (scraper). All three have free tiers that cover normal personal use.

### One-Time Cloud Setup

**1. Create free accounts**
- [Vercel](https://vercel.com/signup) — hosts the web app
- [Neon](https://neon.tech) — cloud Postgres database (0.5 GB free)
- [Google Cloud Console](https://console.cloud.google.com) — for Google sign-in

**2. Set up Google OAuth (for sign-in)**
```
Google Cloud Console
→ APIs & Services → Credentials
→ Create OAuth 2.0 Client ID (Web Application)
→ Authorized redirect URIs: https://your-app.vercel.app/auth/callback
```

**3. Deploy to Vercel**
```bash
npm install -g vercel
vercel deploy --prod
```

**4. Add environment variables in the Vercel dashboard**

| Variable | Value |
|----------|-------|
| `FLASK_SECRET` | Run `python -c 'import secrets; print(secrets.token_urlsafe(48))'` |
| `DATABASE_URL` | Your Neon connection string (starts with `postgresql://`) |
| `GOOGLE_CLIENT_ID` | From Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | From Google Cloud Console |
| `GMAIL_ADDRESS` | Your Gmail address (for email alerts) |
| `GMAIL_APP_PASSWORD` | Your Gmail App Password |
| `IMPORT_SECRET` | Any random string (shared with GitHub Actions) |
| `SKIP_INIT_DB` | `true` |

**5. Set up the scraper in GitHub Actions**

Add these secrets to your GitHub repo (`Settings → Secrets → Actions`):

| Secret | Value |
|--------|-------|
| `DATABASE_URL` | Same Neon connection string |
| `IMPORT_SECRET` | Same random string from above |
| `GMAIL_ADDRESS` | Your Gmail (optional) |
| `GMAIL_APP_PASSWORD` | Your App Password (optional) |

The workflow file at `.github/workflows/scrape.yml` runs automatically twice a day at 7:00 AM and 7:00 PM IST, scrapes all portals, and pushes results to your database.

---

## Email Setup — Getting a Gmail App Password

Standard Gmail passwords do not work for sending email via code. You need an App Password:

```
1. Go to myaccount.google.com → Security
2. Turn on 2-Step Verification (required first)
3. Search for "App Passwords"
4. Select app: Mail  |  Select device: Other (type "Job Agent")
5. Click Generate
6. Copy the 16-character password shown
   → This is your GMAIL_APP_PASSWORD
```

---

## Telegram Setup (Optional)

Get instant job alerts on your phone:

```
1. Open Telegram → search @BotFather
2. Send: /newbot
3. Follow prompts → copy the token it gives you
4. Send any message to your new bot
5. Visit: https://api.telegram.org/bot<TOKEN>/getUpdates
6. Find "chat": {"id": 123456789} in the response
   → That number is your TELEGRAM_CHAT_ID
```

---

## Project Structure

```
job-search-agent/
│
├── app.py                     Main web server (Flask routes)
├── database.py                All database operations
├── scrapers.py                Job portal scrapers
├── analyzer.py                Relevance scoring engine
├── reminder_runner.py         Email alert scheduler
├── digest_generator.py        Daily email digest builder
├── prd_generator.py           Daily PRD generator for PMs
├── hiring_managers_search.py  LinkedIn recruiter finder
├── blob_storage.py            Cloud/local file storage adapter
│
├── templates/                 HTML pages
│   ├── base.html              Sidebar + navigation shell
│   ├── dashboard.html         Home page
│   ├── jobs.html              Jobs list
│   ├── cv.html                CV upload and analysis
│   ├── reminders.html         Alert management
│   └── ...
│
├── static/css/main.css        All styles
│
├── data/
│   ├── hr_sent_contacts.json  Hiring managers already contacted
│   └── prds/                  Cached daily PRDs
│
├── scripts/
│   ├── migrate_to_multiuser.py  One-time setup for existing users
│   └── sqlite_to_neon.py        Migrate local data to Neon
│
├── .github/workflows/
│   └── scrape.yml             GitHub Actions scraper schedule
│
├── api/index.py               Vercel serverless entrypoint
├── requirements.txt           Web app Python packages
├── requirements-scraper.txt   Scraper-only Python packages
├── vercel.json                Vercel deployment configuration
└── .env                       Your secrets (never commit this file)
```

---

## Tech Stack

| Component | Technology | Why This Choice |
|-----------|-----------|-----------------|
| Web framework | Flask 3 (Python) | Simple, battle-tested, low overhead |
| Database — local | SQLite | Zero setup, single file, perfect for personal use |
| Database — cloud | Neon Postgres | Free serverless tier, scales automatically |
| File storage | Vercel Blob / local disk | Stores digest HTML and PRDs |
| Authentication | Google OAuth (Authlib) | Secure, no passwords to manage |
| CSRF protection | flask-wtf | Standard Python CSRF library |
| Web scraping | Selenium + BeautifulSoup | Handles both JS-heavy and plain HTML portals |
| Deployment | Vercel (serverless) | Free, auto-deploys on every GitHub push |
| Scraper runner | GitHub Actions | Free, schedule-based, no server needed |
| Email | Gmail SMTP | Free, universally available |
| Notifications | Telegram Bot API | Free, instant delivery to phone |

---

## Deployment Mode — Owner-Only

This codebase is **multi-user-safe but single-owner by default**. The job
scraper only knows how to use *one* set of preferences (the owner's), and
the GitHub Actions cron runs that scrape on a shared schedule. The web app
then scores those jobs per-user using each user's uploaded CV.

What that means in practice for a fresh deployment:

1. **Set `ALLOWED_EMAILS`** in your Vercel project env to a comma-separated
   allowlist of Google accounts that may sign in. Anyone outside the list
   sees a friendly "invite-only" message and gets bounced back to login.
2. **Set `OWNER_EMAIL`** to the Google account whose CV/preferences drive
   the cron scrape. The first user to sign in becomes the admin.
3. Only the admin (`is_admin=1` in the `users` table) can hit the
   scraper / agent / portals / dedup endpoints.

If you want true multi-tenant scrape (each user's job titles fanned out
to the scraper), that's a future change — see Roadmap.

## Security Model

| Threat | Protection |
|--------|-----------|
| Unauthorized signup | `ALLOWED_EMAILS` env-var allowlist enforced in OAuth callback |
| Seeing another user's data | All tables keyed by `user_id`, queries always scoped |
| Cross-site request forgery | `flask-wtf` CSRFProtect on every form and API endpoint |
| Session hijacking | `HttpOnly`, `Secure`, `SameSite=Lax` cookie flags |
| Open redirect attacks | All `next` parameters validated — external URLs rejected |
| Approval link replay | Tokens claimed atomically with a single SQL `UPDATE WHERE status='pending'`, 48-hour expiry |
| Email-prefetcher auto-approval | Approval/skip require a POST from a confirmation page; the bare GET only renders the preview |
| Outreach token cross-user use | Each `outreach_queue` row is owned by `user_id`; save/mark-applied/map endpoints filter to the calling user |
| Brute-force reminder IDs | Full 128-bit `secrets.token_urlsafe(24)` (~192 bits of entropy) |
| Credential leak via storage | Gmail/Telegram/Apollo creds are stripped from `user_preferences` on read AND write — they live only in `.env` / Vercel env |
| Admin endpoint abuse | `/api/scraper/*`, `/api/admin/*`, `/api/agent/run`, `/api/portals/update` require `is_admin=1` |
| Clickjacking / MIME sniffing / dangling perms | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy`, HSTS on Vercel |
| Bypass auth via dev-login | Off unless `ENABLE_DEV_LOGIN=1` is set (and never honoured on Vercel) |

---

## Frequently Asked Questions

**Q: Does this store my CV on a server?**

On the cloud version (Vercel), your CV text is stored in Neon Postgres under your user account. No other user can read it. If you run locally, everything stays on your own machine and never leaves it.

**Q: Can other users see my jobs or applications?**

No. Job scores, applied status, notes, and preferences are all stored in tables keyed to your user ID. The raw job listings are shared (everyone sees the same pool of scraped jobs), but your interactions with those jobs are completely private.

**Q: Does it automatically apply to jobs for me?**

No, and this is intentional. The agent finds, scores, and surfaces jobs. You click Apply yourself. You should review every application before it goes out with your name on it.

**Q: What if a job portal blocks the scraper?**

The scraper uses realistic delays (2–5 seconds between requests) and caches pages for 12 hours to avoid hammering the same server. If a portal blocks a run, that portal returns empty results while all others continue working normally.

**Q: How is this different from LinkedIn Job Alerts?**

LinkedIn alerts only show LinkedIn jobs. This agent aggregates 6 portals. It also scores every job against your specific CV (not just keyword matching), tracks your full application pipeline, finds recruiters for you, and generates PM practice material — none of which LinkedIn does.

**Q: Is this free?**

Completely free. Vercel free tier, Neon free tier, GitHub Actions free tier. No credit card required anywhere.

**Q: Can I run this locally without any cloud setup?**

Yes. `python app.py` is the entire setup. SQLite handles the database, no cloud accounts needed, everything stays on your machine.

**Q: I am not technical. Can I still use this?**

The cloud version at [job-search-agent-green.vercel.app](https://job-search-agent-green.vercel.app) requires nothing technical at all. Sign in with Google, upload your CV, set your job titles, and the agent handles everything else.

---

## Contributing

Contributions are welcome. Areas that need the most help:

- **New portal scrapers** — add a new portal in `scrapers.py`
- **Better scoring** — improve `analyzer.py` for more domains and role types
- **Tests** — the `tests/` folder is thin and needs coverage
- **UI improvements** — templates in `templates/`, styles in `static/css/main.css`

```bash
# Fork the repo on GitHub, then:
git clone https://github.com/YOUR_USERNAME/Job-Search-Agent.git
cd Job-Search-Agent
pip install -r requirements.txt
python app.py
# Make your changes, test locally, open a Pull Request
```

---

## Roadmap

- [ ] Mobile-friendly PWA (installable on phone)
- [ ] AI-written personalised cover notes per job
- [ ] Browser extension — one-click save from any job page
- [ ] Interview prep mode — company research + common questions per role
- [ ] Salary benchmarking against market data

---

## Built By

**Gaurav Mahale** — AI Product Builder, 9+ years in banking and fintech.

This tool was built to solve my own job search problem. It runs in production, processes hundreds of jobs per day, and is fully open source.

- GitHub: [@gmpro-cr](https://github.com/gmpro-cr)
- LinkedIn: [linkedin.com/in/mahalegauravk](https://www.linkedin.com/in/mahalegauravk)
- X / Twitter: [@mahalegauravk](https://x.com/mahalegauravk)

---

*Built with Flask · Deployed on Vercel · Powered by caffeine and the frustration of manual job hunting.*
