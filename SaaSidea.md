# SaaS / Distribution Ideas for Job Search Agent

## Distribution Options Considered

### 1. Downloadable Desktop App (PyInstaller)
- Bundle Python + Flask + deps into a `.dmg` (Mac) or `.exe` (Windows) installer
- Tray icon launcher (`pystray` + `Pillow`) starts Flask server, opens browser to `localhost:5002`
- Best for non-technical/general public users
- Effort: ~3–5 days for Mac + Windows builds
- Challenges: templates/static files bundling, APScheduler in frozen binary, user data must live in `~/Library/Application Support/` not inside bundle

### 2. SaaS (Centralised Hosting)
- Users sign up and use it on your server — no download needed
- Requires: multi-tenancy (user_id scoping on all DB tables), PostgreSQL (replace SQLite), Celery + Redis for background jobs, Stripe for payments
- Hardest part: DB migration to add per-user data isolation
- Ongoing cost to you for hosting + infra

### 3. One-Click Deploy (Free to you — RECOMMENDED)
Each user deploys their own isolated instance to their own free cloud account. You host nothing, pay nothing.

**How it works:**
- User clicks a "Deploy to Fly.io" button in the README
- Their own free Fly.io account hosts their own instance
- Their data stays in their account — no privacy/data liability for you
- You maintain only the GitHub repo (free)
- No multi-tenancy needed — zero DB migration

**Best platform: Fly.io**
- 3 shared VMs free (process stays alive — APScheduler works)
- No sleep-on-inactivity (unlike Render free tier)
- Pairs with Neon (free Postgres, 0.5GB) or Supabase (free Postgres, 500MB)
- Upstash Redis free tier (10k commands/day) if job queue needed
- Resend free tier (100 emails/day) for reminders

| Platform | Free Tier | Catch |
|---|---|---|
| Fly.io | 3 shared VMs | Best fit — persistent processes |
| Railway | $5 credit/month | Runs out in ~2–3 weeks |
| Render | Free web service | Sleeps after 15 min inactivity |
| Koyeb | 2 free instances | 512MB RAM limit |

## What Needs to Be Built for One-Click Deploy
1. `fly.toml` deploy config
2. Swap SQLite → Postgres (single config change, SQLAlchemy or direct psycopg2)
3. "Deploy to Fly.io" button + 5-minute setup guide in README
4. Environment variable setup guide (API keys, Google OAuth redirect URIs for the deployed URL)

## Monetisation Angle (if ever needed)
- Free tier: 50 jobs tracked, 1 alert, basic scoring
- Pro (₹499/month): unlimited jobs, CV scoring, daily PRDs, multiple alerts
- Payments via Stripe
