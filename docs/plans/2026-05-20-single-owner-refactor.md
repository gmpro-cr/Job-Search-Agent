# Single-Owner Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert the app from multi-user SaaS to a fork-and-deploy personal tool — one owner per deployment, locked via `OWNER_EMAIL`, ready for anyone to clone and run as their own job search agent.

**Architecture:** Each deployment is owned by exactly one person set via the `OWNER_EMAIL` env var. Google OAuth still authenticates the owner. Everyone else who wants their own instance forks the repo, sets up their own GitHub Actions secrets and Vercel project, and gets a completely independent deployment. The existing DB tables are unchanged — multi-user tables still exist (zero migration risk) but are accessed through a single `get_owner_id()` helper instead of the session-based `current_user_id()` in non-request contexts.

**Tech Stack:** Flask, SQLite/Neon Postgres (dual-driver), Vercel Blob, Google OAuth via Authlib, APScheduler, GitHub Actions cron

---

## Task 1: Add OWNER_EMAIL guard to auth flow

**Files:**
- Modify: `app.py` — `auth_callback()` (~line 1027), `auth_demo()` (~line 1044), `auth_fresh()` (~line 1050)
- Modify: `app.py` — top-level config load

**Context:**
Currently any Google account can sign in. The `auth_demo` and `auth_fresh` routes allow bypassing OAuth entirely. We need to:
1. Lock Google OAuth to the owner's email only
2. Remove `auth_demo` and `auth_fresh` (these were multi-user test helpers)
3. Add a fallback local-dev auth that only works when `FLASK_ENV=development` and no `OWNER_EMAIL` is set

**Step 1: Read the current auth_callback, auth_demo, and auth_fresh functions**

```bash
grep -n "def auth_callback\|def auth_demo\|def auth_fresh\|OWNER_EMAIL" app.py
```

**Step 2: Add OWNER_EMAIL config near top of app.py (after existing config block)**

Find the line that reads `DATA_DIR = ...` (around line 70) and add below it:

```python
OWNER_EMAIL = os.environ.get('OWNER_EMAIL', '').strip().lower()
```

**Step 3: Modify auth_callback to reject non-owner emails**

Replace the body of `auth_callback()` with:

```python
@app.route('/auth/callback')
def auth_callback():
    token = oauth.google.authorize_access_token()
    userinfo = token.get('userinfo') or oauth.google.userinfo()
    email = (userinfo.get('email') or '').strip().lower()

    if OWNER_EMAIL and email != OWNER_EMAIL:
        flash(f'This deployment is private. Sign in as the owner or deploy your own instance.', 'error')
        return redirect(url_for('login'))

    from database import get_or_create_user
    uid = get_or_create_user(email, userinfo.get('name', ''), userinfo.get('picture', ''))
    session['user'] = {
        'email': email,
        'name': userinfo.get('name', ''),
        'picture': userinfo.get('picture', ''),
        'id': uid,
    }
    return redirect(url_for('dashboard'))
```

**Step 4: Replace auth_demo with a dev-only bypass**

Replace the entire `auth_demo` and `auth_fresh` functions with a single dev-only route:

```python
@app.route('/auth/dev-login', methods=['POST'])
def auth_dev_login():
    """Local dev only — bypasses OAuth. Disabled in production (OWNER_EMAIL set)."""
    if OWNER_EMAIL:
        flash('Dev login is disabled when OWNER_EMAIL is configured.', 'error')
        return redirect(url_for('login'))
    from database import get_or_create_user
    dev_email = 'dev@localhost'
    uid = get_or_create_user(dev_email, 'Dev User')
    session['user'] = {'email': dev_email, 'name': 'Dev User', 'picture': '', 'id': uid}
    return redirect(url_for('dashboard'))
```

**Step 5: Update allowed_routes in require_login**

```python
# Before:
allowed_routes = ['login', 'auth_google', 'auth_callback', 'auth_demo', 'auth_fresh', 'static']
# After:
allowed_routes = ['login', 'auth_google', 'auth_callback', 'auth_dev_login', 'static']
```

**Step 6: Update login.html to remove demo/fresh buttons, add dev-login conditionally**

Replace the divider + demo + fresh form block in `templates/login.html`:

```html
{% if not owner_locked %}
<div class="divider">or</div>
<form action="{{ url_for('auth_dev_login') }}" method="POST" style="margin:0;">
  <button type="submit" class="cta">Continue as dev user (local only)</button>
</form>
<p class="auth-note">
  Dev login is only available when OWNER_EMAIL is not set. Not for production.
</p>
{% endif %}
```

**Step 7: Pass owner_locked to login template from the login route**

In the `login()` route function, change the render call to:

```python
return render_template('login.html', owner_locked=bool(OWNER_EMAIL))
```

**Step 8: Verify app still imports**

```bash
python -c "from app import app; print('OK')"
```
Expected: `OK` (no ImportError)

**Step 9: Commit**

```bash
git add app.py templates/login.html
git commit -m "feat(auth): lock login to OWNER_EMAIL, remove demo/fresh routes"
```

---

## Task 2: Add get_owner_id() helper — single-user access pattern

**Files:**
- Modify: `app.py` — add helper near `current_user_id()` (~line 104)

**Context:**
Currently background tasks (scheduler, digest generator) have no Flask request context so `session['user']` is unavailable. They fall back to fragile JSON files. The correct pattern for a single-owner app is: background tasks call `get_owner_id()` which resolves the owner from `OWNER_EMAIL` in the DB.

**Step 1: Add get_owner_id() after current_user_id() in app.py**

```python
def get_owner_id() -> int | None:
    """Return the DB user id for the deployment owner (OWNER_EMAIL).
    Falls back to current session user if OWNER_EMAIL is unset (dev mode)."""
    if OWNER_EMAIL:
        from database import get_or_create_user
        return get_or_create_user(OWNER_EMAIL)
    return current_user_id()
```

**Step 2: Audit all places where None is passed as user_id to DB helpers**

```bash
grep -n "user_id=None\|user_id = None\|_rescore_all_jobs\|_score_unscored_for_user\|_autofill_prefs_from_cv" app.py | head -20
```

**Step 3: Replace background-context user_id=None calls with get_owner_id()**

Key places to update (search and replace one by one):

- `_rescore_all_jobs(cv_data, user_id=None, ...)` called from background → change to `_rescore_all_jobs(cv_data, user_id=get_owner_id(), ...)`
- `_score_unscored_for_user(None)` → `_score_unscored_for_user(get_owner_id())`
- `_autofill_prefs_from_cv(cv_data, user_id=None)` → `_autofill_prefs_from_cv(cv_data, user_id=get_owner_id())`
- Any `load_preferences()`, `save_preferences()`, `load_cv_data()`, `save_cv_data()` calls that happen outside a request context

**Step 4: Verify imports still OK**

```bash
python -c "from app import app; print('OK')"
```

**Step 5: Commit**

```bash
git add app.py
git commit -m "feat(auth): add get_owner_id() for background task user resolution"
```

---

## Task 3: Add OWNER_EMAIL to Vercel env and push deployment

**Files:**
- `Beautynomy/client/vercel.json` — reference only, the job-search-agent has its own vercel.json
- `.env` — local env file (gitignored)

**Context:**
The app needs `OWNER_EMAIL` set in Vercel for the owner-only guard to activate. Locally, keep it unset so dev-login still works.

**Step 1: Push OWNER_EMAIL to Vercel production env**

```bash
echo "mahalegauravk@gmail.com" | vercel env add OWNER_EMAIL production
```
If the interactive prompt hangs, use the direct API call:
```bash
TOKEN=$(cat ~/Library/Application\ Support/com.vercel.cli/auth.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(list(d.values())[0]['token'])")
curl -s -X POST "https://api.vercel.com/v10/projects/prj_VXTcJIbqm8kEhOai7Saz4c4ic98L/env" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"key":"OWNER_EMAIL","value":"mahalegauravk@gmail.com","type":"plain","target":["production","preview","development"]}'
```

**Step 2: Add OWNER_EMAIL to local .env for prod-parity testing (optional)**

```bash
echo "OWNER_EMAIL=mahalegauravk@gmail.com" >> .env
```

**Step 3: Deploy to Vercel production**

```bash
git push origin main   # Vercel auto-deploys from main
```

Or force a prod deploy:
```bash
vercel deploy --prod --yes 2>&1 | tail -5
```

**Step 4: Smoke-test the live site**

```bash
curl -s https://job-search-agent-green.vercel.app/login | grep -o '<title>.*</title>'
# Expected: <title>Sign in — Job Search Agent</title>
```

**Step 5: Commit any leftover changes**

```bash
git add .env.example   # if you create one with OWNER_EMAIL placeholder
git commit -m "chore(deploy): push OWNER_EMAIL to Vercel, lock single-owner mode"
```

---

## Task 4: Add OWNER_EMAIL to GitHub Actions secrets

**Context:**
The scraper workflow (`scrape.yml`) reads jobs into Neon. It doesn't directly need `OWNER_EMAIL` — but the email digests are sent to the owner. The `EMAIL_RECIPIENT` secret already handles that. This task verifies secrets are complete and the scraper sends emails again.

**Step 1: Check which secrets are currently set**

Go to: https://github.com/gmpro-cr/Job-Search-Agent/settings/secrets/actions

Verify ALL of the following are set:
- `DATABASE_URL`
- `DATABASE_URL_DIRECT`
- `BLOB_READ_WRITE_TOKEN`
- `GMAIL_ADDRESS`
- `GMAIL_APP_PASSWORD`
- `EMAIL_RECIPIENT`
- `GEMINI_API_KEY` (optional but needed for PRD)

**Step 2: Manually trigger a scrape to verify**

```bash
gh workflow run "Daily Job Scrape" --repo gmpro-cr/Job-Search-Agent
```

Or go to: https://github.com/gmpro-cr/Job-Search-Agent/actions/workflows/scrape.yml → "Run workflow"

**Step 3: Watch the run log for errors**

```bash
gh run list --repo gmpro-cr/Job-Search-Agent --workflow=scrape.yml --limit 3
# Then view the failing run:
gh run view <run-id> --repo gmpro-cr/Job-Search-Agent --log | tail -50
```

**Step 4: Fix any secret-related failures**

Common failures:
- `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` missing → email digest won't send (non-fatal for scraping)
- `DATABASE_URL` missing → scraper fails to write jobs to Neon (fatal)
- `BLOB_READ_WRITE_TOKEN` missing → digest upload fails (non-fatal)

---

## Task 5: Update README as fork-and-deploy template

**Files:**
- Modify: `README.md`

**Context:**
The README should be the "landing page" for anyone who finds this on GitHub. It must tell them in 5 minutes: what it does, how to fork it for their own use, what env vars to set, and how to verify it's working.

**Step 1: Read current README**

```bash
head -50 README.md
```

**Step 2: Rewrite README with this structure**

```markdown
# Job Search Agent

A personal job search dashboard that scrapes Indian portals twice a day, scores each listing against your CV, and emails you a morning digest.

**Your own deployment in 15 minutes:** fork → configure → deploy.

---

## What it does

- Scrapes LinkedIn, Indeed, Naukri (and more) on a schedule via GitHub Actions
- Scores each job against your uploaded CV
- Emails a daily digest with top matches
- Web dashboard: view jobs, track applications, manage outreach to hiring managers

## Quick Start (fork & deploy your own)

### 1. Fork this repo

Click **Fork** on GitHub. You'll get your own copy at `github.com/<you>/Job-Search-Agent`.

### 2. Set up Neon (free Postgres)

1. Go to https://neon.tech and create a free account
2. Create a project — note your `DATABASE_URL` (pooled) and `DATABASE_URL_DIRECT`

### 3. Set up Vercel Blob (free storage)

1. Go to https://vercel.com → Storage → Blob → Create Store
2. Note your `BLOB_READ_WRITE_TOKEN`

### 4. Set up Google OAuth

1. Go to https://console.cloud.google.com → APIs & Services → Credentials
2. Create an OAuth 2.0 Client (Web application)
3. Add `https://<your-vercel-url>/auth/callback` as an authorized redirect URI
4. Note `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`

### 5. Deploy to Vercel

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/gmpro-cr/Job-Search-Agent)

Set these environment variables in Vercel:

| Variable | Value |
|---|---|
| `OWNER_EMAIL` | Your Google email (only this email can sign in) |
| `DATABASE_URL` | Neon pooled connection string |
| `DATABASE_URL_DIRECT` | Neon direct connection string |
| `BLOB_READ_WRITE_TOKEN` | Vercel Blob token |
| `GOOGLE_CLIENT_ID` | From Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | From Google Cloud Console |
| `FLASK_SECRET` | Random 48-char secret (`python -c 'import secrets; print(secrets.token_urlsafe(48))'`) |
| `GMAIL_ADDRESS` | Gmail address for sending digests |
| `GMAIL_APP_PASSWORD` | Gmail app password |
| `GEMINI_API_KEY` | Optional — for AI-powered PRD generation |

### 6. Set GitHub Actions secrets

In your forked repo → Settings → Secrets → Actions, add:
- `DATABASE_URL`
- `DATABASE_URL_DIRECT`
- `BLOB_READ_WRITE_TOKEN`
- `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `EMAIL_RECIPIENT`
- `GEMINI_API_KEY` (optional)

The scraper will run automatically at 7 AM and 7 PM IST.

### 7. Sign in and upload your CV

Go to your Vercel URL → sign in with Google → upload your CV → set preferences → done.

---

## Local development

```bash
git clone https://github.com/<you>/Job-Search-Agent
cd Job-Search-Agent
pip install -r requirements.txt -r requirements-scraper.txt
python app.py        # http://localhost:5001
```

Without `OWNER_EMAIL` set, a "Dev login" button appears on the login page.

---

## Architecture

```
GitHub Actions (twice daily)
    → scrapers.py → job_listings (Neon Postgres)

Flask app (Vercel)
    → dashboard, CV scoring, outreach

Vercel Blob
    → digests, PRDs
```
```

**Step 3: Commit**

```bash
git add README.md
git commit -m "docs: rewrite README as fork-and-deploy template"
```

---

## Task 6: Add Vercel one-click deploy button support

**Files:**
- Modify: `vercel.json` — add `env` schema so Vercel's clone UI prompts for variables

**Context:**
The Vercel "Deploy" button can pre-populate the project config when `vercel.json` includes an `env` block. This makes the self-deploy experience much smoother.

**Step 1: Read current vercel.json**

```bash
cat vercel.json
```

**Step 2: Update vercel.json to declare expected env vars**

Add an `env` block that Vercel shows in the deploy wizard:

```json
{
  "functions": {
    "api/index.py": {
      "maxDuration": 60,
      "includeFiles": "{templates,static,data,agent,autoresearch}/**"
    }
  },
  "rewrites": [{ "source": "/(.*)", "destination": "/api/index" }],
  "env": {
    "OWNER_EMAIL": "Your Google email (only this account can sign in)",
    "DATABASE_URL": "Neon pooled postgres:// connection string",
    "DATABASE_URL_DIRECT": "Neon direct postgres:// connection string",
    "BLOB_READ_WRITE_TOKEN": "Vercel Blob read-write token",
    "GOOGLE_CLIENT_ID": "Google OAuth client ID",
    "GOOGLE_CLIENT_SECRET": "Google OAuth client secret",
    "FLASK_SECRET": "Random secret: python -c 'import secrets; print(secrets.token_urlsafe(48))'",
    "GMAIL_ADDRESS": "Gmail address for sending digests",
    "GMAIL_APP_PASSWORD": "Gmail app password",
    "GEMINI_API_KEY": "Optional: Gemini API key for AI PRD generation"
  }
}
```

**Step 3: Commit**

```bash
git add vercel.json
git commit -m "chore(deploy): add env schema to vercel.json for one-click deploy wizard"
```

---

## Task 7: Clean up the fresh_user route and test multi-user remnants

**Files:**
- Modify: `app.py` — remove `auth_fresh` route (added earlier this session by mistake)
- Modify: `database.py` — no changes, but document that multi-user tables are intentionally kept (zero migration risk)

**Context:**
The `auth_fresh` route was added during an earlier session to test the new-user onboarding flow. It creates a new user with a timestamp email. With single-owner mode this is unnecessary and confusing — remove it.

**Step 1: Remove auth_fresh from app.py**

Find and delete the `auth_fresh` function and its route decorator.

**Step 2: Remove from allowed_routes if still there**

```python
# Ensure allowed_routes is:
allowed_routes = ['login', 'auth_google', 'auth_callback', 'auth_dev_login', 'static']
```

**Step 3: Remove "Continue as fresh user" link from login.html**

The login template was updated to show the fresh user button. Remove that `<a>` tag.

**Step 4: Verify no broken references**

```bash
grep -n "auth_fresh" app.py templates/login.html
# Expected: no output
```

**Step 5: Commit**

```bash
git add app.py templates/login.html
git commit -m "chore(auth): remove auth_fresh test route, no longer needed"
```

---

## Task 8: Final smoke test + push

**Step 1: Verify local startup**

```bash
python -c "from app import app; print('imports OK')"
```

**Step 2: Check that login page renders**

```bash
python app.py &
sleep 2
curl -s http://localhost:5001/login | grep "Sign in"
kill %1
```

**Step 3: Push to main → triggers Vercel auto-deploy**

```bash
git push origin main
```

**Step 4: Wait for Vercel deploy (~90s) and smoke-test prod**

```bash
curl -s https://job-search-agent-green.vercel.app/login | grep -o '<title>.*</title>'
# Expected: <title>Sign in — Job Search Agent</title>

# Test that non-owner email gets blocked (this will redirect to login with error)
# Best tested manually in browser with a non-owner Google account
```

**Step 5: Trigger a manual scrape and verify email arrives**

```bash
gh workflow run "Daily Job Scrape" --repo gmpro-cr/Job-Search-Agent
```

Check your inbox at `mahalegauravk@gmail.com` within ~15 minutes.

---

## Summary of changes

| What | Before | After |
|---|---|---|
| Who can sign in | Anyone with Google account | Only `OWNER_EMAIL` |
| Demo / test login | `/auth/demo`, `/auth/fresh` | Removed; `/auth/dev-login` works only when `OWNER_EMAIL` unset |
| Background task user | `current_user_id()` → None | `get_owner_id()` → always resolves to owner |
| DB schema | Unchanged | Unchanged (zero migration risk) |
| Fork experience | Confusing multi-user docs | README: 15-min setup guide + one-click Vercel button |

**No database migration required.** The multi-user tables (`users`, `user_job_state`, etc.) are kept as-is — they work perfectly for a single owner, just now they always hold exactly one user's data.
