# CLAUDE.md — Job Search Agent

Operating instructions for Claude Code when working in this repo.
Read this once at the start of every session; **don't paraphrase rules from memory**.

---

## What this project is

A multi-user Flask web app that scrapes Indian job portals, scores each
listing against a user's CV + preferences, and lets the user track
applications. Same code runs locally on SQLite and on Vercel against
Neon Postgres + Vercel Blob; a GitHub Actions cron does the actual
scraping twice a day.

- **Production URL:** https://job-search-agent-green.vercel.app
- **GitHub:** https://github.com/gmpro-cr/Job-Search-Agent
- **Cloud:** Vercel project `prj_VXTcJIbqm8kEhOai7Saz4c4ic98L` (team `gaurav-mahales-projects-cbe20bce`),
  Neon project `tiny-cherry-51567556` (org `org-fragrant-lab-97773672`, region `ap-southeast-1`),
  Blob store `store_jyXBoBT10wqsyuVu` (region `iad1`, public access).

The deployment plan + history of decisions lives in the user memory
file `job_search_agent_deployment.md`. Read it for context on *why*
something looks the way it does.

---

## Architecture in 30 seconds

```
┌──────────────────┐   shared       ┌────────────────────┐
│ GitHub Actions   │  ────────────► │   Neon Postgres    │
│ scrape twice/day │                │  (job_listings,    │
└──────────────────┘                │   outreach_queue)  │
        │                           │                    │
        │ writes digests/PRDs       │  per-user state:   │
        ▼                           │   users,           │
┌──────────────────┐                │   user_job_state,  │
│  Vercel Blob     │ ◄─────────────►│   user_preferences │
│ digests/, prds/  │                │   user_cv_data,    │
└──────────────────┘                │   user_reminders   │
        ▲                           └────────────────────┘
        │ list/get                            ▲
        │                                     │ psycopg (USE_POSTGRES=True)
        │                                     │
┌─────────────────────────────────────────────┴───┐
│  Flask app (api/index.py → app.py)              │
│  • Google OAuth                                 │
│  • Per-user routes                              │
│  • No background scheduler on Vercel            │
└─────────────────────────────────────────────────┘
```

**The same `database.py` runs against either SQLite or Neon** — the
driver is picked at import time from `DATABASE_URL`. Locally it falls
back to `jobs.db`. **The same `blob_storage.py` runs against either
Vercel Blob or `<DATA_DIR>/blob_local/`** — picked from
`BLOB_READ_WRITE_TOKEN`.

---

## Repo map (only the load-bearing files)

| File | Purpose |
|---|---|
| `app.py` (~3400 LOC) | Flask routes. Top-level imports keep heavy deps out of module load (see _Slim deploy_ below). |
| `api/index.py` | Vercel WSGI entrypoint — imports `app` from `app.py`. |
| `database.py` (~1900 LOC) | DB layer. Dual-driver adapter (`_PgConn`, `_PgCursor`), per-user helpers, stats. |
| `main.py` | CLI scraper / shared `load_preferences` + `save_preferences` (session-aware). |
| `analyzer.py` | CV parsing, gap analysis, `load_cv_data` / `save_cv_data` (session-aware). |
| `reminder_runner.py` | Reminder load/save (session-aware) + cross-user `run_reminders`. |
| `scrapers.py` | Per-portal scrapers. Lazy-imports `selenium`. |
| `scrape_and_push.py` | GitHub Actions entrypoint. |
| `blob_storage.py` | Vercel Blob client + FS fallback. |
| `digest_generator.py` | HTML/TXT digest builders + `list_digests` / `read_digest`. |
| `prd_generator.py` | Daily PRD generation, Blob-cached. |
| `scripts/migrate_to_multiuser.py` | One-time: wraps single-user JSON state into a default user. |
| `scripts/sqlite_to_neon.py` | One-time: bulk SQLite → Neon copy with `ON CONFLICT DO NOTHING`. |
| `templates/base.html` | Sidebar + topbar shell. Burger toggles `body.sidebar-collapsed` on desktop, `.open` on mobile (persisted to localStorage). |
| `static/css/main.css` | All styling. Sidebar is 272px; `.main-content` is a flex child that **must** keep `min-width: 0`. |
| `requirements.txt` | Slim runtime set for Vercel. |
| `requirements-scraper.txt` | Heavy deps (selenium, langgraph, openai, …) for GitHub Actions only. |
| `vercel.json` | `functions` + `rewrites` config. Bundles `templates/static/data/agent/autoresearch`. |
| `.github/workflows/scrape.yml` | Twice-daily cron. Installs both requirements files. |
| `config.json` | Per-portal enable flags + scoring/digest settings. |

---

## Run / deploy commands

```bash
# Local dev (SQLite, no Blob)
python app.py                                # http://localhost:5001

# Local dev against Neon (DATABASE_URL must be in .env)
python app.py

# Manual scrape (writes to whichever DB DATABASE_URL points at)
python scrape_and_push.py

# Initial multi-user wrap (idempotent)
DEFAULT_USER_EMAIL=you@example.com python -m scripts.migrate_to_multiuser

# Bulk copy SQLite → Neon (idempotent, 60-day cutoff)
python -m scripts.sqlite_to_neon                          # default 60d
python -m scripts.sqlite_to_neon --max-age-days 0         # everything

# Deploy
vercel deploy --yes              # preview
vercel deploy --prod --yes       # production (or just push to main — Vercel auto-deploys)

# Inspect last deploy
vercel list --yes | head -5
vercel inspect --logs <url>

# Trigger the scrape from CLI (needs gh CLI authed)
gh workflow run "Daily Job Scrape"
# …or via UI: https://github.com/gmpro-cr/Job-Search-Agent/actions/workflows/scrape.yml
```

---

## Environment variables

Authoritative `.env` is **gitignored**. Production values live in
Vercel project env (all three: Production / Preview / Development) and
in GitHub Actions secrets.

| Var | Where used | Notes |
|---|---|---|
| `DATABASE_URL` | Everywhere | Pooled Neon URL. Must start with `postgres://` or `postgresql://` for `database.py` to switch into Postgres mode. |
| `DATABASE_URL_DIRECT` | Migrations / DDL | Non-pooled URL for long-running statements. |
| `BLOB_READ_WRITE_TOKEN` | Web app + scraper | Vercel Blob. When absent, `blob_storage.py` falls back to `<DATA_DIR>/blob_local/`. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Web app | OAuth client. Add the deployed URL `/auth/callback` as an authorized redirect URI in Google Cloud Console. |
| `FLASK_SECRET` | Web app | Session signing. Generate with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`. |
| `GEMINI_API_KEY` | PRD generator | Optional; falls back to Ollama then a template. |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | Scraper / reminders | Email digests. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` / `TELEGRAM_MIN_SCORE` | Scraper | Optional alerts. |
| `OPENROUTER_API_KEY` | Scraper (agent) | Optional. |

To push a value into Vercel:

```bash
echo "<value>" | vercel env add MY_VAR production
# Preview env writes via the CLI sometimes hang on the interactive prompt;
# if so, POST to https://api.vercel.com/v10/projects/<id>/env directly using
# the token in ~/Library/Application Support/com.vercel.cli/auth.json
```

---

## Database conventions (READ THIS)

`database.py` runs against **both** SQLite and Postgres via a thin
cursor adapter. Stick to the rules below or you will silently break
one driver:

1. **Use `?` for placeholders, always.** `_PgCursor._adapt()` rewrites
   `?` → `%s` for psycopg. Never write `%s` directly.

2. **Double literal `%` if you write a LIKE pattern inline.** The
   adapter doubles `%` to `%%` so `LIKE '%foo%'` survives, but if you
   bypass `_PgCursor` (rare) you must escape yourself.

3. **Don't use `cursor.lastrowid` against Postgres** — psycopg has no
   such attribute. Use `INSERT ... RETURNING id` and `fetchone()`.
   `get_or_create_user` is the reference pattern.

4. **No `INSERT OR IGNORE`** — that's SQLite-only. Use `INSERT ... ON
   CONFLICT (...) DO NOTHING` (both drivers support it).

5. **Idempotent column adds** via `_add_columns_idempotent(conn, cursor, table, ["col TYPE", …])`.
   Postgres uses `ADD COLUMN IF NOT EXISTS`; SQLite uses
   try/except (because it doesn't support `IF NOT EXISTS` on ADD COLUMN
   even at 3.45).

6. **`BIGINT` for any column that can exceed 2³¹.** `salary_min` /
   `salary_max` learned this the hard way — the Naukri scraper
   sometimes produces 9-digit rupee values.

7. **Aggregates that return Postgres `Decimal` must be cast to
   `float`** before passing to templates. See
   `get_dashboard_insights_user`.

8. **Per-user state lives in user-keyed tables.** Never write
   user-specific data to `job_listings.applied_status` /
   `cv_score` / `user_notes` / `hidden` on new code paths — those
   columns are legacy single-user remnants that the migration script
   read once and they only still exist for the unmigrated default
   user. New per-user state goes into `user_job_state`.

---

## Per-user request flow

Every authenticated request:

1. `app.before_request` redirects to `/login` unless `session['user']`
   is set or the route is in `allowed_routes`.
2. `current_user_id()` returns the DB id for the current session;
   `require_user_id()` 401s if missing. **Use `current_user_id()`**
   when calling user-scoped helpers.
3. `load_preferences()` (main.py), `load_cv_data()` (analyzer.py), and
   `load_reminders()` (reminder_runner.py) automatically resolve the
   current Flask session user and pull from DB, falling back to the
   legacy JSON files when no session is present (CLI / scraper).
4. `_build_jobs_query(filters, user_id=current_user_id())` returns
   `(conditions, params, order, join_sql, join_params, select_extra)`
   — the caller composes `SELECT job_listings.* <select_extra> FROM
   job_listings <join_sql> WHERE <AND of conditions> ORDER BY <order>`.
   The join adds `user_job_state` for per-user fields.

---

## Slim deploy / heavy deps

`requirements.txt` is the **minimum** set the Vercel runtime needs.
Anything required only by the scraper or the AI agent lives in
`requirements-scraper.txt`. The way we keep both lists honest:

- **Top-level imports** in any file `app.py` imports (transitively)
  must resolve from `requirements.txt` alone.
- **Heavy deps are imported lazily inside route handlers** (see
  `pdfplumber`, `docx`, `agent.graph`, `telegram`, etc.). The
  `_should_start_background_tasks()` guard returns False on Vercel
  (`VERCEL=1`) so the APScheduler import path never fires.

When adding a new top-level import, ask whether the package will fit
the slim set. If not, lazy-import it inside the handler and add it to
`requirements-scraper.txt`.

---

## Storage (Blob vs local FS)

`blob_storage.py` has one rule: **always write through `put()` and read
through `get()` / `list()`**. Both modes (Vercel Blob and local FS)
honour the same `<prefix>/<filename>` pathnames. Prefixes in use today:

- `digests/digest_<timestamp>.html` (and `.txt`)
- `prds/prd_<YYYY-MM-DD>.json`

`digest_generator` and `prd_generator` already wrap their writes in
best-effort Blob uploads. **Don't `open(filepath, 'w')` for a new
artefact type** without also calling `blob_storage.put`, or the
artefact will be invisible on Vercel.

---

## Layout / CSS gotchas

`.main-content` is a flex child of `.layout` (which is `display:
flex`). For any wide page, **leave `min-width: 0` on `.main-content`**
or the layout will overflow horizontally when the sidebar is open.
Same applies to nested flex containers — `.page-body` has
`overflow-x: hidden` as a safety net but the fix is `min-width: 0` on
the flex item, not clipping.

Sidebar:
- `.sidebar` width: **272 px**.
- `.main-content { margin-left: 272px }` must match.
- The burger button is **visible on all viewports** since the sidebar
  toggle change. It toggles `body.sidebar-collapsed` on desktop
  (persisted to `localStorage` so navigation doesn't flip the state)
  and `.sidebar.open` on mobile. There's a tiny pre-paint script in
  `<body>` of `base.html` that restores the collapsed state before
  first render — don't move it below other scripts.

---

## Commit / deploy etiquette

- **Don't commit** `cv_data.json`, `reminders.json`, `data/prds/*`,
  `jobs.db*`, `.env*`, or `tmp_career_ops`. They're either secrets or
  data the cloud now owns.
- Vercel is git-connected — pushing to `main` triggers a production
  deploy automatically. Don't `vercel deploy --prod` and push the
  same commit; you'll burn two builds.
- The GitHub Actions scrape workflow (`scrape.yml`) used to commit
  `data/latest_scrape.json` back to the repo. That step is **removed**
  — Neon is the source of truth now. Don't reintroduce it.

---

## When something looks broken

- **`/jobs` page is empty** → check `/api/jobs?limit=5` (it must return
  `{jobs:[...]}`). The page is client-rendered.
- **Empty `cv_score` everywhere** → check the user actually has a CV
  saved (`get_user_cv_data(user_id)`) and that `_rescore_all_jobs` was
  called for that user.
- **Dashboard shows zeros for new user** → expected. Aggregates use
  `get_*_user(user_id)`; new users have an empty
  `user_job_state`. `get_application_pipeline_stats_user_with_legacy_fallback`
  exists only for the migrated default user.
- **Postgres `the query has 0 placeholders but 1 parameters` error**
  → you put `%s` in the SQL string. Use `?`.
- **Postgres `only '%s', '%b', '%t' are allowed as placeholders, got '%X'`**
  → you have a literal `%` in the SQL that wasn't doubled. Either
  parameterise the value or write `%%`.
- **Cold start > 30s on Vercel** → expected on first hit. Neon
  serverless wakes from idle (~1s) plus the dashboard runs 6
  sequential queries. Warm responses are sub-second.

---

## What NOT to do without asking

- Drop the legacy per-user columns on `job_listings` — the migration
  is done but the default user still reads them in some fallback paths
  (`get_application_pipeline_stats_user_with_legacy_fallback`).
- Change `vercel.json` to a `vercel.ts` config without confirming the
  rest of the pipeline (no Node tooling in this repo).
- Re-enable any of the seven disabled portals in `config.json`
  without checking they produce > 0 jobs — the 30-minute GitHub
  Actions budget is tight.
- Add `requirements.txt` entries for packages that aren't imported at
  module load time. Lazy-import them instead and put them in
  `requirements-scraper.txt`.
- Push directly to `main` with `--force` — Vercel will deploy
  whatever you push, including broken state.

---

## Harness Rules

See global `~/.claude/CLAUDE.md` for the universal gate (Plan → Build → Verify → Commit → Push).

**Project-specific:**
- No Vercel deploy without confirming Neon Postgres connection string is set
